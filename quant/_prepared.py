"""Immutable, time-major simulator input shared by portfolio engines."""

from dataclasses import replace
from contextlib import contextmanager
from pathlib import Path
import os
import shutil
import tempfile
from time import monotonic, sleep

import numpy as np

from marketdata.model import load_json, sha256, write_json
from marketdata.state import writer_lock
from simulator.feed import FIELDS

from .data import fingerprint
from .features import cache_key


def _identity(selection, feed):
    source = fingerprint(replace(selection, fields=tuple(FIELDS)))
    legacy_aux = []
    if feed.aux and not (feed.aux_root / "inventory.json").exists():
        legacy_aux = [
            (p.relative_to(feed.aux_root).as_posix(), sha256(p))
            for p in sorted(feed.aux_root.rglob("*"))
            if p.is_file()
        ]
    implementation = sha256(
        Path(__file__).resolve().parents[1] / "simulator" / "feed.py"
    )
    return cache_key(
        function="prepared_market",
        version=implementation,
        fingerprint=source,
        parameters=[feed.aux, feed.mark_staleness_ms],
        missing_policy="native coverage/status and historical settlement rules",
        configuration=[feed.symbols, feed.step, feed.lag, legacy_aux],
    )


def _bytes(feed):
    rows = (feed.end - feed.start) // feed.step
    return rows * len(feed.symbols) * 33 + feed.settlements.nbytes + (32 << 10)


def _read(folder, feed):
    meta = load_json(folder / "receipt.json")
    if meta["key"] != folder.name:
        raise ValueError("Prepared market cache receipt identity does not match")
    rows = (feed.end - feed.start) // feed.step
    market = np.load(folder / "market.npy", mmap_mode="r", allow_pickle=False)
    flags = np.load(folder / "status.npy", mmap_mode="r", allow_pickle=False)
    events = np.load(folder / "funding.npy", mmap_mode="r", allow_pickle=False)
    if (
        market.shape != (rows, len(feed.symbols), 4)
        or flags.shape != market.shape[:2]
        or market.dtype != np.dtype("=f8")
        or flags.dtype != np.dtype("u1")
        or events.dtype != feed.settlements.dtype
        or events.ndim != 1
    ):
        raise ValueError("Prepared market cache schema does not match its receipt")
    feed.settlements = events
    feed.cursor = 0
    feed.status_counts[:] = meta["status_counts"]
    feed.mark_missing_slots[:] = meta["mark_missing_slots"]
    feed.funding_warnings = meta["funding_warnings"]
    return market, flags


@contextmanager
def _writer(root, cancel):
    deadline = monotonic() + 600
    while True:
        if cancel is not None and cancel.is_set():
            yield False
            return
        lock = writer_lock(root)
        try:
            lock.__enter__()
        except RuntimeError:
            if monotonic() >= deadline:
                raise
            sleep(0.1)
        else:
            break
    try:
        yield True
    finally:
        lock.__exit__(None, None, None)


def prepared(selection, feed, directory, budget, counters, cancel=None):
    if directory is None:
        yield from feed.blocks(shared=True)
        return
    root = Path(directory) / "prepared-v1"
    root.mkdir(parents=True, exist_ok=True)
    key = _identity(selection, feed)
    published = root / key
    built = False
    if not published.is_dir():
        with _writer(root, cancel) as acquired:
            if not acquired:
                return
            if not published.is_dir():
                for abandoned in root.glob(".pending-*"):
                    if (
                        abandoned.is_dir()
                        and abandoned.resolve().parent == root.resolve()
                    ):
                        shutil.rmtree(abandoned)
                used = sum(p.stat().st_size for p in root.rglob("*") if p.is_file())
                estimate = _bytes(feed)
                counters["cache_estimated_bytes"] = estimate
                if estimate + used > budget or estimate > shutil.disk_usage(root).free:
                    raise MemoryError(
                        "Prepared cache exceeds the available disk budget"
                    )
                rows = (feed.end - feed.start) // feed.step
                with tempfile.TemporaryDirectory(dir=root, prefix=".pending-") as stage:
                    folder = Path(stage)
                    market = np.lib.format.open_memmap(
                        folder / "market.npy",
                        mode="w+",
                        dtype="=f8",
                        shape=(rows, len(feed.symbols), 4),
                    )
                    flags = np.lib.format.open_memmap(
                        folder / "status.npy",
                        mode="w+",
                        dtype="u1",
                        shape=(rows, len(feed.symbols)),
                    )
                    cursor = 0
                    try:
                        for starts, values, status in feed.blocks(shared=True):
                            if cancel is not None and cancel.is_set():
                                return
                            next_row = cursor + len(starts)
                            market[cursor:next_row], flags[cursor:next_row] = (
                                values,
                                status,
                            )
                            cursor = next_row
                        market.flush()
                        flags.flush()
                    finally:
                        del market, flags
                    if cursor != rows:
                        raise ValueError(
                            "Prepared data does not cover the selected grid"
                        )
                    np.save(
                        folder / "funding.npy", feed.settlements, allow_pickle=False
                    )
                    write_json(
                        folder / "receipt.json",
                        {
                            "key": key,
                            "status_counts": feed.status_counts.tolist(),
                            "mark_missing_slots": feed.mark_missing_slots.tolist(),
                            "funding_warnings": feed.funding_warnings,
                        },
                    )
                    for artifact in folder.iterdir():
                        with artifact.open("r+b") as handle:
                            os.fsync(handle.fileno())
                    if sum(p.stat().st_size for p in folder.iterdir()) + used > budget:
                        raise MemoryError(
                            "Prepared cache exceeded its reserved disk budget"
                        )
                    folder.rename(published)
                    built = True
                    if os.name != "nt":
                        fd = os.open(root, os.O_DIRECTORY)
                        try:
                            os.fsync(fd)
                        finally:
                            os.close(fd)
                    counters["cache_builds"] = counters.get("cache_builds", 0) + 1
    market, flags = _read(published, feed)
    counters["cache_reads"] = counters.get("cache_reads", 0) + 1
    counters["cache_hits"] = counters.get("cache_hits", 0) + (not built)
    for lo in range(0, len(market), feed.block_steps):
        hi = min(lo + feed.block_steps, len(market))
        starts = feed.start + np.arange(lo, hi, dtype="i8") * feed.step
        yield starts, market[lo:hi], flags[lo:hi]
