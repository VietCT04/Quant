"""Submit native inventory research and diagnostic windows through the existing queue."""

import argparse
import json
from pathlib import Path
from time import perf_counter

import quant as q
from quant.control_cli import request


VERS = {"id": "shock_inventory_recovery", "version": "1.0.0"}
VARIANTS = {
    "patient": [
        ("Patient absolute repricing", "A wider shock and slower recovery distribution can reduce two-minute churn.",
         {"mechanism": "velocity", "cadence": 12, "depth": .014, "retrace": .40, "hold_minutes": 360}),
        ("Patient idiosyncratic flow exhaustion", "Decelerating aggressive pressure during a still-adverse move offers a wider retracement.",
         {"mechanism": "residual_flow", "cadence": 12, "depth": .012, "retrace": .34,
          "hold_minutes": 360, "dynamic": True}),
        ("Patient isolated short", "An extreme upside dislocation can retrace after a wider entry hurdle.",
         {"mechanism": "residual", "side": -1, "cadence": 12, "depth": .014,
          "retrace": .40, "hold_minutes": 360}),
    ],
    "initial": [
        ("Absolute accelerating decline", "Acceleration after a fast, large downside move predicts a partial recovery.", {"mechanism": "velocity"}),
        ("Coin-specific decline", "An idiosyncratic decline without a comparable market selloff is more likely to reverse.", {"mechanism": "residual"}),
        ("Aggressive sell burst", "Unusually heavy aggressive selling produces temporary price pressure.", {"mechanism": "flow"}),
        ("Coin-specific upward shock", "An isolated upside shock can retrace under a short inventory policy.", {"mechanism": "residual", "side": -1}),
    ],
}


def payload(source, group, dates, *, origin=None, symbols=None):
    selection = q.data.select(q.data.open(source), start=dates[0], end=dates[1], symbols=symbols)
    if origin:
        if origin["status"] != "completed":
            raise ValueError("Choose a completed origin job")
        spec = origin["specification"]
        strategy = {k: spec["strategy"][k] for k in ("id", "version")}
        variants = [(group, "Inspect the specified hypothesis over the stated range.", spec["parameters"])]
    else:
        strategy = VERS | {"version": "2.0.0"} if group == "patient" else VERS
        variants = VARIANTS[group]
    return {"campaign": f"Shock inventory / {group}", "hypotheses": [
        {"name": name, "family": "Shock inventory", "stage": "simulation",
         "mechanism": prediction, "prediction": prediction,
         "definition": "Seed and add on successive adverse developments. Distribute on a causal retracement, then close on fair value, invalidation, or expiry.",
         "protocol": {"start": selection.start, "end": selection.end, "universe": selection.symbols,
                      "dataset_fingerprint": q.data.fingerprint(selection), "seed": 716,
                      "evaluation": {"research_window": "full_history" if dates[0] == "2024-11-25" else
                                     "six_month" if dates[0] == "2026-05-25" else "diagnostic",
                                     "origin_job": origin["id"] if origin else None}, "costs": {}},
         "candidates": [{"kind": "native", "strategy": strategy, "parameters": parameters}]}
        for name, prediction, parameters in variants
    ]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("group")
    parser.add_argument("--port", type=int, default=8791)
    parser.add_argument("--date", nargs=2, default=("2026-05-25", "2026-11-25"))
    parser.add_argument("--origin", nargs=2)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    port = args.port
    server = request(port, "/service")
    origin = None
    if args.origin:
        owner, job = args.origin
        origin = next(j for j in request(port, f"/hypotheses/{owner}/results")["experiments"] if j["id"] == job)
    spec = payload(server["coverage"]["source"], args.group, args.date, origin=origin)
    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / "spec.json").write_text(json.dumps(spec, indent=2), encoding="utf8")
    start = perf_counter()
    receipt = request(port, "/hypotheses", spec) | {"submit_seconds": perf_counter() - start}
    (args.out / "receipt.json").write_text(json.dumps(receipt, indent=2), encoding="utf8")
    print(json.dumps(receipt), flush=True)


if __name__ == "__main__":
    main()
