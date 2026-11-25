"""Measure fresh SDK caches, reuse and independent parallel feature runs."""

import argparse
from pathlib import Path
import json
from time import perf_counter

import numpy as np

import quant as q


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", type=Path, required=True)
    options = parser.parse_args()
    options.out.mkdir(parents=True, exist_ok=True)
    decoded_path = options.out / "decoded"
    feature_path = options.out / "feature"
    if decoded_path.exists() or feature_path.exists():
        raise ValueError("Point this benchmark at a fresh output directory")
    d = q.data.select(
        q.data.open(), start="2026-06-10", end="2026-06-11", fields=["close"]
    )
    decoded = q.features.Cache(
        directory=decoded_path, memory_bytes=0, disk_bytes=32 << 20
    )
    features = q.features.Cache(
        directory=feature_path, memory_bytes=0, disk_bytes=32 << 20
    )
    f = q.features.expression(
        "quant.ts.rolling_zscore", "close", window=240, units="dimensionless"
    )
    identity = q.data.fingerprint(d)
    runs = []
    for repeat in range(2):
        timer = perf_counter()
        blocks = q.data.iter_blocks(d, decoded_cache=decoded)
        count = sum(
            np.isfinite(x.values["zscore"]).sum()
            for x in q.features.evaluate(
                blocks,
                {"zscore": f},
                cache=features,
                fingerprint=identity,
                context={"benchmark": 1},
            )
        )
        runs.append(
            dict(
                pass_type="fresh SDK cache" if repeat == 0 else "warm SDK cache",
                seconds=perf_counter() - timer,
                observations=int(count),
                decoded_hits=decoded.hits,
                feature_hits=features.hits,
                cache_bytes=sum(p.stat().st_size for p in options.out.rglob("*.npy")),
            )
        )
    block = next(q.data.iter_blocks(d, block_steps=8192, decoded_cache=decoded))
    close = block.values["close"]
    cases = [24, 36, 48, 60] * 4

    def trial(span):
        return int(np.isfinite(q.ts.ema(close, span)).sum())

    scaling = []
    for workers in (1, 2, 4):
        timer = perf_counter()
        result = list(
            q.optimize.parallel(trial, cases, workers=workers, backend="thread")
        )
        scaling.append(
            dict(
                workers=workers,
                seconds=perf_counter() - timer,
                replays=len(result),
                observations=sum(result),
            )
        )
    report = dict(cache=runs, parallel=scaling, scheduled_symbols=len(d.symbols))
    (options.out / "benchmark.json").write_text(
        json.dumps(report, indent=2), encoding="utf-8"
    )
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
