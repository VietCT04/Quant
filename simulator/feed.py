from __future__ import annotations

from functools import lru_cache
from concurrent.futures import ThreadPoolExecutor
from contextlib import nullcontext
from time import perf_counter

import numpy as np
import pyarrow.parquet as pq

from marketdata.model import MINUTE, load_json, next_month, utc_day
from marketdata.reader import as_millis

from .native import SETTLEMENT

TRADED, EMPTY, MISSING, QUARANTINED, PRELISTING = range(5)
FIELDS = ["close", "volume", "quote_volume", "is_empty"]


def order_rules(dataset, symbols):
    universe = load_json(dataset.meta / "universes" / "liquid50.json")
    metadata = load_json(dataset.meta.parent / universe["exchange_snapshot"])
    by_name = {item["symbol"]: item for item in metadata["symbols"]}
    rules = []
    for symbol in symbols:
        filters = {x["filterType"]: x for x in by_name[symbol]["filters"]}
        market = float(filters.get("MARKET_LOT_SIZE", {}).get("stepSize", "0"))
        lot = market or float(filters["LOT_SIZE"]["stepSize"])
        notional = filters.get("MIN_NOTIONAL", filters.get("NOTIONAL", {}))
        rules.append((lot, float(notional.get("notional", notional.get("minNotional", 0)))))
    return rules


@lru_cache(maxsize=120)
def _marks(path):
    if not path.is_file():
        return np.empty(0, dtype="i8"), np.empty(0)
    t = pq.ParquetFile(path).read(columns=["available_ms", "price"])
    return t["available_ms"].to_numpy(), t["price"].to_numpy()


