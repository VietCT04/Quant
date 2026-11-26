"""Submit the V2 directional persistence experiment without waiting for workers."""

import argparse
import json
from pathlib import Path
from time import perf_counter

import quant as q
from quant.control_cli import request


def campaign(source, symbols, full, short=False):
    start, end = ("2024-11-25" if full else "2026-05-25"), "2026-11-25"
    if short:
        start, end = "2026-10-01", "2026-10-04"
    selection = q.data.select(q.data.open(source), symbols=symbols, start=start, end=end)
    window = "full_history" if full else "fixture" if short else "six_month"
    protocol = {"start": selection.start, "end": selection.end, "universe": selection.symbols,
                "dataset_fingerprint": q.data.fingerprint(selection), "seed": 602,
                "evaluation": {"research_window": window, "estimated_order_cadence_ticks": 720},
                "costs": {}}
    return {"campaign": f"V2 directional persistence / {window}", "hypotheses": [{
        "name": "Slow directional persistence with an entry hurdle",
        "stage": "validation" if full else "simulation", "family": "Directional persistence",
        "mechanism": "Persistent net demand can continue after a large directional move over multiple sessions.",
        "definition": "At completed hourly decisions enter on a large trailing return, exit on a move through zero, and otherwise hold actual quantities.",
        "prediction": "Sparse exposure earns positive net ROI and positive daily Sharpe after modeled execution costs.",
        "protocol": protocol,
        "candidates": [{"kind": "native", "strategy": {"id": "slow_directional_persistence", "version": "1.0.0"},
                        "parameters": {"entry": level, "lookback_hours": 48, "allow_short": short_side}}
                       for level, short_side in ((.025, False), (.025, True))],
    }]}


def main():
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("--port", type=int, default=8765)
    cli.add_argument("--symbols", nargs="+", default=None)
    windows = cli.add_mutually_exclusive_group()
    windows.add_argument("--full", action="store_true")
    windows.add_argument("--fixture", action="store_true")
    cli.add_argument("--out", type=Path, required=True)
    args = cli.parse_args()
    source = request(args.port, "/service")["coverage"]["source"]
    specification = campaign(source, args.symbols, args.full, args.fixture)
    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / "specification.json").write_text(json.dumps(specification, indent=2), encoding="utf8")
    started = perf_counter()
    receipt = request(args.port, "/hypotheses", specification)
    value = receipt | {"submission_seconds": perf_counter() - started}
    (args.out / "receipt.json").write_text(json.dumps(value, indent=2), encoding="utf8")
    print(json.dumps(value))


if __name__ == "__main__":
    main()
