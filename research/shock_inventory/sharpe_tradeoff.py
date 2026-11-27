"""Test drawdown and return trade-offs around H-0097 through the native queue."""

import argparse
import json
from pathlib import Path

import quant as q
from quant.control_cli import request
from quant.strategy import prepare

from .sharpe_search import BREAKOUT, FULL, SCREEN


BASE = dict(depth=.07, sigma=3.5, trail=.12, hold=168, slots=2, fraction=.47)
MANAGED = BREAKOUT | {"version": "2.0.0"}
VARIANTS = [
    ("Reserve cash", dict(fraction=.28), "Smaller allocations limit damage during abrupt reversals."),
    ("Larger cash reserve", dict(fraction=.18), "Lower exposure reduces the impact of the most volatile names."),
    ("Spread shock inventory", dict(slots=4, fraction=.21), "Extra slots spread exposure across qualifying names."),
    ("Tighter reversal exit", dict(trail=.075, fraction=.30), "Earlier exits preserve more of realized repricing."),
    ("Shorter shock exposure", dict(hold=96, trail=.09, fraction=.30), "Less time in expired shocks limits giveback."),
    ("Broader shock basket", dict(depth=.052, sigma=3., slots=4, fraction=.22),
     "More qualifying positions can reduce dependence on rare price events."),
    ("Require prior relative strength", dict(alignment=.01, market_floor=-.003, fraction=.30),
     "Pre-shock strength and a calmer market reduce false breakouts."),
    ("Spread exposure and shorten holds", dict(slots=4, fraction=.20, depth=.055, hold=120, trail=.095),
     "More varied entries and less time in faded shocks can improve risk-adjusted returns."),
]
MANAGED_VARIANTS = [
    ("Volatility budget", dict(fraction=.36, slots=3, vol_target=.018),
     "Smaller entries in volatile contracts can reduce the contribution of one shock to daily returns."),
    ("Drawdown budget", dict(drawdown_sensitivity=1.8),
     "Sizing new positions down after booked equity losses can limit clustered drawdowns."),
    ("Volatility and drawdown budgets", dict(fraction=.36, slots=3, vol_target=.026, drawdown_sensitivity=1.4),
     "Volatility-sensitive allocations and a drawdown response can protect downside while retaining upside."),
    ("Observable volume budget", dict(fraction=.33, slots=3, volume_budget=.25),
     "Capping new orders at a fraction of recent mean traded notional can reduce concentration and oversized fills."),
]


def submit(port, output, promote, *, managed=False):
    strategy = MANAGED if managed else BREAKOUT
    dataset = q.data.open(request(port, "/service")["coverage"]["source"])
    screen = q.data.select(dataset, start=SCREEN[0], end=SCREEN[1])
    selected = q.data.select(dataset, start=FULL[0], end=FULL[1]) if promote else screen
    if promote:
        items = []
        for key in promote:
            h = request(port, f"/hypotheses/{key}/results")
            job, = h["experiments"]
            spec = job["specification"]
            frozen = spec["strategy"]
            if job["status"] != "completed" or spec["dataset_fingerprint"] != q.data.fingerprint(screen):
                raise ValueError(f"{key} does not identify a completed screen on this dataset")
            if (spec["seed"] != 716 or spec["costs"] or tuple(spec["universe"]) != screen.symbols
                    or not spec["evaluation"].get("sharpe_tradeoff")):
                raise ValueError(f"{key} does not match the recorded research settings")
            resolved = prepare(strategy, spec["parameters"], rebalance_ticks=1)[3]
            if resolved != {k: v for k, v in frozen.items() if k != "warmup_fingerprint"}:
                raise ValueError(f"{key} has incompatible frozen strategy metadata")
            items.append((h["name"], spec["parameters"], f"Measure the unchanged {key} policy over the full archive.", job["id"]))
    else:
        variants = MANAGED_VARIANTS if managed else VARIANTS
        items = [(name, BASE | changes, mechanism, None) for name, changes, mechanism in variants]
    protocol = dict(start=selected.start, end=selected.end, universe=selected.symbols,
                    dataset_fingerprint=q.data.fingerprint(selected), seed=716, costs={})
    hypotheses = [
        dict(name=name, family="Shock repricing", stage="simulation", mechanism=mechanism, prediction=mechanism,
             definition="Vary concentration, trailing exits and holding time of the frozen upside shock policy.",
             protocol=protocol | {"evaluation": {"research_window": "full_history" if promote else "six_month",
                                                "sharpe_tradeoff": True, "baseline_hypothesis": "H-0097",
                                                "origin_job": job}},
             candidates=[{"kind": "native", "strategy": strategy, "parameters": params}])
        for name, params, mechanism, job in items
    ]
    tag = "managed" if managed else "fixed"
    spec = {"campaign": f"Shock / Sharpe trade-off / {tag} / " + ("full" if promote else "screen"),
            "hypotheses": hypotheses}
    output.mkdir(parents=True, exist_ok=False)
    (output / "spec.json").write_text(json.dumps(spec, indent=2), encoding="utf8")
    receipt = request(port, "/hypotheses", spec)
    (output / "receipt.json").write_text(json.dumps(receipt, indent=2), encoding="utf8")
    return receipt


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--promote", nargs="+", default=[])
    parser.add_argument("--managed", action="store_true")
    args = parser.parse_args()
    print(json.dumps(submit(args.port, args.out, args.promote, managed=args.managed)), flush=True)


if __name__ == "__main__":
    main()
