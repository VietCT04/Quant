from __future__ import annotations

import asyncio
import email.utils
import random
import re
import shutil
import time
from dataclasses import dataclass
from pathlib import Path

import aiohttp

from .model import Archive, replace_file, sha256


class MissingArchive(Exception):
    pass


class IntegrityError(Exception):
    pass


class PermanentHTTP(Exception):
    pass


class RetryableHTTP(Exception):
    pass


@dataclass
class Metrics:
    input_bytes: int = 0
    rows: int = 0
    bars: int = 0
    output_bytes: int = 0
    retries: int = 0
    downloading: int = 0
    processing: int = 0
    archives: int = 0
    failed_archives: int = 0
    temp_peak: int = 0
    memory_peak: int = 0
    download_seconds: float = 0.0
    processing_seconds: float = 0.0
    ended: int = 0
    partitions: int = 0


class HTTP:
    def __init__(self, session, metrics, rate=20, retries=5):
        self.session = session
        self.metrics = metrics
        self.rate = rate
        self.retries = retries
        self.next_start = self.cooldown = 0.0
        self.lock = asyncio.Lock()

    async def pace(self):
        async with self.lock:
            while (wait := max(self.next_start, self.cooldown) - time.monotonic()) > 0:
                await asyncio.sleep(wait)
            self.next_start = time.monotonic() + 1 / self.rate

    def check(self, response, accepted=(200,)):
        status = response.status
        if status in accepted:
            return
        if status in (404, 410):
            raise MissingArchive(str(response.url))
        if status in (429, 503) or 500 <= status <= 599:
            retry = response.headers.get("Retry-After")
            if retry:
                try:
                    seconds = float(retry)
                except ValueError:
                    seconds = email.utils.parsedate_to_datetime(retry).timestamp() - time.time()
            else:
                seconds = 4 if status == 429 else 0
            self.cooldown = max(self.cooldown, time.monotonic() + max(0, seconds))
            raise RetryableHTTP(f"HTTP {status} at {response.url}")
        raise PermanentHTTP(f"HTTP {status} at {response.url}")

    async def retry(self, operation):
        for attempt in range(self.retries + 1):
            try:
                await self.pace()
                return await operation()
            except (aiohttp.ClientConnectionError, aiohttp.ClientPayloadError,
                    TimeoutError, RetryableHTTP) as exc:
                if attempt == self.retries:
                    raise RuntimeError(f"Network retries exhausted: {exc}") from exc
                self.metrics.retries += 1
                await asyncio.sleep(min(60, 2 ** attempt) * random.uniform(.75, 1.25))

    async def body(self, url, *, params=None, json=False):
        async def get():
            async with self.session.get(url, params=params) as response:
                self.check(response)
                return await (response.json(content_type=None) if json else response.read())
        return await self.retry(get)

    async def size(self, url):
        async def head():
            async with self.session.head(url) as response:
                self.check(response)
                if response.content_length is None:
                    raise IntegrityError(f"Missing archive Content-Length: {url}")
                return response.content_length
        return await self.retry(head)


@dataclass(frozen=True)
class Downloaded:
    archive: Archive
    path: Path
    checksum: str
    size: int


class RawBudget:
    def __init__(self, root: Path, budget: int, metrics: Metrics):
        self.root, self.budget, self.metrics = root, budget, metrics
        root.mkdir(parents=True, exist_ok=True)
        self.sizes = {p: p.stat().st_size for p in root.iterdir()
                      if p.is_file() and (p.name.endswith(".zip") or p.name.endswith(".zip.part"))}
        self.active = set()
        self.peak()
        self.changed = asyncio.Condition()

    def peak(self):
        self.metrics.temp_peak = max(self.metrics.temp_peak, sum(self.sizes.values()))

    async def reserve(self, path: Path, size: int):
        part = path.with_name(path.name + ".part")
        async with self.changed:
            while True:
                old = self.sizes.get(path, 0) + self.sizes.get(part, 0)
                required = max(0, size - old)
                if size > self.budget:
                    raise OSError(f"{path.name} requires {size:,} bytes; raw budget is {self.budget:,}")
                if sum(self.sizes.values()) + required <= self.budget:
                    if shutil.disk_usage(self.root).free < required:
                        raise OSError(f"Insufficient disk space for {path.name} ({required:,} bytes)")
                    self.sizes.pop(part, None)
                    self.sizes[path] = size
                    self.active.add(path)
                    self.peak()
                    return
                if not (self.active - {path}):
                    raise OSError("Raw storage exhausted by retained files or interrupted downloads")
                await self.changed.wait()

    async def release(self, path: Path, delete: bool):
        async with self.changed:
            part = path.with_name(path.name + ".part")
            self.active.discard(path)
            self.sizes.pop(path, None)
            for file in (path, part):
                if delete:
                    file.unlink(missing_ok=True)
                elif file.exists():
                    self.sizes[file] = file.stat().st_size
            self.changed.notify_all()


