"""Comparable legacy vector-path benchmark before and after strategy integration."""

import argparse
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Event, Thread
from time import perf_counter

import numpy as np
import psutil

import quant as q
from simulator.native import Settings, library


def measure(output):
    output.parent.mkdir(parents=True, exist_ok=True)
    selection = q.data.select(
        q.data.open(), start="2026-06-10", end="2026-06-10T03:00:00Z",
        symbols=["BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT"],
    )
    expressions = {
        name: q.features.expression("quant.ts.ema", "close", span=span, units="USDT/base")
        for name, span in (("fast", 10), ("slow", 30))
    }
    configs = [
        q.simulator.Candidate(
            f"c{index}",
            lambda block, index=index: (300 + index) * np.sign(block.values["fast"] - block.values["slow"]),
            expressions, Settings(rebalance_ticks=12),
        )
        for index in range(12)
    ]
    library()
    process = psutil.Process()
    done = Event()
    rss = []

    def sample():
        while not done.wait(.01):
            rss.append(process.memory_info().rss)

    sampler = Thread(target=sample)
    sampler.start()
    passes = []
    try:
        with TemporaryDirectory(dir=output.parent) as scratch:
            for index in range(4):
                started = perf_counter()
                result = q.simulator.run_batch(
                    selection, configs, out=Path(scratch) / str(index),
                    workers=4, cpu_limit=4, memory_budget_gib=2, logging="summary",
                )
                if result.failures:
                    raise RuntimeError(result.failures)
                passes.append((perf_counter() - started, result.profile, [
                    item.report["end"] for item in result.results.values()
                ]))
    finally:
        done.set()
        sampler.join()
    report = {"hardware": q.research.hardware(), "processor": os.environ.get("PROCESSOR_IDENTIFIER"),
              "peak_rss": max(rss, default=process.memory_info().rss), "passes": passes}
    output.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps([{"seconds": elapsed, "bars": profile["observed_bars"]} for elapsed, profile, _ in passes]))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("output", type=Path)
    measure(parser.parse_args().output)