class MarketDataFeed:
    def __init__(self, dataset, symbols, start, end, *, block_steps=4096, aux=True, mark_staleness_ms=MINUTE, completed_clock=False, trading_start=None):
        self.dataset = dataset
        self.symbols = tuple(dataset._symbols(symbols))
        self.start, self.end = as_millis(start), as_millis(end)
        self.trading_start = self.start if trading_start is None else as_millis(trading_start)
        self.completed_clock = completed_clock
        self.step = dataset.interval_ms
        self.lag = 0 if completed_clock else dataset.availability_lag_ms
        if not self.symbols or self.start >= self.end or self.start % self.step or self.end % self.step:
            raise ValueError("A nonempty universe and interval-aligned UTC range are required")
        self.block_steps = block_steps
        self.read_threads = True
        self.read_workers = 1
        self.profile = dict(parquet_and_slicing_seconds=0., alignment_and_marks_seconds=0.)
        if mark_staleness_ms < 1:
            raise ValueError("Mark staleness limit must be positive")
        self.mark_staleness_ms = mark_staleness_ms
        self.aux = aux
        self.aux_root = dataset.path.parent / "simulator_aux"
        universe = load_json(dataset.meta / "universes" / "liquid50.json")
        listing = {r["symbol"]: r["listing_timestamp_ms"] for r in universe["symbols"]}
        self.listing = [listing[s] for s in self.symbols]
        coverage = {s: [] for s in self.symbols}
        for row in dataset.coverage(self.symbols, self.start, self.end).to_pylist():
            if row["status"] in {"quarantined_source_conflict", "official_aggtrade_gap", "failed_validation"}:
                coverage[row["symbol"]].append((row["start_ms"], row["end_ms"]))
        self.quarantine = [np.array(sorted(coverage[s]), dtype="i8").reshape(-1, 2) for s in self.symbols]
        self._funding = None
        self.cursor = 0
        self.mark_missing_slots = np.zeros(len(self.symbols), dtype="i8")
        self.status_counts = np.zeros((5, len(self.symbols)), dtype="i8")

    def _funding_data(self):
        if self._funding is None:
            self._funding = self._settlements()
        return self._funding

    @property
    def settlements(self):
        return self._funding_data()[0]

    @property
    def funding_warnings(self):
        return self._funding_data()[1]

    def use_funding(self, settlements, warnings):
        self._funding = settlements, warnings
        self.cursor = 0

    def _settlements(self):
        pieces, warnings = [], []
        for i, symbol in enumerate(self.symbols):
            start = max(self.trading_start, self.listing[i])
            if start >= self.end:
                continue
            path = self.aux_root / "funding" / f"{symbol}.parquet"
            receipt = path.with_suffix(".json")
            if not (self.aux and path.exists() and receipt.exists()):
                warnings.append({"symbol": symbol, "reason": "No verified funding file"})
                continue
            info = load_json(receipt)
            data = pq.ParquetFile(path).read()
            t = data["timestamp_ms"].to_numpy()
            lo, hi = np.searchsorted(t, [start, self.end])
            selected = t[lo:hi]
            largest_gap = np.diff(np.r_[start, selected, self.end]).max()
            if info["start_ms"] > start or info["end_ms_exclusive"] < self.end or largest_gap > 12 * 60 * MINUTE:
                warnings.append({"symbol": symbol, "reason": "Archive range or settlement cadence has a gap",
                                 "largest_gap_ms": int(largest_gap)})
            result = np.empty(hi-lo, dtype=SETTLEMENT)
            result["ms"], result["symbol"] = selected, i
            result["rate"], result["price"] = (data[f].to_numpy()[lo:hi] for f in ("rate", "price"))
            verified = np.isfinite(result["price"]) & (result["price"] > 0)
            if not verified.all():
                warnings.append({"symbol": symbol, "reason": "Funding events missing a usable settlement mark",
                                 "timestamps_ms": selected[~verified].tolist()})
            pieces.append(result[verified])
        complete = np.concatenate(pieces) if pieces else np.empty(0, dtype=SETTLEMENT)
        return complete[np.argsort(complete["ms"], kind="stable")], warnings

    def funding_through(self, now):
        stop = np.searchsorted(self.settlements["ms"], now, side="right")
        out = self.settlements[self.cursor:stop]
        self.cursor = stop
        return out

    def _fill_marks(self, symbol, starts, clock, minute_keys, minute_index, out):
        first = utc_day(int(starts[0]) - self.mark_staleness_ms).replace(day=1)
        last = utc_day(int(starts[-1]) + self.step + self.lag)
        while first <= last:
            times, prices = _marks(self.aux_root / "marks" / symbol / f"{first:%Y-%m}.parquet")
            if len(times):
                idx = np.searchsorted(times, minute_keys, side="right")[minute_index] - 1
                candidates = np.maximum(idx, 0)
                valid = (idx >= 0) & (clock - times[candidates] < self.mark_staleness_ms)
                out[valid] = prices[candidates[valid]]
            first = next_month(first)

    def __iter__(self):
        return self.blocks()

    def blocks(self, *, shared=False, projected=()):
        fields = tuple(dict.fromkeys((*FIELDS, *projected)))
        source = self.dataset.iter_symbol_slices(self.symbols, self.start, self.end, fields,
                                                 time_steps=self.block_steps, use_threads=self.read_threads, workers=self.read_workers)
        alignment_groups = [range(i, len(self.symbols), self.read_workers) for i in range(self.read_workers)]
        manager = ThreadPoolExecutor(max_workers=self.read_workers) if self.read_workers > 1 else nullcontext()
        with manager as align_pool:
            while True:
                now = perf_counter()
                piece = next(source, None)
                self.profile["parquet_and_slicing_seconds"] += perf_counter() - now
                if piece is None:
                    break
                lo, hi, samples = piece
                now = perf_counter()
                starts = np.arange(lo, hi, self.step, dtype="i8")
                clock = starts + self.step + self.lag
                clock_minute = (clock - 100) // MINUTE
                minute_keys = (np.arange(clock_minute[0], clock_minute[-1] + 1) * MINUTE) + 100
                minute_index = clock_minute - clock_minute[0]
                cols = 4 if shared else 5
                # Populate contiguous per-symbol runs, then pack the native time-major block once.
                n, ticks = len(self.symbols), len(starts)
                values = np.full((n, ticks, cols), np.nan)
                flags = np.full((n, ticks), MISSING, dtype="u1")
                extras = {name: np.full((n, ticks), np.nan) for name in projected}
                actual_start = int(np.searchsorted(starts, self.trading_start))
                def align(i, symbol, table):
                    intervals = self.quarantine[i]
                    state = flags[i]
                    has_quarantine = len(intervals) and intervals[0, 0] <= starts[-1] and intervals[-1, 1] > starts[0]
                    if has_quarantine:
                        j = np.searchsorted(intervals[:, 0], starts, side="right") - 1
                        state[(j >= 0) & (starts < intervals[j, 1])] = QUARANTINED
                    has_prelisting = starts[0] + self.step <= self.listing[i]
                    if has_prelisting:
                        state[:int(np.searchsorted(starts, self.listing[i] - self.step, side="right"))] = PRELISTING
                    if table is not None:
                        times = table["timestamp_ms"].to_numpy()
                        dense = (not (self.completed_clock and (has_quarantine or has_prelisting))
                                 and len(times) == ticks and np.array_equal(times, starts))
                        offsets = slice(None) if dense else (times - lo) // self.step
                        close, vol, quote, empty = (table[name].to_numpy() for name in FIELDS)
                        allowed = None
                        if self.completed_clock and (has_quarantine or has_prelisting):
                            allowed = state[offsets] == MISSING
                            offsets, close, vol, quote, empty = (x[allowed] for x in (offsets, close, vol, quote, empty))
                        state[offsets] = np.where(empty, EMPTY, TRADED)
                        values[i, offsets, 0] = close
                        values[i, offsets, 1] = np.divide(quote, vol, out=np.full_like(vol, np.nan), where=vol > 0)
                        values[i, offsets, 2] = vol
                        for name, projection in extras.items():
                            column = (
                                quote if name == "quote_volume" and allowed is None
                                else table[name].to_numpy()
                            )
                            projection[i, offsets] = column if allowed is None else column[allowed]
                    if self.aux and state[-1] != PRELISTING:
                        self._fill_marks(symbol, starts, clock, minute_keys, minute_index, values[i, :, 3])
                    if has_prelisting:
                        values[i, state == PRELISTING, 3] = np.nan
                    actual = state[actual_start:]
                    self.mark_missing_slots[i] += np.count_nonzero(
                        ~np.isfinite(values[i, actual_start:, 3]) & (actual != PRELISTING)
                    )
                    self.status_counts[:, i] += np.bincount(actual, minlength=5)
                if align_pool is None:
                    for i, (symbol, table) in enumerate(samples.items()):
                        align(i, symbol, table)
                else:
                    def align_group(group):
                        for i in group:
                            symbol = self.symbols[i]
                            align(i, symbol, samples[symbol])
                    list(align_pool.map(align_group, alignment_groups))
                values = np.ascontiguousarray(values.transpose(1, 0, 2))
                flags = np.ascontiguousarray(flags.T)
                extras = {name: np.ascontiguousarray(x.T) for name, x in extras.items()}
                self.profile["alignment_and_marks_seconds"] += perf_counter() - now
                yield (starts, values, flags, extras) if projected else (starts, values, flags)
