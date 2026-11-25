from __future__ import annotations

import asyncio
import hashlib
import itertools
import json
import shutil
import time
import zipfile
from dataclasses import asdict, dataclass, replace
from datetime import date
from pathlib import Path
from contextlib import nullcontext

import numpy as np
import polars as pl
import psutil
import pyarrow as pa
import pyarrow.parquet as pq
from rich.live import Live
from rich.table import Table

from .aggregate import (
    CrossCheckError, arrow_bars, fill_bars, gap_boundaries, klines, mask_minutes, quarantine_unobserved_minutes,
    reconcile, sparse_bars,
    validate_bars, writer,
)
from .model import DAY, MINUTE, Archive, dates, millis, next_month, partition_path, replace_file, sha256, today, utc_day
from .network import Downloader, HTTP, IntegrityError, Metrics, MissingArchive, RawBudget, session
from .state import output_present


class InvalidSourceArchive(ValueError):
    pass


@dataclass
class Config:
    workers: int = 16
    processors: int = 3
    memory: int = 4 * 1024 ** 3
    raw_budget: int = 8 * 1024 ** 3
    temp: Path | None = None
    keep_raw: bool = False
    display: bool = True
    rate: int = 20
    retry_missing: bool = False


class QueuePool:
    def __init__(self, workers, capacity):
        self.workers = workers
        self.queue = asyncio.Queue(capacity)

    async def __aenter__(self):
        self.tasks = [asyncio.create_task(self.run()) for _ in range(self.workers)]
        return self

    async def __aexit__(self, *exc):
        for task in self.tasks:
            task.cancel()
        await asyncio.gather(*self.tasks, return_exceptions=True)

    async def run(self):
        while True:
            func, future = await self.queue.get()
            try:
                result = await func()
            except Exception as exc:
                if not future.done():
                    future.set_exception(exc)
            else:
                if not future.done():
                    future.set_result(result)
            finally:
                self.queue.task_done()

    async def submit(self, func):
        future = asyncio.get_running_loop().create_future()
        await self.queue.put((func, future))
        return await future


async def read_reference(http, spec):
    url = spec.url
    check = (await http.body(url + ".CHECKSUM")).decode("utf-8").split()
    if len(check) != 2 or check[1].lstrip("*") != spec.filename:
        raise IntegrityError(f"Malformed kline checksum for {url}")
    data = await http.body(url)
    if len(data) > 16 * 1024 ** 2:
        raise IntegrityError(f"Unexpected 1-minute ZIP size for {url}")
    checksum = hashlib.sha256(data).hexdigest()
    if checksum != check[0].lower():
        raise IntegrityError(f"SHA-256 mismatch for {url}")
    http.metrics.input_bytes += len(data)
    http.metrics.archives += 1
    return data, checksum


def coverage_rows(frame, start, end, interval):
    if not frame.height:
        return [[start, end, "no_verified_trading"]]
    times = frame["timestamp_ms"].to_numpy()
    bounds = np.flatnonzero(np.diff(times) != interval) + 1
    cuts = [0, *bounds.tolist(), len(times)]
    result = []
    lo = start
    for left, right in itertools.pairwise(cuts):
        begin, stop = int(times[left]), int(times[right - 1]) + interval
        if lo < begin:
            result.append([lo, begin, "no_verified_trading"])
        result.append([begin, stop, "covered"])
        lo = stop
    if lo < end:
        result.append([lo, end, "no_verified_trading"])
    return result


def label_source_gaps(segments, gaps):
    for lo, hi in gaps:
        revised = []
        for left, right, reason in segments:
            if right <= lo or hi <= left:
                revised.append([left, right, reason])
                continue
            if reason == "covered":
                raise ValueError("Coverage intersects a quarantined interval")
            middle_start, middle_end = max(left, lo), min(right, hi)
            if left < middle_start:
                revised.append([left, middle_start, reason])
            revised.append([middle_start, middle_end, "quarantined_source_conflict"])
            if middle_end < right:
                revised.append([middle_end, right, reason])
        segments = revised
    return segments


