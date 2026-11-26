"""Submit preregistered shock inventory variants to the native research queue."""

import argparse
import json
from pathlib import Path
from time import perf_counter

import quant as q
from quant.control_cli import request
from quant.strategy import prepare


SCREEN = ("2026-05-25", "2026-11-25")
FULL = ("2024-11-25", "2026-11-25")
STRATEGY = {"id": "shock_inventory_recovery", "version": "2.0.0"}
LIQUIDITY = {"id": "shock_liquidity_recovery", "version": "1.0.0"}
CONTEXT = LIQUIDITY | {"version": "2.0.0"}
COMMON = dict(cadence=24, order_usdt=10, depth=.024, minimum_gap=.016,
              patient_minutes=18, hold_minutes=720, step=.004, retrace=.50, stop=2.5)
FAMILIES = {
    "Pressure": ("flow", 1, "Abnormal aggressive selling combined with a wide price dislocation predicts relief."),
    "Isolated": ("residual", 1, "A large coin-specific markdown unwinds after separating it from market pressure."),
    "Decelerating": ("residual_flow", 1, "Isolated sell pressure can become exhausted while price is still falling."),
    "Upside": ("residual", -1, "An isolated upside overshoot can retrace after a wider dislocation."),
}
PROFILES = {
    "wide": {},
    "extreme": dict(depth=.045, minimum_gap=.028, sigma=3.0, stop=2),
    "patient": dict(cadence=36, patient_minutes=30, hold_minutes=1440, retrace=.68, market_limit=.004),
    "early": dict(cadence=6, depth=.032, minimum_gap=.024, patient_minutes=12, retrace=.42, add_minutes=25),
}


def screening_candidates():
    for family, (mechanism, side, prediction) in FAMILIES.items():
        for profile, overrides in PROFILES.items():
            yield (f"{family} / {profile}", prediction,
                   COMMON | overrides | dict(mechanism=mechanism, side=side), STRATEGY, None)


def liquidity_candidates():
    mechanisms = [
        ("Isolated downside", 1, "isolated", "Deep idiosyncratic markdowns amid a stable market recover over hours."),
        ("Market-wide downside", 1, "systemic", "Broad forced selling temporarily discounts the more liquid contracts."),
        ("Isolated upside", -1, "isolated", "Isolated upside dislocations from a quiet prior trend can unwind."),
    ]
    profiles = [
        ("half recovery", dict(depth=.026, gap=.030, hold=1440)),
        ("extreme dislocation", dict(depth=.045, gap=.045, recovery=.65, hold=2880, turnover=650)),
        ("faster accumulation", dict(depth=.022, gap=.026, cadence=24, add_minutes=60, hold=720, recovery=.60)),
    ]
    for name, side, market, prediction in mechanisms:
        for profile, values in profiles:
            yield f"{name} / {profile}", prediction, values | dict(side=side, market=market), LIQUIDITY, None


def followup_candidates():
    start = COMMON | PROFILES["extreme"] | dict(mechanism="residual", side=1)
    variants = [
        ("Isolated deep / wait for larger rebound", "Avoid small fee-heavy exits before a large fraction of displacement clears.",
         dict(retrace=.78, patient_minutes=30, stop=3.5, step=.009)),
        ("Isolated deep / scope to quiet tape", "In stable broad markets, a rapid isolated repricing is more likely to reverse.",
         dict(market_limit=.002, depth=.038, minimum_gap=.026, retrace=.65)),
        ("Isolated deep / veto additional adverse inventory", "An initial isolated break can recover while doubling down on persistence destroys the edge.",
         dict(inventory=10, depth=.04, retrace=.65, stop=3)),
        ("Isolated deep / delayed verdict", "Resolve a temporary dislocation over a day with looser stop and less reactive cuts.",
         dict(hold_minutes=1440, stop=4, patient_minutes=30, retrace=.65)),
        ("Isolated deep / rescue tail", "Dislocations above seven percent may attract more corrective demand after fees.",
         dict(depth=.07, minimum_gap=.04, sigma=3.5, stop=2, hold_minutes=1440, retrace=.55)),
        ("Decelerating deep / less selective", "The rare positive flow case needs more distinct events to test the exhaustion mechanism.",
         dict(mechanism="residual_flow", depth=.034, minimum_gap=.023, sigma=2.0, hold_minutes=1440)),
    ]
    for name, prediction, values in variants:
        yield name, prediction, start | values, STRATEGY, None


def context_candidates():
    prediction = "A large markdown inside an already observable uptrend predicts temporary pressure and later recovery."
    variants = [
        ("Isolated pullback in a daily uptrend", dict(market="isolated", context_hours=24, context_min=.025)),
        ("Broad selloff in a daily uptrend", dict(market="systemic", context_hours=24, context_min=.025)),
        ("Broad deeper shock inside two-day trend", dict(market="systemic", context_hours=48, context_min=.05,
                                                         depth=.035, gap=.038, hold=2160)),
        ("Isolated shallower shock inside two-day trend", dict(market="isolated", context_hours=48, context_min=.04,
                                                              depth=.020, gap=.022, hold=720)),
    ]
    for name, overrides in variants:
        yield name, prediction, dict(side=1, cadence=60, turnover=500) | overrides, CONTEXT, None


