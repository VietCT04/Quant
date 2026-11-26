"""Prepare a fixed development-only control center acceptance submission."""

import argparse
import json
from pathlib import Path

import quant as q


def campaign(start, end):
    selection = q.data.select(q.data.open(), start=start, end=end)
    protocol = {
        "start": start, "end": end, "universe": selection.symbols,
        "dataset_fingerprint": q.data.fingerprint(selection), "seed": 0,
        "features": ["quant.ts.ema"],
        "evaluation": {"purpose": "Phase 4.5 infrastructure acceptance", "horizon": 12, "method": "pearson", "groups": 3},
        "costs": {"rebalance_ticks": 12},
    }
    payload = {"campaign": "Development infrastructure acceptance", "hypotheses": []}
    names = ["Fast span sensitivity", "Medium span sensitivity", "Slow span sensitivity", "Contrarian sign control"]
    for index, name in enumerate(names):
        payload["hypotheses"].append({
            "key": f"screen-{index}", "name": name, "family": "EMA spread", "stage": "screening",
            "mechanism": "A fixed EMA grid exercises sharing across compatible screening hypotheses.",
            "definition": "Score(t) = direction × (EMA_fast(close) − EMA_slow(close)) / close(t)",
            "prediction": "The scheduler commits exactly one screening result for each predeclared candidate.",
            "parameters": {"horizon_bars": 12, "direction": -1 if index == 3 else 1},
            "capabilities": [{"name": "quant.ts.ema", "causal": True, "streaming": True}],
            "protocol": protocol,
            "candidates": [
                {"kind": "screen", "parameters": {"fast": f, "slow": s, "direction": -1 if index == 3 else 1}}
                for f in range(4 + index, 9 + index) for s in range(20, 25)
            ],
        })
    for index, name in enumerate(("Native cost sensitivity", "Native accounting controls")):
        payload["hypotheses"].append({
            "key": f"native-{index}", "name": name, "family": "EMA spread", "stage": "simulation",
            "mechanism": "Fixed signed exposure decisions exercise the existing simulator accounting.",
            "definition": "Target(t) = 300 USDT × sign(EMA_8(close) − EMA_24(close))",
            "prediction": "Each configuration produces a native marked portfolio report.",
            "capabilities": [{"name": "quant.ts.ema", "causal": True, "streaming": True}],
            "depends_on": [f"screen-{index}"],
            "protocol": protocol,
            "candidates": [
                {"kind": "native", "parameters": {"fast": 8, "slow": 24, "settings": {"fee_bps": fee}}}
                for fee in (2, 4, 8, 12)
            ],
        })
    return payload


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("out", type=Path)
    parser.add_argument("--start", default="2026-06-10")
    parser.add_argument("--end", default="2026-06-10T03:00:00Z")
    args = parser.parse_args()
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(campaign(args.start, args.end), indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
