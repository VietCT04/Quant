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
FAST = BREAKOUT | {"version": "3.0.0"}
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
FREQUENCY_BASE = BASE | dict(fraction=.30, alignment=.01, market_floor=-.003)
FREQUENCY_VARIANTS = [
    ("Three strength slots", dict(slots=3, fraction=.26), "Allow a third qualifying shock without relaxing entry quality."),
    ("Four strength slots", dict(slots=4, fraction=.22), "Reduce single-name sizing and admit overlapping qualifying shocks."),
    ("Six strength slots", dict(slots=6, fraction=.15), "Spread overlapping shocks across six smaller positions."),
    ("Strength with 6% shocks", dict(depth=.06, slots=3, fraction=.26), "A modestly lower absolute hurdle admits more relative outliers."),
    ("Strength with 5% shocks", dict(depth=.05, slots=4, fraction=.22), "Keep the relative surprise filter as the absolute hurdle falls."),
    ("Stronger trend with 5% shocks", dict(depth=.05, alignment=.04, slots=4, fraction=.22),
     "A stronger prior trend can preserve signal quality when admitting smaller shocks."),
    ("Stronger relative surprise", dict(depth=.05, sigma=4., slots=4, fraction=.22),
     "Use larger volatility-scaled surprises to qualify more moderate absolute moves."),
    ("Faster capital recycling", dict(hold=96, slots=3, fraction=.26),
     "A four-day expiry frees capacity for fresh shocks."),
    ("Quicker trailing exit", dict(trail=.095, slots=3, fraction=.26, depth=.06),
     "Combining a lower shock threshold with a closer trail can reuse capital without waiting for expiry."),
    ("Moderate shocks in a calm market", dict(depth=.045, sigma=3., alignment=.025, market_floor=0., slots=4, fraction=.22),
     "A nonnegative median hourly market and positive prior trend may filter weaker shocks."),
]
FAST_VARIANTS = [
    ("Half-hour strength, 5% shock", dict(depth=.05, fraction=.23, slots=4),
     "Earlier observations can admit shocks missed at the hourly boundary."),
    ("Half-hour strength, 4.2% shock", dict(depth=.042, fraction=.23, slots=4),
     "The same 24-hour prior-strength filter may protect smaller half-hour shocks."),
    ("Half-hour strength, larger surprise", dict(depth=.045, sigma=4., fraction=.27, slots=3),
     "Require a stronger volatility-adjusted move when observing earlier."),
    ("Half-hour strength, more slots", dict(depth=.045, fraction=.16, slots=5, hold=120),
     "Wider inventory coverage and shorter expiry leave room for new shocks."),
    ("Half-hour strength, faster recycling", dict(depth=.045, fraction=.23, slots=4, hold=96, trail=.095),
     "A shorter holding limit and closer trail release capital for new qualifying shocks."),
]
QUALITY_BASE = FREQUENCY_BASE | dict(depth=.05, alignment=.04, slots=4, fraction=.22)
QUALITY_VARIANTS = [
    ("Stronger prior trend", dict(alignment=.07),
     "A modestly stronger pre-shock trend can remove weak breakouts while preserving trade count."),
    ("Quiet market, strong trend", dict(market_floor=0.),
     "Weak broad markets may explain losses from smaller qualifying shocks."),
    ("Allow moderate market pullbacks", dict(market_floor=-.006, alignment=.06),
     "Persistent prior demand may support a larger set of shocks during an ordinary market pullback."),
    ("Stronger shock surprise with strength", dict(sigma=4.0),
     "A higher volatility-adjusted hurdle can retain the most informative 5% shocks."),
    ("Moderate surprises with strong trend", dict(sigma=3., alignment=.07),
     "Prior strength can substitute for a higher short-window shock score."),
    ("Smaller strength entries", dict(fraction=.17, slots=5),
     "Reduce single-name volatility contribution while reserving capacity for more positions."),
    ("Longer time for a qualified move", dict(hold=216, trail=.135, fraction=.20),
     "Emphasize prior strength and give winners more time to compound."),
    ("Stronger trend with shorter hold", dict(hold=120, alignment=.055, trail=.11),
     "Faster capital recycling can preserve count while a tighter trend gate filters the weaker entries."),
]


def submit(port, output, promote, *, managed=False, frequency=False, fast=False, quality=False):
    strategy = FAST if fast else MANAGED if managed else BREAKOUT
    dataset = q.data.open(request(port, "/service")["coverage"]["source"])
    screen = q.data.select(dataset, start=SCREEN[0], end=SCREEN[1])
    selected = q.data.select(dataset, start=FULL[0], end=FULL[1]) if promote else screen
    if promote:
        items = []
        fingerprint = q.data.fingerprint(screen)
        for key in promote:
            h = request(port, f"/hypotheses/{key}/results")
            job, = h["experiments"]
            spec = job["specification"]
            frozen = spec["strategy"]
            if job["status"] != "completed" or spec["dataset_fingerprint"] != fingerprint:
                raise ValueError(f"{key} does not identify a completed screen on this dataset")
            if (spec["seed"] != 716 or spec["costs"] or tuple(spec["universe"]) != screen.symbols
                    or not spec["evaluation"].get("sharpe_tradeoff")):
                raise ValueError(f"{key} does not match the recorded research settings")
            resolved = prepare(strategy, spec["parameters"], rebalance_ticks=1)[3]
            if resolved != {k: v for k, v in frozen.items() if k != "warmup_fingerprint"}:
                raise ValueError(f"{key} has incompatible frozen strategy metadata")
            items.append((h["name"], spec["parameters"], f"Measure the unchanged {key} policy over the full archive.", job["id"]))
    else:
        base = QUALITY_BASE if quality else FREQUENCY_BASE if frequency or fast else BASE
        variants = (QUALITY_VARIANTS if quality else FAST_VARIANTS if fast else
                    FREQUENCY_VARIANTS if frequency else MANAGED_VARIANTS if managed else VARIANTS)
        items = [(name, base | changes, mechanism, None) for name, changes, mechanism in variants]
    protocol = dict(start=selected.start, end=selected.end, universe=selected.symbols,
                    dataset_fingerprint=q.data.fingerprint(selected), seed=716, costs={})
    hypotheses = [
        dict(name=name, family="Shock repricing", stage="simulation", mechanism=mechanism, prediction=mechanism,
             definition="Vary concentration, trailing exits and holding time of the frozen upside shock policy.",
             protocol=protocol | {"evaluation": {"research_window": "full_history" if promote else "six_month",
                                                "sharpe_tradeoff": True,
                                                "baseline_hypothesis": "H-0110" if frequency or fast or quality else "H-0097",
                                                "origin_job": job}},
             candidates=[{"kind": "native", "strategy": strategy, "parameters": params}])
        for name, params, mechanism, job in items
    ]
    tag = ("shock quality" if quality else "half-hour" if fast else
           "frequency" if frequency else "managed" if managed else "fixed")
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
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--managed", action="store_true")
    mode.add_argument("--frequency", action="store_true")
    mode.add_argument("--fast", action="store_true")
    mode.add_argument("--quality", action="store_true")
    args = parser.parse_args()
    print(json.dumps(submit(args.port, args.out, args.promote, managed=args.managed,
                            frequency=args.frequency, fast=args.fast, quality=args.quality)), flush=True)


if __name__ == "__main__":
    main()