def build_source(download, reference, reference_hash, start, end, listing, interval, seed, dest, temp, spill):
    begin, stop = millis(download.archive.start), millis(download.archive.end)
    clock = time.perf_counter()
    try:
        sparse, info = sparse_bars(download.path, begin, stop, start, end, listing, interval, 8 * 1024 ** 2,
                                   temp, spill)
        refs = klines(reference, begin, stop, start, end)
    except (ValueError, zipfile.BadZipFile) as exc:
        raise InvalidSourceArchive(str(exc)) from exc
    unmasked = sparse
    verified_start = max(start, (listing + MINUTE - 1) // MINUTE * MINUTE)
    validation = reconcile(sparse, refs, verified_start, end)
    original = validation
    gaps = []
    if validation["daily_outliers"] or validation["missing_agg_minutes"]:
        sparse, refs, gaps = quarantine_unobserved_minutes(sparse, refs, verified_start, end, validation)
        if gaps:
            validation = reconcile(sparse, refs, verified_start, end)
    source_ok = len(info.id_gap_timestamps_ms) == info.agg_id_gaps
    if source_ok and info.agg_id_gaps:
        id_gaps = [(max(start, min(a, b) // MINUTE * MINUTE),
                    min(end, (max(a, b) // MINUTE + 1) * MINUTE)) for a, b in info.id_gap_timestamps_ms]
        sparse, refs, gaps = mask_minutes(sparse, refs, gaps + [[lo, hi] for lo, hi in id_gaps if lo < hi])
        validation = reconcile(sparse, refs, verified_start, end)
    if download.archive.monthly and (validation["daily_outliers"] or validation["missing_ref_minutes"]):
        bad_days = {millis(date.fromisoformat(row["date"])) for row in validation["daily_outliers"]}
        bad_days.update(ts // DAY * DAY for ts in validation["missing_ref_timestamps_ms"])
        day_gaps = [[max(start, day), min(end, day + DAY)] for day in bad_days]
        sparse, refs, gaps = mask_minutes(sparse, refs, gaps + day_gaps)
        validation = reconcile(sparse, refs, verified_start, end)
    if validation["daily_outliers"] or validation["missing_ref_minutes"] or not source_ok or gaps and not sparse.height:
        raise CrossCheckError(original | {
            "agg_id_gaps": info.agg_id_gaps, "archive_sha256": download.checksum,
            "archive_bytes": download.size, "reference_sha256": reference_hash,
        })
    qualified = validation["mismatched_minutes"] > 0 or info.pre_listing_rows_excluded > 0
    missing_minutes = validation["missing_agg_timestamps_ms"]
    verified = refs.filter((pl.col("volume") > 0) & ~pl.col("timestamp_ms").is_in(missing_minutes))
    bars = fill_bars(sparse, verified, interval, start, end, listing, seed)
    validate_bars(bars, interval, start, end, listing)
    segments = label_source_gaps(coverage_rows(bars, start, end, interval), gaps)
    boundaries = gap_boundaries(unmasked, gaps) if gaps and download.archive.monthly else []
    last = bars.row(-1, named=True) if bars.height else None
    if bars.height:
        dest.parent.mkdir(parents=True, exist_ok=True)
        staging = dest.with_suffix(".part")
        with writer(staging, interval) as sink:
            sink.write_table(arrow_bars(bars, interval), row_group_size=65_536)
        with pq.ParquetFile(staging) as actual:
            if actual.metadata.num_rows != bars.height or not actual.schema_arrow.equals(arrow_bars(bars.head(0), interval).schema):
                raise ValueError(f"Output Parquet verification failed: {dest}")
        replace_file(staging, dest)
    return {
        "archive_url": download.archive.url, "checksum": download.checksum, "zip_bytes": download.size,
        "reference_url": replace(download.archive, kline=True).url, "reference_sha256": reference_hash,
        "validation": "qualified_kline_differences" if qualified else "exact_kline_match",
        "crosscheck": validation, "bars": bars.height, "empty_bars": bars["is_empty"].sum(),
        "crosscheck_before_quarantine": original if gaps else None, "source_gaps": gaps,
        "gap_id_boundaries": boundaries,
        "segments": segments, "input_seed": seed, "end_close": (
            last["close"] if last and last["timestamp_ms"] + interval == end else None
        ),
        "trades": asdict(info), "aggregation_seconds": time.perf_counter() - clock,
        "first_ms": int(bars["timestamp_ms"][0]) if bars.height else None,
        "last_ms": int(bars["timestamp_ms"][-1]) if bars.height else None,
    }


def compact(paths, destination, interval, start, end, listing, expected):
    destination.parent.mkdir(parents=True, exist_ok=True)
    staged = destination.with_suffix(".part")
    last = None
    pending, count, rows = [], 0, 0
    with writer(staged, interval) as sink:
        for path, clip_start, clip_end in paths:
            with pq.ParquetFile(path) as part:
                for batch in part.iter_batches(batch_size=65_536):
                    frame = pl.from_arrow(batch)
                    if clip_start is not None:
                        frame = frame.filter(pl.col("timestamp_ms").is_between(clip_start, clip_end, closed="left"))
                    if not frame.height:
                        continue
                    validate_bars(frame, interval, start, end, listing)
                    first = frame["timestamp_ms"][0]
                    if (last is not None and first <= last["timestamp_ms"]
                        or last is not None and frame["is_empty"][0] and first == last["timestamp_ms"] + interval
                        and frame["close"][0] != last["close"]):
                        raise ValueError("Invalid chronology or carried close across fragments")
                    last = frame.row(-1, named=True)
                    persisted = frame.to_arrow().cast(part.schema_arrow).to_batches() if clip_start is not None else [batch]
                    pending.extend(persisted)
                    count += frame.height
                    rows += frame.height
                    if count >= 65_536:
                        table = pa.Table.from_batches(pending)
                        sink.write_table(table.slice(0, 65_536), row_group_size=65_536)
                        pending = table.slice(65_536).to_batches()
                        count -= 65_536
        if count:
            sink.write_table(pa.Table.from_batches(pending), row_group_size=65_536)
    if rows != expected or pq.read_metadata(staged).num_rows != rows:
        raise ValueError("Unexpected partition row count after writing Parquet")
    digest = sha256(staged)
    replace_file(staged, destination)
    return {"bars": rows, "bytes": destination.stat().st_size, "output_sha256": digest}


class Acquisition:
    def __init__(self, manifest, config):
        self.state, self.config = manifest, config
        self.root, self.plan = manifest.root, manifest.plan()
        self.interval = self.plan["interval_ms"]
        tag = hashlib.sha256(str(self.root.resolve()).encode()).hexdigest()[:12]
        self.temp = config.temp / tag if config.temp else self.root / "temp"
        self.temp.mkdir(parents=True, exist_ok=True)
        self.stage = self.root / "metadata" / "staging"
        self.metrics = Metrics()
        worker_memory = (512 + 256 * 5000 // self.interval) * 1024 ** 2
        if config.memory < worker_memory:
            raise ValueError(f"Memory budget is too small for this interval; allow at least {worker_memory:,} bytes")
        self.processors = min(config.processors, config.memory // worker_memory)
        self.spill = max(128 * 1024 ** 2, config.raw_budget // (2 * self.processors))
        self.budget = RawBudget(self.temp / "raw", config.raw_budget // 2, self.metrics)
        self.start = time.perf_counter()

    async def source(self, task, spec, start, end, seed):
        key = f"{spec.filename}:{start}:{end}"
        dest = self.stage / task["symbol"] / task["month"] / (spec.filename + ".parquet")
        stored = self.state.get_fragment(task, key)
        if stored and stored["input_seed"] == seed and (not stored["bars"] or dest.exists()):
            return stored, dest

        reference, reference_hash = await read_reference(self.http, replace(spec, kline=True))
        download = await self.download_pool.submit(lambda: self.downloader.fetch(spec))
        delete = False
        try:
            async def process():
                self.metrics.processing += 1
                start_clock = time.perf_counter()
                try:
                    result = await asyncio.to_thread(
                        build_source, download, reference, reference_hash, start, end, task["listing"],
                        self.interval, seed, dest, self.temp, self.spill,
                    )
                    self.metrics.rows += result["trades"]["records"]
                    self.metrics.bars += result["bars"]
                    return result
                finally:
                    self.metrics.processing_seconds += time.perf_counter() - start_clock
                    self.metrics.processing -= 1

            result = await self.process_pool.submit(process)
            self.state.fragment(task, key, result)
            delete = not self.config.keep_raw
            return result, dest
        finally:
            await self.budget.release(download.path, delete)

    async def partition(self, task, seed, previous_ids):
        start, end = date.fromisoformat(task["start"]), date.fromisoformat(task["end"])
        s, e = millis(start), millis(end)
        symbol = task["symbol"]
        month = start.replace(day=1)
        stop = next_month(month)
        parts, sources, fallbacks = [], [], []
        # Reuse staged daily prefixes when a prior incremental run already
        # selected daily archives. They stay authoritative on extension.
        prior = json.loads(task["result"]) if task["result"] else {}
        daily_prefix = (any("/daily/" in source.get("archive_url", "") for source in prior.get("sources", []))
                        and not any(str(item.get("reason", "")).startswith("Kline reconciliation failed")
                                    for item in prior.get("fallbacks", [])))
        if stop <= today() and (end - start).days >= 14 and not daily_prefix:
            try:
                source, path = await self.source(task, Archive(symbol, month, monthly=True), s, e, seed)
                sources.append(source)
                if source["bars"]:
                    parts.append((path, None, None))
                repairs = []
                for (lo, hi), (before, after) in zip(source.get("source_gaps", []), source.get("gap_id_boundaries", [])):
                    cursor = lo
                    while cursor < hi:
                        spec = Archive(symbol, utc_day(cursor))
                        stop_patch = min(hi, millis(spec.end))
                        detail = {"daily_repair": spec.url, "start_ms": cursor, "end_ms": stop_patch}
                        try:
                            patch, patch_path = await self.source(task, spec, cursor, stop_patch, None)
                        except (CrossCheckError, MissingArchive, InvalidSourceArchive) as exc:
                            detail["reason"] = str(exc)
                        else:
                            ids = patch["trades"]
                            if (patch["bars"] and before is not None and after is not None
                                and before[0] < ids["included_min_id"] <= ids["included_max_id"] < after[0]
                                and before[1] < ids["included_first_underlying"] <= ids["included_last_underlying"] < after[1]):
                                repairs.append((cursor, stop_patch, patch_path, patch))
                                before = [ids["included_max_id"], ids["included_last_underlying"]]
                                detail["accepted"] = True
                            else:
                                detail["reason"] = "Candidate lacked non-overlapping aggregate and fill ID boundaries"
                            detail["source_sha256"] = patch["checksum"]
                        fallbacks.append(detail)
                        cursor = stop_patch
                if repairs:
                    source = dict(source)
                    sources[0] = source
                    parts, left, repaired_bars = [], s, 0
                    source["repairs"] = []
                    for lo, hi, patch_path, patch in repairs:
                        parts.extend([(path, left, lo), (patch_path, None, None)])
                        source["repairs"].append(patch)
                        source["segments"] = [
                            segment for segment in source["segments"] if segment[1] <= lo or segment[0] >= hi
                        ] + patch["segments"] + [
                            [max(hi, segment[0]), segment[1], segment[2]]
                            for segment in source["segments"] if segment[0] < hi < segment[1]
                        ] + [
                            [segment[0], min(lo, segment[1]), segment[2]]
                            for segment in source["segments"] if segment[0] < lo < segment[1]
                        ]
                        source["segments"].sort()
                        repaired_bars += patch["bars"]
                        left = hi
                    parts.append((path, left, e))
                    source["bars"] += repaired_bars
            except (MissingArchive, CrossCheckError, InvalidSourceArchive) as exc:
                fallbacks.append({"monthly": Archive(symbol, month, monthly=True).url,
                                  "reason": str(exc), "crosscheck": getattr(exc, "details", None)})
        if not sources:
            for day in dates(start, end):
                spec = Archive(symbol, day)
                try:
                    source, path = await self.source(task, spec, millis(day), millis(spec.end), seed)
                    sources.append(source)
                    seed = source["end_close"]
                    if source["bars"]:
                        parts.append((path, None, None))
                except (MissingArchive, CrossCheckError, InvalidSourceArchive) as exc:
                    sources.append({
                        "archive_url": spec.url,
                        "validation": "missing_archive" if isinstance(exc, MissingArchive) else "failed_crosscheck",
                        "segments": [[millis(day), millis(spec.end), "missing_source" if isinstance(
                            exc, MissingArchive) else "failed_validation"]],
                        "error": str(exc), "crosscheck": getattr(exc, "details", {}),
                    })
                    seed = None
        output = partition_path(self.root, symbol, task["month"], self.interval)
        bars = sum(src.get("bars", 0) for src in sources)
        for source in sources:
            ids = source.get("trades")
            if ids and ids["first_id"] is not None:
                if previous_ids is not None and (ids["first_id"] <= previous_ids["last_id"]
                        or ids["first_underlying"] <= previous_ids["last_underlying"]):
                    raise ValueError("Aggregate or underlying IDs overlap across archives")
                previous_ids = ids
        result = {"bars": 0, "bytes": 0}
        if bars:
            result = await self.process_pool.submit(lambda: asyncio.to_thread(
                compact, parts, output, self.interval, s, e, task["listing"], bars,
            ))
            self.metrics.output_bytes += result["bytes"]
        elif output.exists():
            output.unlink()
        result.update({
            "sources": sources, "fallbacks": fallbacks, "end_close": sources[-1].get("end_close"),
            "end_ids": previous_ids,
        })
        states = {src["validation"] for src in sources}
        status = ("incomplete" if states & {"missing_archive", "failed_crosscheck"} else
                  "qualified" if "qualified_kline_differences" in states or any(s.get("source_gaps") for s in sources)
                  else "complete")
        self.state.partition(task, status, result)
        if status in {"complete", "qualified"} and end == stop:
            for path, _, _ in parts:
                path.unlink(missing_ok=True)
        return result

    async def symbol(self, tasks, limit):
        async with limit:
            seed, last_end, previous_ids = None, None, None
            for task in tasks:
                if last_end != task["start"]:
                    seed = None
                status = task["status"]
                valid = output_present(self.root, task, self.interval)
                stored = json.loads(task["result"]) if task["result"] else {}
                first_source = stored.get("sources", [{}])[0]
                unchanged_seed = first_source.get("input_seed") == seed or not first_source.get("bars")
                if (valid and unchanged_seed and
                    (status in {"complete", "qualified"} or status == "incomplete" and not self.config.retry_missing)):
                    result = stored
                else:
                    try:
                        result = await self.partition(task, seed, previous_ids)
                    except Exception as exc:
                        print(f'{task["symbol"]} {task["month"]}: {exc}', flush=True)
                        result = stored | {"error": str(exc), "end_close": None}
                        self.state.partition(task, "failed", result)
                seed, last_end = result.get("end_close"), task["end"]
                previous_ids = result.get("end_ids") or previous_ids
                self.metrics.ended += 1

    async def dashboard(self):
        process = psutil.Process()
        old_bytes, old_rows, last, last_report = 0, 0, self.start, self.start
        monitor = Live(refresh_per_second=1, auto_refresh=False) if self.config.display else nullcontext()
        with monitor as live:
            while True:
                now = time.perf_counter()
                dt = max(0.001, now - last)
                m = self.metrics
                mem = process.memory_info().rss
                m.memory_peak = max(m.memory_peak, mem)
                speed, tps = (m.input_bytes - old_bytes) / dt, (m.rows - old_rows) / dt
                left = m.partitions - m.ended
                eta = (now - self.start) * left / m.ended if m.ended else None
                if self.config.display:
                    table = Table(title="Binance USD-M historical data")
                    table.add_column("Metric")
                    table.add_column("Current run")
                    table.add_row("Symbols", ", ".join(self.plan["symbols"]))
                    table.add_row("Partitions", f"{m.ended:,}/{m.partitions:,}  ({m.ended/max(1,m.partitions):.1%}), {left:,} remaining")
                    table.add_row("Archives / possible remaining", f"{m.archives:,} verified / ≤{left * 62:,} (daily fallback upper bound)")
                    table.add_row("Downloaded", f"{m.input_bytes / 2**20:,.1f} MiB @ {speed / 2**20:,.1f} MiB/s")
                    table.add_row("Aggregate trades", f"{m.rows:,} @ {tps:,.0f} trades/s")
                    table.add_row("Bars / output", f"{m.bars:,} / {m.output_bytes / 2**20:,.1f} MiB")
                    table.add_row("Raw reserved / RSS", f"{sum(self.budget.sizes.values()) / 2**20:,.1f} MiB / {mem / 2**20:,.1f} MiB")
                    table.add_row("Workers / retries / archive errors", f"{m.downloading} network, {m.processing} processing / {m.retries} / {m.failed_archives}")
                    table.add_row("ETA", f"{eta/60:,.1f} min" if eta is not None else "Measuring throughput")
                    live.update(table, refresh=True)
                if m.ended >= m.partitions:
                    return
                if now - last_report >= 60:
                    self.state.report()
                    last_report = now
                old_bytes, old_rows, last = m.input_bytes, m.rows, now
                await asyncio.sleep(.5)

    async def run(self, symbols=None):
        tasks = [task for task in self.state.partitions() if symbols is None or task["symbol"] in symbols]
        self.metrics.partitions = len(tasks)
        if not tasks:
            raise ValueError("Plan has no listed contracts within the date range.")
        limit = asyncio.Semaphore(self.config.workers + self.processors)
        async with (
            session(self.config.workers) as client,
            QueuePool(self.config.workers, self.config.workers) as dl,
            QueuePool(self.processors, self.processors * 2) as cpu,
        ):
            self.http = HTTP(client, self.metrics, self.config.rate)
            self.downloader = Downloader(self.http, self.budget, self.metrics)
            self.download_pool, self.process_pool = dl, cpu
            async with asyncio.TaskGroup() as group:
                group.create_task(self.dashboard())
                for _, symbol_tasks in itertools.groupby(tasks, lambda task: task["symbol"]):
                    group.create_task(self.symbol(list(symbol_tasks), limit))
        self.state.report()
        return asdict(self.metrics) | {
            "elapsed_seconds": time.perf_counter() - self.start,
            "raw_disk_free_bytes": shutil.disk_usage(self.temp).free,
            "output_disk_free_bytes": shutil.disk_usage(self.root).free,
            "statuses": self.state.summary(),
        }
