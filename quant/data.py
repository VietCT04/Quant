"""Aligned, availability-aware bounded market data blocks."""

from dataclasses import dataclass, replace
from contextlib import closing
from types import MappingProxyType
import hashlib
import sqlite3

import numpy as np
import pyarrow.parquet as pq

from marketdata.model import load_json, sha256, utc_day, next_month
from marketdata.reader import MarketDataset
from simulator.feed import TRADED, EMPTY, MISSING, QUARANTINED, PRELISTING

from ._core import MissingCapabilityError, duration
from .validation import _check

PRICES = ("open", "high", "low", "close")
FLOW = (
    "volume",
    "quote_volume",
    "buy_volume",
    "sell_volume",
    "buy_quote_volume",
    "sell_quote_volume",
    "agg_trade_count",
    "trade_count",
    "buy_trade_count",
    "sell_trade_count",
)
UNITS = {
    **dict.fromkeys((*PRICES, "vwap"), "USDT/base"),
    **{
        x: "USDT" if "quote" in x else "count" if "count" in x else "base" for x in FLOW
    },
    "max_trade_qty": "base",
    "is_empty": "bool",
}


@dataclass(frozen=True)
class Selection:
    """An immutable read plan for a half-open UTC range, one universe and projected fields."""

    dataset: MarketDataset
    start: int
    end: int
    symbols: tuple
    fields: tuple
    infrastructure: bool = False


@dataclass(frozen=True)
class Block:
    """Projected fields on one regular [time, symbol] array per field."""

    timestamps_ms: np.ndarray
    symbols: tuple[str, ...]
    available_at_ms: np.ndarray
    status: np.ndarray
    values: dict
    interval_ms: int
    units: dict
    provenance: tuple = ()

    def __post_init__(self):
        timestamps = np.asarray(self.timestamps_ms, dtype="i8")
        available = np.asarray(self.available_at_ms, dtype="i8")
        status = np.asarray(self.status)
        shape = (len(timestamps), len(self.symbols))
        if (
            timestamps.ndim != 1
            or self.interval_ms <= 0
            or len(set(self.symbols)) != len(self.symbols)
            or np.any(timestamps % self.interval_ms)
            or np.any(np.diff(timestamps) != self.interval_ms)
            or available.shape != (len(timestamps),)
            or np.any(available < timestamps + self.interval_ms)
            or np.any(np.diff(available) < 0)
            or status.shape != shape
            or status.dtype.kind not in "iu"
            or np.any(status < TRADED)
            or np.any(status > PRELISTING)
        ):
            raise ValueError("Invalid block axes, coverage or availability")
        values = {}
        for name, field in self.values.items():
            field = np.asarray(field, dtype=float)
            if field.shape != shape or np.any(np.isfinite(field[status > EMPTY])):
                raise ValueError(f"{name} disagrees with block shape or coverage")
            values[name] = field
        object.__setattr__(self, "timestamps_ms", timestamps)
        object.__setattr__(self, "available_at_ms", available)
        object.__setattr__(self, "status", status.astype("u1", copy=False))
        object.__setattr__(self, "values", MappingProxyType(values))
        object.__setattr__(self, "units", MappingProxyType(dict(self.units)))

    @property
    def valid(self):
        """Per-field validity. Confirmed empty intervals can contain price and zero flow."""
        return {
            name: (self.status <= EMPTY) & np.isfinite(x)
            for name, x in self.values.items()
        }


def open(
    path="data/binance/um/bars_5s", *, universe="liquid50", availability_lag_ms=100
):
    """Open the existing Arrow dataset with the frozen Liquid 50 as the default universe."""
    return MarketDataset(
        path, universe=universe, availability_lag_ms=availability_lag_ms
    )


