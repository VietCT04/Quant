"""Submit independent inventory hypotheses and promote an unchanged screen."""

import argparse
import json
from pathlib import Path
from time import perf_counter

import quant as q
from quant.control_cli import request
from quant.strategy import prepare


STRATEGY = {"id": "active_pullback_inventory", "version": "1.0.0"}
DESIGNS = (
    ("Long pullbacks within active uptrends", {"side": 1}),
    ("Short pullbacks within active downtrends", {"side": -1}),
    ("Stronger trend and deeper pullbacks", {"trend": .04, "shock": .005, "hold": 240}),
    ("Long shocks with trend and taker confirmation", {"mode": "impulse", "side": 1}),
    ("Short shocks with trend and taker confirmation", {"mode": "impulse", "side": -1}),
)


def main():
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("out", type=Path)
    cli.add_argument("--port", type=int, default=8765)
    cli.add_argument("--promote", nargs=2, metavar=("HYPOTHESIS", "JOB"))
    args = cli.parse_args()
    port = args.port
    full = args.promote is not None
    source = request(port, "/service")["coverage"]["source"]
    selection = q.data.select(q.data.open(source), start="2024-11-25" if full else "2026-05-25", end="2026-11-25")
    protocol = {"start": selection.start, "end": selection.end, "universe": selection.symbols,
                "dataset_fingerprint": q.data.fingerprint(selection), "seed": 514, "costs": {},
                "evaluation": {"research_window": "full_history" if full else "six_month", "origin_job": None,
                               "promotion": "Positive net ROI and Sharpe, with at least 200 closed lifecycles and no material execution failures."}}
    designs = DESIGNS
    if full:
        owner, job_id = args.promote
        job = next(j for j in request(port, f"/hypotheses/{owner}/results")["experiments"] if j["id"] == job_id)
        if job["status"] != "completed":
            raise ValueError("Only a completed screen can be promoted")
        spec = job["specification"]
        resolved = prepare(STRATEGY, spec["parameters"], rebalance_ticks=1)[3]
        if {k: v for k, v in spec["strategy"].items() if k != "warmup_fingerprint"} != resolved:
            raise ValueError("Screen strategy does not match the loaded version")
        designs = ((f"Full-history validation of {owner}", spec["parameters"]),)
        protocol["evaluation"]["origin_job"] = job_id
    rows = []
    for name, params in designs:
        mode = params.get("mode", "pullback")
        thesis = ("After a lagged directional move, an active one-minute pullback offers cheaper inventory in the existing trend."
                  if mode == "pullback" else
                  "An active one-minute shock with confirming taker flow and lagged trend may persist after costs.")
        rows.append({"name": name, "family": "Active-market inventory", "stage": "validation" if full else "simulation",
                     "mechanism": thesis, "prediction": "Positive net equity return with repeated small increments and reductions.",
                     "definition": "Lagged 60-minute trend, one-minute turnover filter and signal, booked inventory, 10 USDT market clips.",
                     "protocol": protocol, "candidates": [{"kind": "native", "strategy": STRATEGY, "parameters": params}]})
    payload = {"campaign": f"Active-market inventory / {protocol['evaluation']['research_window']}", "hypotheses": rows}
    args.out.mkdir(parents=True, exist_ok=False)
    (args.out / "specification.json").write_text(json.dumps(payload, indent=2), encoding="utf-8")
    then = perf_counter()
    receipt = request(port, "/hypotheses", payload) | {"submit_seconds": perf_counter() - then}
    (args.out / "receipt.json").write_text(json.dumps(receipt, indent=2), encoding="utf-8")
    print(json.dumps(receipt), flush=True)


if __name__ == "__main__":
    main()
