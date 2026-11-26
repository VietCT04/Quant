"""Immutable, time-major simulator input shared by portfolio engines."""

from dataclasses import replace
from contextlib import contextmanager, ExitStack
from pathlib import Path
import os
import shutil
import tempfile
from time import monotonic, sleep

import numpy as np

from marketdata.model import load_json, sha256, write_json
from marketdata.state import writer_lock
from simulator.feed import FIELDS
from simulator.native import SETTLEMENT

from .data import fingerprint
from .features import cache_key


def _identity(selection, feed, projected=()):
    source = fingerprint(replace(selection, start=feed.start,
                                 fields=tuple(dict.fromkeys([*FIELDS, *projected]))))
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
        configuration=[feed.symbols, feed.step, feed.lag, legacy_aux,
                       *([feed.completed_clock, feed.trading_start, projected] if projected or feed.completed_clock else [])],
    )


def _bytes(feed, projected=()):
    rows = (feed.end - feed.start) // feed.step
    return rows * len(feed.symbols) * (33 + 8 * len(projected)) + feed.settlements.nbytes + (32 << 10)


def _read(folder, feed, projected=()):
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
        or events.dtype != SETTLEMENT
        or events.ndim != 1
    ):
        raise ValueError("Prepared market cache schema does not match its receipt")
    extras = [np.load(folder / f"{name}.npy", mmap_mode="r", allow_pickle=False) for name in projected]
    if any(array.shape != flags.shape or array.dtype != np.dtype("=f8") for array in extras):
        raise ValueError("Prepared market projection does not match its receipt")
    feed.use_funding(events, meta["funding_warnings"])
    feed.status_counts[:] = meta["status_counts"]
    feed.mark_missing_slots[:] = meta["mark_missing_slots"]
    return tuple(
        (Path(value.filename), value.dtype, value.shape, value.offset)
        for value in (market, flags, *extras)
    )


def _header(handle, shape, dtype):
    np.lib.format.write_array_header_2_0(
        handle,
        {
            "shape": shape,
            "fortran_order": False,
            "descr": np.lib.format.dtype_to_descr(np.dtype(dtype)),
        },
    )


def _window(description, lo, hi):
    path, dtype, shape, offset = description
    width = int(np.prod(shape[1:])) * dtype.itemsize
    return np.memmap(
        path,
        dtype=dtype,
        mode="r",
        offset=offset + lo * width,
        shape=(hi - lo, *shape[1:]),
    )


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


def prepared(selection, feed, directory, budget, counters, cancel=None, *, projected=()):
    if directory is None:
        yield from feed.blocks(shared=True, projected=projected)
        return
    root = Path(directory) / "prepared-v1"
    root.mkdir(parents=True, exist_ok=True)
    key = _identity(selection, feed, projected)
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
                estimate = _bytes(feed, projected)
                counters["cache_estimated_bytes"] = estimate
                if estimate + used > budget or estimate > shutil.disk_usage(root).free:
                    raise MemoryError(
                        "Prepared cache exceeds the available disk budget"
                    )
                rows = (feed.end - feed.start) // feed.step
                with tempfile.TemporaryDirectory(dir=root, prefix=".pending-") as stage:
                    folder = Path(stage)
                    cursor = 0
                    with ExitStack() as stack:
                        def create(name, shape, dtype):
                            target = stack.enter_context((folder / f"{name}.npy").open("wb", buffering=0))
                            _header(target, shape, dtype)
                            return target

                        axes = (rows, len(feed.symbols))
                        market = create("market", (*axes, 4), "=f8")
                        flags = create("status", axes, "u1")
                        outputs = {name: create(name, axes, "=f8") for name in projected}
                        for batch in feed.blocks(shared=True, projected=projected):
                            starts, values, status = batch[:3]
                            if cancel is not None and cancel.is_set():
                                return
                            if cursor + len(starts) > rows:
                                raise ValueError("Prepared data exceeds the selected grid")
                            values.tofile(market)
                            status.tofile(flags)
                            for name, file in outputs.items():
                                batch[3][name].tofile(file)
                            cursor += len(starts)
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
    market, flags, *extras = _read(published, feed, projected)
    counters["cache_reads"] = counters.get("cache_reads", 0) + 1
    counters["cache_hits"] = counters.get("cache_hits", 0) + (not built)
    rows = market[2][0]
    # Keep the mapped working set independent of total history, preserving the feed's block edges.
    block_bytes = feed.block_steps * len(feed.symbols) * (33 + 8 * len(projected))
    window_rows = max(1, (64 << 20) // block_bytes) * feed.block_steps
    for first in range(0, rows, window_rows):
        last = min(first + window_rows, rows)
        mapped, coverage = _window(market, first, last), _window(flags, first, last)
        columns = {name: _window(desc, first, last) for name, desc in zip(projected, extras)}
        for lo in range(first, last, feed.block_steps):
            hi = min(lo + feed.block_steps, last)
            starts = feed.start + np.arange(lo, hi, dtype="i8") * feed.step
            block = (starts, mapped[lo - first : hi - first], coverage[lo - first : hi - first])
            if projected:
                yield (*block, {name: array[lo-first:hi-first] for name, array in columns.items()})
            else:
                yield block