def select(data, *, start, end, symbols=None, fields=("close",), infrastructure=False):
    """Plan a projected, half-open UTC time range without loading bar data."""
    lo, hi = _check(start, end, infrastructure=infrastructure)
    fields = tuple(dict.fromkeys(fields))
    absent = set(fields).difference(UNITS)
    if absent:
        raise MissingCapabilityError("market bars", sorted(absent))
    symbols = tuple(data._symbols(symbols))
    if not symbols or not fields or lo % data.interval_ms or hi % data.interval_ms:
        raise ValueError("Select fields and symbols with UTC grid-aligned boundaries")
    return Selection(data, lo, hi, symbols, fields, infrastructure)


def iter_blocks(
    selection, *, block_steps=4096, memory_bytes=64 << 20, decoded_cache=None
):
    """Iterate bounded dense blocks without sorting the source or manufacturing unknown observations."""
    yield from _iter_blocks(selection, block_steps, memory_bytes, decoded_cache)


def _iter_blocks(selection, block_steps, memory_bytes, decoded_cache, *, use_threads=True):
    ds, symbols, fields = selection.dataset, selection.symbols, selection.fields
    lo, hi = _check(
        selection.start, selection.end, infrastructure=selection.infrastructure
    )
    rows = memory_bytes // (8 * (len(fields) + 2) * len(symbols) + 16)
    if min(rows, block_steps) < 1:
        raise ValueError("Insufficient block budget")
    steps = min(rows, block_steps)
    if decoded_cache is None:
        yield from _source_blocks(selection, steps, use_threads=use_threads)
        return
    from .features import Cache, cache_key

    if not isinstance(decoded_cache, Cache):
        raise TypeError("Expected a bounded feature Cache")
    identity = fingerprint(selection)
    step, lag = ds.interval_ms, ds.availability_lag_ms
    source = None
    for begin in range(lo, hi, steps * step):
        stop = min(hi, begin + steps * step)
        key = cache_key(
            function="quant.data.iter_blocks",
            version="1",
            fingerprint=identity,
            parameters=[begin, stop, fields],
            missing_policy="published bar coverage",
            configuration=[symbols, step, lag, selection.infrastructure],
        )
        packed = decoded_cache.get(key)
        shape = (1 + len(fields), (stop - begin) // step, len(symbols))
        if packed is not None and packed.shape == shape:
            t = np.arange(begin, stop, step, dtype="i8")
            if source is not None:
                next(source)
            yield Block(
                t,
                symbols,
                t + step + lag,
                packed[0].astype("u1"),
                dict(zip(fields, packed[1:])),
                step,
                {f: UNITS[f] for f in fields},
                ("binance_um",),
            )
        else:
            if source is None:
                source = iter(_source_blocks(replace(selection, start=begin), steps, use_threads=use_threads))
            block = next(source)
            decoded_cache.put(key, np.stack([block.status, *block.values.values()]))
            yield block


def _source_blocks(selection, steps, *, use_threads=True):
    ds, symbols, fields = selection.dataset, selection.symbols, selection.fields
    lo, hi = selection.start, selection.end
    universe = load_json(ds.meta / "universes" / "liquid50.json")
    listing = {
        item["symbol"]: item["listing_timestamp_ms"] for item in universe["symbols"]
    }
    quarantine = {s: [] for s in symbols}
    for row in ds.coverage(symbols, lo, hi).to_pylist():
        if row["status"] in {
            "quarantined_source_conflict",
            "official_aggtrade_gap",
            "failed_validation",
        }:
            quarantine[row["symbol"]].append((row["start_ms"], row["end_ms"]))
    state_intervals = {}
    for symbol, ranges in quarantine.items():
        ranges = np.asarray(sorted(ranges), dtype="i8").reshape(-1, 2)
        if len(ranges):
            ranges[:, 1] = np.maximum.accumulate(ranges[:, 1])
        state_intervals[symbol] = ranges
    step = ds.interval_ms
    projected = tuple(dict.fromkeys((*fields, "is_empty")))
    for begin, stop, tables in ds.iter_symbol_slices(
        symbols, lo, hi, projected, time_steps=steps, use_threads=use_threads
    ):
        t = np.arange(begin, stop, step, dtype="i8")
        state = np.full((len(t), len(symbols)), MISSING, dtype="u1")
        values = {f: np.full(state.shape, np.nan) for f in fields}
        for i, symbol in enumerate(symbols):
            intervals = state_intervals[symbol]
            if len(intervals):
                idx = np.searchsorted(intervals[:, 0], t, side="right") - 1
                state[(idx >= 0) & (t < intervals[idx, 1]), i] = QUARANTINED
            if symbol in listing:
                state[t + step <= listing[symbol], i] = PRELISTING
            table = tables[symbol]
            if table is None:
                continue
            offsets = (table["timestamp_ms"].to_numpy() - begin) // step
            mask = state[offsets, i] == MISSING
            state[offsets[mask], i] = np.where(
                table["is_empty"].to_numpy()[mask], EMPTY, TRADED
            )
            for field in fields:
                values[field][offsets[mask], i] = table[field].to_numpy()[mask]
        yield Block(
            t,
            symbols,
            t + step + ds.availability_lag_ms,
            state,
            values,
            step,
            {f: UNITS[f] for f in fields},
            ("binance_um",),
        )


def align(*blocks, mode="exact"):
    """Align regular blocks by exact, inner or outer UTC times and symbol labels without filling prices."""
    if not blocks:
        raise ValueError("At least one block is required")
    first = blocks[0]
    for block in blocks[1:]:
        if first.interval_ms != block.interval_ms:
            raise ValueError("Incompatible sampling intervals")
    if mode == "exact":
        for block in blocks[1:]:
            if first.symbols != block.symbols or not np.array_equal(
                first.timestamps_ms, block.timestamps_ms
            ):
                raise ValueError("Incompatible time or symbol axes")
        return blocks
    if mode not in {"inner", "outer"} or any(
        not len(block.timestamps_ms) for block in blocks
    ):
        raise ValueError("Inner and outer alignment require nonempty source axes")
    step = first.interval_ms
    if mode == "inner":
        lo = max(block.timestamps_ms[0] for block in blocks)
        hi = min(block.timestamps_ms[-1] for block in blocks)
        symbols = tuple(s for s in first.symbols if all(s in b.symbols for b in blocks))
    else:
        lo = min(block.timestamps_ms[0] for block in blocks)
        hi = max(block.timestamps_ms[-1] for block in blocks)
        symbols = tuple(dict.fromkeys(s for block in blocks for s in block.symbols))
    times = np.arange(lo, hi + step, step, dtype="i8")
    result = []
    for block in blocks:
        valid_t = (times >= block.timestamps_ms[0]) & (times <= block.timestamps_ms[-1])
        new_i = np.flatnonzero(valid_t)
        old_i = (times[valid_t] - block.timestamps_ms[0]) // step
        new_j = [j for j, s in enumerate(symbols) if s in block.symbols]
        old_j = [block.symbols.index(symbols[j]) for j in new_j]
        dst, src = (
            np.ix_(new_i, np.array(new_j, dtype=int)),
            np.ix_(old_i, np.array(old_j, dtype=int)),
        )
        status = np.full((len(times), len(symbols)), MISSING, dtype="u1")
        status[dst] = block.status[src]
        fields = {}
        for name, values in block.values.items():
            field = np.full(status.shape, np.nan)
            field[dst] = values[src]
            fields[name] = field
        available = times + step
        available[new_i] = block.available_at_ms[old_i]
        available = np.maximum.accumulate(available)
        result.append(
            Block(
                times,
                symbols,
                available,
                status,
                fields,
                step,
                block.units,
                block.provenance,
            )
        )
    return tuple(result)


def _join(left, right):
    return Block(
        np.r_[left.timestamps_ms, right.timestamps_ms],
        left.symbols,
        np.r_[left.available_at_ms, right.available_at_ms],
        np.concatenate([left.status, right.status]),
        {f: np.concatenate([left.values[f], right.values[f]]) for f in left.values},
        left.interval_ms,
        dict(left.units),
        left.provenance,
    )


def _slice(block, start, end):
    sl = slice(start, end)
    return replace(
        block,
        timestamps_ms=block.timestamps_ms[sl],
        available_at_ms=block.available_at_ms[sl],
        status=block.status[sl],
        values={f: x[sl] for f, x in block.values.items()},
    )


def resample(blocks, resolution):
    """Emit complete UTC buckets with conservative coverage and last-input availability."""
    size = duration(resolution) if isinstance(resolution, str) else resolution
    carry = None
    for block in blocks:
        if not len(block.timestamps_ms):
            continue
        if (
            not isinstance(size, int)
            or size < block.interval_ms
            or size % block.interval_ms
        ):
            raise ValueError(
                "Resolution must be an integer multiple of the source sampling interval"
            )
        if carry is not None:
            if (
                carry.symbols != block.symbols
                or carry.interval_ms != block.interval_ms
                or carry.values.keys() != block.values.keys()
                or carry.units != block.units
            ):
                raise ValueError("Resampling requires consistent source blocks")
            block = _join(carry, block)
        group = size // block.interval_ms
        head = (-int(block.timestamps_ms[0]) % size) // block.interval_ms
        stop = head + max(0, len(block.timestamps_ms) - head) // group * group
        carry = _slice(
            block,
            min(head, len(block.timestamps_ms))
            if stop > len(block.timestamps_ms)
            else stop,
            None,
        )
        if stop <= head:
            continue
        src = _slice(block, head, stop)
        count = len(src.timestamps_ms) // group
        shape = (count, group, len(src.symbols))
        status = src.status.reshape(shape)
        pre = status == PRELISTING
        unknown = (status == MISSING).any(axis=1) | (pre.any(axis=1) & ~pre.all(axis=1))
        coverage = np.where((status == TRADED).any(axis=1), TRADED, EMPTY)
        coverage[unknown] = MISSING
        coverage[pre.all(axis=1)] = PRELISTING
        coverage[(status == QUARANTINED).any(axis=1)] = QUARANTINED
        values = {}
        for name, field in src.values.items():
            a = field.reshape(shape)
            if name in FLOW:
                y = a.sum(axis=1)
            elif name == "open":
                first = np.argmax(status == TRADED, axis=1)
                y = np.take_along_axis(a, first[:, None, :], axis=1)[:, 0, :]
            elif name == "close":
                y = a[:, -1, :].copy()
            elif name in {"high", "low", "max_trade_qty"}:
                reducer = np.max if name != "low" else np.min
                if name != "max_trade_qty":
                    has_trade = (status == TRADED).any(axis=1)[:, None, :]
                    a = np.where(
                        has_trade & (status == EMPTY),
                        -np.inf if name == "high" else np.inf,
                        a,
                    )
                y = reducer(a, axis=1)
            elif name == "is_empty":
                y = (coverage == EMPTY).astype(float)
            else:
                continue
            y[coverage > EMPTY] = np.nan
            values[name] = y
        if "vwap" in src.values:
            if not {"quote_volume", "volume"} <= values.keys():
                raise MissingCapabilityError(
                    "resampled vwap", ("volume", "quote_volume")
                )
            from ._core import divide

            values["vwap"] = divide(values["quote_volume"], values["volume"])
        yield Block(
            src.timestamps_ms[::group],
            src.symbols,
            src.available_at_ms.reshape(count, group).max(axis=1),
            coverage,
            values,
            size,
            src.units,
            (*src.provenance, f"resample:{size}"),
        )


def _aux(selection, kind, clock):
    ds = selection.dataset
    lo, hi = _check(
        selection.start, selection.end, infrastructure=selection.infrastructure
    )
    root = ds.path.parent / "simulator_aux" / kind
    universe = load_json(ds.meta / "universes" / "liquid50.json")
    listing = {
        row["symbol"]: row["listing_timestamp_ms"] for row in universe["symbols"]
    }
    for symbol in selection.symbols:
        begin = max(lo, listing.get(symbol, lo))
        if begin >= hi:
            continue
        if kind == "funding":
            paths = [root / f"{symbol}.parquet"]
        else:
            month = utc_day(begin).replace(day=1)
            paths = []
            while month <= utc_day(hi - 1):
                paths.append(root / symbol / f"{month:%Y-%m}.parquet")
                month = next_month(month)
        for path in paths:
            if not path.exists():
                raise MissingCapabilityError(f"{kind}:{symbol}", (str(path),))
            yield (
                symbol,
                pq.read_table(path, filters=[(clock, ">=", begin), (clock, "<", hi)]),
            )


def load_funding(selection):
    """Yield per-symbol funding tables filtered by recorded settlement time."""
    yield from _aux(selection, "funding", "timestamp_ms")


def load_marks(selection):
    """Yield per-symbol monthly mark tables filtered by recorded availability time."""
    yield from _aux(selection, "marks", "available_ms")


def asof_observations(block, observations, *, value, clock, max_age_ms, units):
    """Align auxiliary observations using their actual availability, never looking forward."""
    if (
        not isinstance(block, Block)
        or not isinstance(max_age_ms, int)
        or max_age_ms < 0
    ):
        raise ValueError("Need a source Block and a nonnegative maximum age")
    rows = {s: [] for s in block.symbols}
    for symbol, table in observations:
        if symbol in rows:
            rows[symbol].append((table[clock].to_numpy(), table[value].to_numpy()))
    result = np.full(block.status.shape, np.nan)
    for j, symbol in enumerate(block.symbols):
        if not rows[symbol]:
            continue
        times, values = (np.concatenate(col) for col in zip(*rows[symbol]))
        if not len(times):
            continue
        order = np.argsort(times, kind="stable")
        times, values = times[order], values[order]
        if np.any(np.diff(times) <= 0) or not np.isfinite(times).all():
            raise ValueError(
                "Auxiliary observation clocks must be finite and unique per symbol"
            )
        indices = np.searchsorted(times, block.available_at_ms, side="right") - 1
        candidates = np.maximum(indices, 0)
        valid = (
            (indices >= 0)
            & (block.available_at_ms - times[candidates] <= max_age_ms)
            & (block.status[:, j] <= EMPTY)
        )
        result[valid, j] = values[candidates[valid]]
    return replace(
        block,
        values={value: result},
        units={value: units},
        provenance=(*block.provenance, clock),
    )


def fingerprint(selection, *, verify_contents=False):
    """Digest selection, current manifest receipts and metadata; optionally hash selected Parquet bytes."""
    ds = selection.dataset
    h = hashlib.sha256(
        repr(
            (
                selection.start,
                selection.end,
                selection.symbols,
                selection.fields,
                ds.interval_ms,
                ds.availability_lag_ms,
                selection.infrastructure,
            )
        ).encode()
    )
    for path in (
        ds.meta / "universes" / "liquid50.json",
        ds.meta / "coverage.parquet",
        ds.path.parent / "simulator_aux" / "inventory.json",
    ):
        if path.is_file():
            h.update(sha256(path).encode())
    manifest = ds.meta / "manifest.sqlite"
    if manifest.is_file():
        placeholders = ",".join("?" for _ in selection.symbols)
        months = (
            utc_day(selection.start).isoformat()[:7],
            utc_day(selection.end - 1).isoformat()[:7],
        )
        with closing(
            sqlite3.connect(manifest.resolve().as_uri() + "?mode=ro", uri=True)
        ) as conn:
            rows = conn.execute(
                f"""SELECT * FROM partitions WHERE month BETWEEN ? AND ?
                AND symbol IN ({placeholders}) ORDER BY symbol,month""",
                (*months, *selection.symbols),
            )
            for row in rows:
                h.update(repr(row).encode())
    if verify_contents or not manifest.is_file():
        for symbol in selection.symbols:
            for path in ds._paths(symbol, selection.start, selection.end):
                h.update(f"{symbol}/{path.parents[1].name}/{path.parent.name}".encode())
                h.update(sha256(path).encode())
    return h.hexdigest()


__all__ = [
    "open",
    "select",
    "iter_blocks",
    "align",
    "resample",
    "load_funding",
    "load_marks",
    "asof_observations",
    "fingerprint",
]