def tail_candidates():
    baseline = COMMON | PROFILES["extreme"] | dict(mechanism="residual", side=1)
    choices = [
        ("Extreme isolated / fewer adds", "One additional leg limits tail exposure while retaining inventory accumulation.",
         dict(depth=.05, minimum_gap=.031, inventory=20, step=.008, retrace=.65, stop=3)),
        ("Extreme isolated / insensitive to brief rebound", "A longer recovery window captures slow relinking after a deep markdown.",
         dict(depth=.060, minimum_gap=.036, inventory=30, retrace=.72, patient_minutes=30, hold_minutes=1440)),
        ("Deep isolated / no-add comparison", "At the shock depth associated with better results, a seed-only policy reduces exposure to failed shocks.",
         dict(depth=.055, minimum_gap=.030, inventory=10, retrace=.65, stop=3)),
    ]
    for name, prediction, values in choices:
        yield name, prediction, baseline | values, STRATEGY, None


def promoted_candidates(port, ids, screening_selection):
    fingerprint = q.data.fingerprint(screening_selection)
    for hypothesis in ids:
        for trial in request(port, f"/hypotheses/{hypothesis}/results")["experiments"]:
            if trial["status"] != "completed":
                continue
            metrics = trial["outcome"].get("metrics", {})
            if (metrics.get("sharpe") or 0) < 2 or (metrics.get("roi") or 0) <= 0:
                continue
            spec = trial["specification"]
            strategy = {key: spec["strategy"][key] for key in ("id", "version")}
            if (strategy not in (STRATEGY, LIQUIDITY, CONTEXT) or spec["seed"] != 716 or spec["costs"]
                    or (spec["start_ms"], spec["end_ms"]) != (screening_selection.start, screening_selection.end)
                    or tuple(spec["universe"]) != screening_selection.symbols
                    or spec["dataset_fingerprint"] != fingerprint):
                raise ValueError(f"{hypothesis} has incompatible screening settings")
            fresh = prepare(strategy, spec["parameters"], rebalance_ticks=1)[3]
            original = {key: value for key, value in spec["strategy"].items() if key != "warmup_fingerprint"}
            if fresh != original:
                raise ValueError(f"{hypothesis} strategy or execution runtime differs from the screened copy")
            yield (f"Full history / {hypothesis}", "The frozen screening policy remains profitable across the archive.",
                   spec["parameters"], strategy, trial["id"])


def submit(port, output, promotion=(), *, liquidity=False, followup=False, context=False, tail=False):
    dates = FULL if promotion else SCREEN
    source = request(port, "/service")["coverage"]["source"]
    dataset = q.data.open(source)
    selection = q.data.select(dataset, start=dates[0], end=dates[1])
    protocol = dict(start=selection.start, end=selection.end, universe=selection.symbols,
                    dataset_fingerprint=q.data.fingerprint(selection), seed=716, costs={})
    candidates = (promoted_candidates(port, promotion, q.data.select(dataset, start=SCREEN[0], end=SCREEN[1])) if promotion else
                  liquidity_candidates() if liquidity else
                  context_candidates() if context else
                  tail_candidates() if tail else
                  followup_candidates() if followup else screening_candidates())
    hypotheses = [
        dict(name=name, family="Shock inventory", stage="simulation", mechanism=prediction, prediction=prediction,
             definition="Use 10 USDT orders on the patient shock inventory policy with causal adverse additions and recovery reductions.",
             protocol=protocol | {"evaluation": {
                 "research_window": "full_history" if promotion else "six_month",
                 "origin_job": origin, "promotion_minimum_net_sharpe": 2.0, "promotion_requires_positive_roi": True,
             }}, candidates=[{"kind": "native", "strategy": strategy, "parameters": parameters}])
        for name, prediction, parameters, strategy, origin in candidates
    ]
    if not hypotheses:
        raise ValueError("No completed candidate meets the recorded promotion gate")
    stage = ("full archive" if promotion else "frozen reference round" if liquidity else
             "directional context round" if context else
             "inventory depth round" if tail else
             "isolated displacement follow-up" if followup else "wide displacement round")
    envelope = {"campaign": "Shock / Sharpe 2 / " + stage,
                "hypotheses": hypotheses}
    output.mkdir(parents=True, exist_ok=False)
    (output / "spec.json").write_text(json.dumps(envelope, indent=2), encoding="utf8")
    started = perf_counter()
    receipt = request(port, "/hypotheses", envelope) | {"submit_seconds": perf_counter() - started}
    (output / "receipt.json").write_text(json.dumps(receipt, indent=2), encoding="utf8")
    return receipt


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--out", type=Path, required=True)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--promote", nargs="+", default=[])
    mode.add_argument("--liquidity", action="store_true")
    mode.add_argument("--followup", action="store_true")
    mode.add_argument("--context", action="store_true")
    mode.add_argument("--tail", action="store_true")
    args = parser.parse_args()
    print(json.dumps(submit(args.port, args.out, args.promote, liquidity=args.liquidity,
                            followup=args.followup, context=args.context, tail=args.tail)), flush=True)


if __name__ == "__main__":
    main()
