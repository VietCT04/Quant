from __future__ import annotations

from datetime import UTC, date, datetime
from contextlib import closing
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import sqlite3

import numpy as np
import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.dataset as ds

from .aggregate import SCHEMA
from .model import load_json, millis, next_month, symbol_name, utc_day

FEATURES = SCHEMA.append(pa.field("symbol", pa.string())).append(pa.field("available_at_ms", pa.int64()))


def as_millis(value):
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        value = datetime.fromisoformat(value)
    if isinstance(value, datetime):
        value = value.replace(tzinfo=UTC) if value.tzinfo is None else value
        return int(value.timestamp() * 1000)
    if isinstance(value, date):
        return millis(value)
    raise TypeError(f"Unsupported timestamp {value!r}")


class MarketDataset:
    """Read-only Arrow access. Date ranges refer to bar starts and are half-open."""

    def __init__(self, path, *, availability_lag_ms=100, universe=None):
        self.path = Path(path)
        if not self.path.name.startswith("bars_") or not self.path.name.endswith("s"):
            raise ValueError("Pass the bars_<seconds>s dataset directory")
        self.interval_ms = int(self.path.name[5:-1]) * 1000
        if not isinstance(availability_lag_ms, int) or availability_lag_ms < 0:
            raise ValueError("Availability lag must be a nonnegative number of milliseconds")
        self.availability_lag_ms = availability_lag_ms
        self.meta = self.path.parents[2] / "metadata"
        self.universe_symbols = None
        if universe is not None:
            config = load_json(self.meta / "universes" / f"{symbol_name(universe)}.json")
            self.universe_symbols = tuple(symbol_name(row["symbol"]) for row in config["symbols"])

    def _symbols(self, symbols):
        if symbols is None:
            if self.universe_symbols is None:
                raise ValueError("Pass symbols or configure a universe")
            return list(self.universe_symbols)
        requested = list(dict.fromkeys(map(symbol_name, symbols)))
        if self.universe_symbols is not None and set(requested).difference(self.universe_symbols):
            raise ValueError("Requested symbols must belong to the configured universe")
        return requested

    def _manifest_months(self, symbols):
        manifest = self.meta / "manifest.sqlite"
        if not manifest.is_file():
            return None
        result = {symbol: set() for symbol in symbols}
        places = ",".join("?" for _ in symbols)
        with closing(sqlite3.connect(manifest.resolve().as_uri() + "?mode=ro", uri=True)) as db:
            rows = db.execute(
                f"""SELECT symbol,month FROM partitions WHERE symbol IN ({places})
                    AND status IN ('complete','qualified','incomplete')""", tuple(symbols),
            )
            for symbol, month in rows:
                result[symbol].add(month)
        return result

    def _paths(self, symbol, lo, hi, *, months=None):
        manifest = self.meta / "manifest.sqlite"
        committed = None
        if months is not None:
            committed = months[symbol]
        elif manifest.is_file():
            with closing(sqlite3.connect(manifest.resolve().as_uri() + "?mode=ro", uri=True)) as db:
                committed = {row[0] for row in db.execute(
                    "SELECT month FROM partitions WHERE symbol=? AND status IN ('complete','qualified','incomplete')",
                    (symbol,),
                )}
        current = utc_day(lo).replace(day=1)
        while millis(current) < hi:
            target = (self.path / f"symbol={symbol_name(symbol)}" / f"year={current.year:04d}"
                      / f"month={current.month:02d}" / "part-000.parquet")
            if (committed is None or current.isoformat()[:7] in committed) and target.is_file():
                yield target
            current = next_month(current)

    def _scan(self, symbol, lo, hi, internal, num_rows, use_threads=True, *, months=None):
        columns = [f for f in internal if f not in {"symbol", "available_at_ms"}]
        for path in self._paths(symbol, lo, hi, months=months):
            source = ds.dataset(path, format="parquet")
            predicate = (ds.field("timestamp_ms") >= lo) & (ds.field("timestamp_ms") < hi)
            for batch in source.to_batches(columns=columns, filter=predicate,
                                           batch_size=num_rows, use_threads=use_threads, batch_readahead=1):
                if not batch.num_rows:
                    continue
                if "symbol" not in internal and "available_at_ms" not in internal:
                    yield batch
                    continue
                data = {name: batch[name] for name in columns}
                if "symbol" in internal:
                    data["symbol"] = pa.repeat(pa.scalar(symbol), batch.num_rows)
                if "available_at_ms" in internal:
                    data["available_at_ms"] = pc.add(batch["timestamp_ms"], self.interval_ms + self.availability_lag_ms)
                yield pa.record_batch(data)

    def _slices(self, symbols, lo, hi, columns, steps, use_threads=True, workers=1):
        internal = list(dict.fromkeys(["timestamp_ms", *columns]))
        months = self._manifest_months(symbols)
        streams = {s: iter(self._scan(s, lo, hi, internal, max(steps, 32768), use_threads, months=months))
                   for s in symbols}
        pending = {s: None for s in symbols}
        groups = [symbols[i::workers] for i in range(workers)]

        def collect(group, end, first):
            result = {}
            for symbol in group:
                stream = streams[symbol]
                batch = next(stream, None) if first else pending[symbol]
                parts = []
                while batch is not None:
                    index = int(np.searchsorted(batch["timestamp_ms"].to_numpy(), end))
                    if index:
                        parts.append(batch.slice(0, index))
                    if index < batch.num_rows:
                        pending[symbol] = batch.slice(index)
                        break
                    batch = next(stream, None)
                else:
                    pending[symbol] = None
                result[symbol] = pa.Table.from_batches(parts) if parts else None
            return result

        pool = ThreadPoolExecutor(max_workers=workers) if workers > 1 else None
        try:
            for start in range(lo, hi, steps * self.interval_ms):
                end = min(hi, start + steps * self.interval_ms)
                if pool is None:
                    result = collect(symbols, end, start == lo)
                else:
                    parts = pool.map(lambda group: collect(group, end, start == lo), groups)
                    combined = {s: table for piece in parts for s, table in piece.items()}
                    result = {s: combined[s] for s in symbols}
                yield start, end, result
        finally:
            if pool is not None:
                pool.shutdown()

    def iter_batches(self, symbols, start, end, columns=None, *, batch_size=65_536):
        """Yield chronologically interleaved RecordBatches, breaking ties by symbol."""
        lo, hi = as_millis(start), as_millis(end)
        symbols = self._symbols(symbols)
        columns = list(columns) if columns is not None else [*SCHEMA.names, "symbol"]
        required = list(dict.fromkeys(["timestamp_ms", "symbol", *columns]))
        if not symbols or hi <= lo or batch_size < 1:
            raise ValueError("Provide symbols, an increasing range, and a positive batch size")
        if any(f not in FEATURES.names for f in columns):
            raise ValueError("Unknown market data column")
        for _, _, tables in self._slices(symbols, lo, hi, required, max(1, batch_size // len(symbols))):
            present = [table for table in tables.values() if table is not None]
            if present:
                table = pa.concat_tables(present).sort_by([("timestamp_ms", "ascending"), ("symbol", "ascending")])
                yield from table.select(columns).to_batches(max_chunksize=batch_size)

    def load(self, symbols, start, end, columns=None):
        """Materialize a selected time slice. Use iter_batches for long windows."""
        columns = list(columns) if columns is not None else [*SCHEMA.names, "symbol"]
        batches = list(self.iter_batches(symbols, start, end, columns))
        if not batches:
            return pa.Table.from_batches([], schema=pa.schema([FEATURES.field(name) for name in columns]))
        return pa.Table.from_batches(batches)

    def iter_aligned(self, symbols, start, end, columns, *, time_steps=4096):
        """Yield (timestamps, {symbol: Arrow table}) with nulls and a present flag for uncovered slots."""
        lo, hi = as_millis(start), as_millis(end)
        wanted = list(dict.fromkeys(columns))
        symbols = self._symbols(symbols)
        if not symbols or hi <= lo or time_steps < 1 or lo % self.interval_ms or hi % self.interval_ms:
            raise ValueError("Aligned iteration requires symbols, a positive batch size, and aligned UTC boundaries")
        for begin, stop, parts in self._slices(symbols, lo, hi, wanted, time_steps):
            axis = pa.array(np.arange(begin, stop, self.interval_ms, dtype=np.int64))
            result = {}
            for symbol, table in parts.items():
                indices = pc.index_in(axis, value_set=table["timestamp_ms"].combine_chunks()) if table is not None else None
                aligned = {
                    name: pc.take(table[name], indices) if table is not None else pa.nulls(len(axis), FEATURES.field(name).type)
                    for name in wanted
                }
                aligned["present"] = pc.is_valid(indices) if indices is not None else pa.repeat(pa.scalar(False), len(axis))
                result[symbol] = pa.table(aligned)
            yield axis, result

    def iter_symbol_slices(self, symbols, start, end, columns, *, time_steps=4096, use_threads=True, workers=1):
        """Yield bounded UTC windows of sparse, ordered Arrow tables keyed by symbol."""
        lo, hi = as_millis(start), as_millis(end)
        resolved = self._symbols(symbols)
        if not resolved or time_steps < 1 or lo >= hi or lo % self.interval_ms or hi % self.interval_ms:
            raise ValueError("Symbol slices require symbols, aligned UTC boundaries and a positive batch size")
        return self._slices(resolved, lo, hi, columns, time_steps, use_threads, workers)

    def coverage(self, symbols, start, end):
        """Return reported coverage segments. An absent segment is outside published coverage."""
        source = ds.dataset(self.meta / "coverage.parquet", format="parquet")
        query = (ds.field("symbol").isin(self._symbols(symbols)) & (ds.field("end_ms") > as_millis(start))
                 & (ds.field("start_ms") < as_millis(end)))
        return source.to_table(filter=query)
