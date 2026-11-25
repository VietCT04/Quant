"""Benchmark representative bounded SDK work on the local development dataset."""

import argparse
import json
from pathlib import Path
import platform
from time import perf_counter
import threading

import numpy as np
import psutil

import quant as q


def measure(dataset, start, end, symbols):
    selection = q.data.select(
        dataset, start=start, end=end, symbols=symbols, fields=("close", "volume")
    )
    process = psutil.Process()
    stop = threading.Event()
    peak = [process.memory_info().rss]

    def sample():
        while not stop.wait(0.1):
            peak[0] = max(peak[0], process.memory_info().rss)

    sampler = threading.Thread(target=sample)
    sampler.start()
    count = cells = 0
    source = features = 0.0
    states = (q.ts.EMAState(60), q.ts.EMAState(240))
    iterator = q.data.iter_blocks(selection, block_steps=8192)
    wall = perf_counter()
    try:
        while True:
            clock = perf_counter()
            block = next(iterator, None)
            source += perf_counter() - clock
            if block is None:
                break
            clock = perf_counter()
            close = block.values["close"]
            difference = states[0].update(close) - states[1].update(close)
            features += perf_counter() - clock
            count += np.isfinite(difference).sum()
            cells += difference.size
    finally:
        stop.set()
        sampler.join()
    elapsed = perf_counter() - wall
    return dict(
        first_open_or_repeat="filesystem cache is uncontrolled",
        valid_features=int(count),
        scheduled_cells=int(cells),
        read_seconds=source,
        feature_seconds=features,
        wall_seconds=elapsed,
        peak_rss_bytes=peak[0],
        cells_per_read_second=cells / source,
        cells_per_feature_second=cells / features,
    )


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--skip-full", action="store_true")
    options = parser.parse_args()
    ds = q.data.open()
    cases = [
        ("one_day", "2025-01-01", "2025-01-02", ["BTCUSDT"]),
        ("all_day", "2026-06-10", "2026-06-11", None),
        ("all_month", "2026-06-01", "2026-07-01", None),
    ]
    if not options.skip_full:
        cases.append(("full_development", "2024-11-25", "2026-08-25", None))
    results = {"platform": platform.platform(), "cpus": psutil.cpu_count(), "runs": {}}
    options.out.parent.mkdir(parents=True, exist_ok=True)
    for name, start, end, symbols in cases:
        rows = []
        for _ in range(1 if name == "full_development" else 2):
            row = measure(ds, start, end, symbols)
            rows.append(row)
            print(name, row, flush=True)
            results["runs"][name] = rows
            options.out.write_text(json.dumps(results, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
