"""Verify explorer responses against the original BTCUSDT run and record API timings."""

import argparse
import json
from pathlib import Path
import platform
from threading import Event, Thread
from time import perf_counter

import numpy as np
import polars as pl
import psutil

from quant import data, ts
from quant.control_cli import CLIENT, request


def run(port, hypothesis, out):
    service = request(port, "/service")
    original, = request(port, f"/hypotheses/{hypothesis}/results")["experiments"]
    job = original["id"]
    route = f"/explorer/{job}"
    meta = request(port, route + "/meta")
    spec = original["specification"]
    assert meta["detailed_job"] and meta["symbols"] == ["BTCUSDT"]
    replay, = request(port, f"/hypotheses/{meta['replay']['hypothesis_id']}/results")["experiments"]
    state = original["outcome"]["metrics"]
    detailed = replay["outcome"]["metrics"]
    for field in ("end", "risk", "daily_marked_equity", "per_symbol", "execution_costs"):
        assert state[field] == detailed[field], field
    assert "exact_replay_of" not in replay["outcome"]["execution"] or replay["outcome"]["execution"]["exact_replay_of"] == job
    artifacts = {Path(path).stem: Path(path) for path in replay["outcome"]["artifacts"]}
    series = ("candles?tf=15m", "features?tf=15m", "fills?limit=500",
              "positions?limit=500", "series?tf=15m")
    timings = {}
    results = {}
    process = psutil.Process(service["pid"])
    done = Event()
    baseline = peak = process.memory_info().rss

    def sample_rss():
        nonlocal peak
        while not done.wait(0.025):
            peak = max(peak, process.memory_info().rss)

    sampler = Thread(target=sample_rss)
    sampler.start()
    try:
        for resource in series:
            measurements = []
            for _ in range(3):
                tick = perf_counter()
                with CLIENT.open(f"http://127.0.0.1:{port}/api{route}/{resource}", timeout=20) as response:
                    wire = response.read()
                measurements.append(round((perf_counter() - tick) * 1000, 2))
            results[resource] = json.loads(wire)
            timings[resource] = {"ms": measurements, "bytes": len(wire)}
    finally:
        done.set()
        sampler.join()

    dataset = data.open(service["coverage"]["source"])
    lo, hi = spec["start_ms"], spec["end_ms"]
    candles = results["candles?tf=15m"]["rows"]
    assert len(candles) == 672
    first = next(data.iter_blocks(data.select(dataset, start=lo, end=lo + 900_000,
        symbols=["BTCUSDT"], fields=("open", "high", "low", "close", "volume"))))
    expected = {"open": first.values["open"][0, 0], "high": first.values["high"].max(),
                "low": first.values["low"].min(), "close": first.values["close"][-1, 0],
                "volume": first.values["volume"].sum()}
    for field, value in expected.items():
        np.testing.assert_allclose(candles[0][field], value, rtol=1e-12)
    source = data.select(dataset, start=lo, end=hi, symbols=["BTCUSDT"])
    close = np.concatenate([block.values["close"][:, 0] for block in data.iter_blocks(source)])
    features = results["features?tf=15m"]["series"]
    for field, span in (("fast", 10), ("slow", 30)):
        rows = np.array(features[field])
        assert (rows[:, 3] == rows[:, 2] + 5100).all()
        expected = ts.ema(close, span)[((rows[:, 2] - lo) / 5000).astype(int)]
        np.testing.assert_allclose(rows[:, 1], expected, rtol=1e-13, atol=1e-10)

    ledger = results["fills?limit=500"]["rows"]
    native_fills = pl.read_parquet(artifacts["fills"]).to_dicts()
    assert len(ledger) == len(native_fills) == state["fills"]
    fields = ("ms", "bar_start", "decision", "filled", "price", "after", "entry", "fee", "slippage", "realized")
    assert [{key: row[key] for key in fields} for row in ledger] == [
        {key: row[key] for key in fields} for row in native_fills
    ]
    positions = results["positions?limit=500"]
    for field in ("qty", "realized", "fees", "funding", "slippage"):
        np.testing.assert_allclose(positions["reconciled"][field], state["per_symbol"][0][field], rtol=1e-9, atol=1e-7)
    assert sum(row["state"] == "open" for row in positions["rows"]) == (state["per_symbol"][0]["qty"] != 0)
    assert sum(row["type"] == "reversal" for row in ledger) > 0

    fine = request(port, f"{route}/candles?tf=5s&start={lo}&end={lo + 900_000}")
    assert len(fine["rows"]) == 180
    report = {"machine": platform.platform(), "logical_cpus": service["hardware"]["logical"],
              "usable_cpus": service["hardware"]["usable_cpus"], "dataset": spec["dataset_fingerprint"], "range_ms": [lo, hi],
              "job": job, "detailed_job": meta["detailed_job"], "fills": len(ledger), "positions": positions["total"],
              "closed_positions": sum(row["state"] == "closed" for row in positions["rows"]), "reversals": sum(row["type"] == "reversal" for row in ledger),
              "funding_records": pl.read_parquet(artifacts["funding"]).height, "accounting": positions["reconciled"],
              "api_timings": timings, "server_rss_before_bytes": baseline, "server_rss_peak_sampled_bytes": peak,
              "snapshot_ticks": detailed["settings"]["snapshot_ticks"], "cache_method": "Three equal consecutive requests, OS cache uncontrolled"}
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--hypothesis", default="H-0001")
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    run(args.port, args.hypothesis, args.out)


if __name__ == "__main__":
    main()