class Downloader:
    def __init__(self, http: HTTP, budget: RawBudget, metrics: Metrics):
        self.http, self.budget, self.metrics = http, budget, metrics

    async def fetch(self, archive: Archive):
        path = self.budget.root / archive.filename
        part = path.with_name(path.name + ".part")
        self.metrics.downloading += 1
        start = time.perf_counter()
        try:
            check = (await self.http.body(archive.url + ".CHECKSUM")).decode("utf-8").split()
            if len(check) != 2 or not re.fullmatch("[0-9a-fA-F]{64}", check[0]) or check[1].lstrip("*") != path.name:
                raise IntegrityError(f"Unexpected CHECKSUM for {archive.url}")
            checksum = check[0].lower()
            size = path.stat().st_size if path.exists() else await self.http.size(archive.url)
            await self.budget.reserve(path, size)
            try:
                if path.exists() and await asyncio.to_thread(sha256, path) == checksum:
                    self.metrics.archives += 1
                    return Downloaded(archive, path, checksum, size)
                if path.exists():
                    path.unlink()
                    size = await self.http.size(archive.url)
                    await self.budget.reserve(path, size)
                for attempt in range(2):
                    await self.http.retry(lambda: self.transfer(archive.url, part, size))
                    if await asyncio.to_thread(sha256, part) == checksum:
                        await asyncio.to_thread(replace_file, part, path)
                        self.metrics.archives += 1
                        return Downloaded(archive, path, checksum, size)
                    part.unlink()
                    self.metrics.retries += 1
                    if attempt:
                        raise IntegrityError(f"SHA-256 mismatch for {archive.url}")
            except BaseException:
                await self.budget.release(path, False)
                raise
        except Exception:
            self.metrics.failed_archives += 1
            raise
        finally:
            self.metrics.downloading -= 1
            self.metrics.download_seconds += time.perf_counter() - start

    async def transfer(self, url: str, part: Path, size: int):
        offset = part.stat().st_size if part.exists() else 0
        if offset == size and part.exists():
            return
        if offset > size:
            part.unlink()
            offset = 0
        headers = {"Range": f"bytes={offset}-"} if offset else {}
        async with self.http.session.get(url, headers=headers) as response:
            if response.status == 416 and offset:
                part.unlink(missing_ok=True)
                raise RetryableHTTP(f"Range no longer accepted for {url}")
            self.http.check(response, (200, 206))
            if response.status == 206:
                if not offset or response.headers.get("Content-Range") != f"bytes {offset}-{size - 1}/{size}":
                    part.unlink(missing_ok=True)
                    raise RetryableHTTP(f"Unexpected range for {url}")
            else:
                offset = 0
            with part.open("ab" if offset else "wb") as f:
                async for data in response.content.iter_chunked(1024 * 1024):
                    offset += len(data)
                    if offset > size:
                        raise IntegrityError(f"Archive grew during download: {url}")
                    f.write(data)
                    self.metrics.input_bytes += len(data)
            if offset != size:
                raise RetryableHTTP(f"Incomplete archive: {url} ({offset}/{size})")


def session(workers: int):
    return aiohttp.ClientSession(
        connector=aiohttp.TCPConnector(limit=workers, limit_per_host=workers, ttl_dns_cache=300),
        timeout=aiohttp.ClientTimeout(connect=30, sock_read=60),
        auto_decompress=False, headers={"Accept-Encoding": "identity", "User-Agent": "quant-marketdata/0.1"},
        trust_env=True,
    )
