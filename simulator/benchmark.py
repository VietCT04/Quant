"""Reader and engine benchmarks. No evaluation of strategy returns."""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Event, Thread
from time import perf_counter

import numpy as np
import psutil
import pyarrow as pa

from marketdata.model import write_json
from marketdata.reader import MarketDataset, as_millis

from .__main__ import HOLDOUT
from .feed import FIELDS, MarketDataFeed, order_rules
from .native import Engine, Settings, library
from .strategies import Signals


def reader_pass(dataset, symbols, start, end, method):
    t = perf_counter()
    rows = 0
    if method == "sorted_arrow":
        for block in dataset.iter_batches(symbols, start, end, FIELDS):
            rows += block.num_rows
    elif method == "aligned_arrow":
        for _, batch in dataset.iter_aligned(symbols, start, end, FIELDS):
            rows += sum(len(t["close"]) - t["close"].null_count for t in batch.values())
    elif method == "arrow_parquet_projection":
        lo, hi = as_millis(start), as_millis(end)
        for symbol in dataset._symbols(symbols):
            rows += sum(b.num_rows for b in dataset._scan(symbol, lo, hi, FIELDS, 65536))
    return {"method": method, "rows": rows, "seconds": perf_counter() - t}


def stream(dataset, symbols, start, end, *, measure_signals=False):
    t = perf_counter()
    feed = MarketDataFeed(dataset, symbols, start, end)
    settings = Settings(logging=0)
    core = signals = 0
    n = obs = 0
    strategy = Signals("ema", feed.symbols, 0, settings.rebalance_ticks, 41) if measure_signals else None
    allocations = np.zeros(len(feed.symbols))
    with Engine(settings, order_rules(dataset, feed.symbols), allocations if strategy is None else None) as e:
        for starts, values, flags in feed:
            if strategy:
                tick = perf_counter()
                strategy.apply(values, flags)
                signals += perf_counter() - tick
            pending = feed.funding_through(starts[-1] + settings.interval + settings.lag)
            tick = perf_counter()
            e.run(starts, values, flags, pending)
            core += perf_counter() - tick
            n += flags.size
            obs += int(np.count_nonzero(flags <= 1))
    total = perf_counter() - t
    return {"slots": n, "observed_bars": obs, "total_seconds": total, "core_seconds": core,
            "strategy_seconds": signals, "feed_and_overhead_seconds": total-core-signals,
            "core_slots_per_second": n/core, "core_observed_bars_per_second": obs/core,
            "observed_bars_per_second": obs/total}


def codec_test(dataset):
    symbols, start, end = ["BTCUSDT"], "2025-06-10", "2025-06-11"
    raw = dataset.load(symbols, start, end, ["close"])["close"].to_numpy().tobytes()
    zstd = pa.Codec("zstd")
    payload = zstd.compress(raw)
    t = perf_counter()
    for _ in range(250):
        zstd.decompress(payload, len(raw))
    seconds = perf_counter() - t
    return {"raw_bytes": len(raw), "compressed_bytes": len(payload), "iterations": 250,
            "seconds": seconds, "decoded_bytes_per_second": 250 * len(raw) / seconds,
            "scope": "ZSTD in-memory codec on observed BTC closes, separate from Parquet decoding"}


def execute(dataset, only_quick):
    library()
    proc = psutil.Process()
    done = Event()
    peak = [proc.memory_info().rss]

    def sample():
        while not done.wait(.1):
            peak[0] = max(peak[0], proc.memory_info().rss)

    thread = Thread(target=sample, daemon=True)
    thread.start()
    beginning, cpu = perf_counter(), sum(proc.cpu_times()[:2])
    levels = [
        ("btc_day", ["BTCUSDT"], "2025-06-10", "2025-06-11"),
        ("liquid50_day", None, "2025-06-10", "2025-06-11"),
        ("liquid50_month", None, "2025-06-01", "2025-07-01"),
        ("liquid50_full_development", None, "2024-11-25", HOLDOUT),
    ][:2 if only_quick else 4]
    results = {}
    try:
        for level, symbols, start, end in levels:
            print(level, flush=True)
            method_timings = []
            if level != "liquid50_full_development":
                for method in ("arrow_parquet_projection", "sorted_arrow", "aligned_arrow"):
                    for repetition in (1, 2):
                        method_timings.append(reader_pass(dataset, symbols, start, end, method) | {"pass": repetition})
            run = stream(dataset, symbols, start, end)
            signal_run = stream(dataset, symbols, start, end, measure_signals=True) if level == "liquid50_day" else None
            results[level] = {"start": start, "end": end, "reader": method_timings, "pipeline": run,
                              "zero_notional_ema_profile": signal_run}
            print(level, run, flush=True)
        jobs = 4
        parallel = []
        for workers in (1, 2, 4):
            tick = perf_counter()
            with ThreadPoolExecutor(workers) as pool:
                tasks = [pool.submit(stream, dataset, None, "2025-06-10", "2025-06-11") for _ in range(jobs)]
                slots = sum(future.result()["slots"] for future in tasks)
            elapsed = perf_counter() - tick
            parallel.append({"workers": workers, "runs": jobs, "slots": slots, "seconds": elapsed,
                             "slots_per_second": slots / elapsed})
        elapsed = perf_counter() - beginning
        return {"levels": results, "codec": codec_test(dataset), "parallel": parallel, "peak_sampled_rss_bytes": peak[0],
                "cpu_core_equivalents": (sum(proc.cpu_times()[:2]) - cpu) / elapsed, "total_seconds": elapsed,
                "cache_notes": "First pass is a process-observed read with uncontrolled OS cache. Second pass is immediate warm reread. No verified cold-cache eviction was performed."}
    finally:
        done.set()
        thread.join()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", type=Path, default=Path("data"))
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--quick", action="store_true")
    args = parser.parse_args()
    d = MarketDataset(args.data / "binance/um/bars_5s", universe="liquid50")
    write_json(args.out, execute(d, args.quick))
