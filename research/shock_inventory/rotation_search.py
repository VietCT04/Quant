"""Run one frozen wave of the broader 3.00 Sharpe search."""

import argparse
import asyncio
import json
from pathlib import Path

import quant as q
from quant.control_cli import request
from quant.strategy import prepare

from research.phase5.review import wait_for_outcomes
from .position_spread import outcome
from .sharpe_search import FULL, SCREEN


STRATEGY = {"id": "peer_rotation_persistence", "version": "1.0.0"}
RECLAIM = {"id": "failed_range_reclaim", "version": "1.0.0"}
REFERENCE_ROLL = {"id": "displayed_return_roll", "version": "1.0.0"}
ADAPTIVE_PEER = {"id": "adaptive_peer_spread", "version": "1.0.0"}
MARK_REVERSION = {"id": "perpetual_mark_reversion", "version": "1.0.0"}
CANDIDATES = [
    ("Relative momentum, wider exit band", dict(hold=48, slots=6)),
    ("Relative momentum, more peers", dict(hold=36, enter=.79, keep=.55, slots=8, fraction=.075)),
    ("Relative momentum, signed inventory", dict(short=True, slots=6, fraction=.065, hold=48)),
    ("Relative momentum, slower winners", dict(hold=72, keep=.53, enter=.80, slots=8, fraction=.075)),
    ("Relative reversal, narrow dislocations", dict(mode="reversal", hold=24, shock_cap=.06,
                                                     minimum_score=.7, enter=.82, slots=7)),
    ("Relative reversal, signed inventory", dict(mode="reversal", short=True, hold=24, shock_cap=.07,
                                                   minimum_score=.8, fraction=.065, slots=6)),
    ("Trend pullback, long only", dict(mode="pullback", hold=30, minimum_score=1., enter=.78, slots=8, fraction=.075)),
    ("Trend pullback, signed inventory", dict(mode="pullback", short=True, hold=36, minimum_score=1.4,
                                                enter=.82, fraction=.065, slots=6)),
]
REASONS = {
    "momentum": "Persistent relative demand can extend beyond a single hourly shock.",
    "reversal": "Moderate peer dislocations without a large daily drift can mean revert after costs.",
    "pullback": "A countertrend move in a persistent relative trend may offer a cheaper entry.",
}
RECLAIM_CANDIDATES = [
    ("Daily range reclaim, long", {}),
    ("Daily range reclaim, both directions", dict(short=True, fraction=.07)),
    ("Half-day reclaim", dict(lookback=12, probe=.005, target_min=.009, hold=14, fraction=.085)),
    ("Half-day reclaim, quick recovery", dict(lookback=12, probe=.004, target_min=.005, hold=12,
                                               quick=True, short=True, fraction=.065)),
    ("Two-day boundary with larger probe", dict(lookback=48, probe=.014, target_min=.018, hold=36)),
    ("Daily range, avoid persistent trend", dict(probe=.006, drift_cap=.04, hold=24, short=True, fraction=.07)),
    ("Daily range, tighter invalidation", dict(probe=.006, loss_cap=.024, target_min=.012, hold=20)),
    ("Daily range, larger recovery room", dict(probe=.006, target_min=.022, hold=36, chase=.012)),
]
ROLL_CANDIDATES = [
    ("Display roll, signed inventory", {}),
    ("Display roll, rotate long only", dict(short=False, slots=6, fraction=.12)),
    ("Display roll, large expiring hour", dict(depth=.035, score=2.15, hold=3)),
    ("Display roll, liquid contracts", dict(liquidity=2_000_000., depth=.018, hold=2)),
    ("Display roll, longer impact", dict(hold=4, depth=.028, score=2., adverse=.020)),
    ("Display roll, one-hour response", dict(hold=1, depth=.022, score=1.9)),
    ("Display roll, earlier-hour comparison", dict(lag_hours=22)),
]
ADAPTIVE_CANDIDATES = [
    ("Adaptive 12-hour peer rotation", {}),
    ("Adaptive shorter horizon", dict(horizon=3, hold=6, edge=.003, decay_days=18)),
    ("Adaptive one-day peer spread", dict(horizon=12, hold=16, edge=.006, decay_days=32)),
    ("Adaptive slower weights", dict(decay_days=48, ridge=1.0, hold=10, edge=.0035, fraction=.075)),
    ("Adaptive fast response", dict(decay_days=12, ridge=.8, hold=8, edge=.0045)),
    ("Adaptive signed positions", dict(short=True, ridge=.8, hold=10, edge=.0055, fraction=.065)),
]
MARK_CANDIDATES = [
    ("Perpetual mark gap, signed inventory", {}),
    ("Perpetual mark gap, long only", dict(direction=1)),
    ("Perpetual mark gap, short only", dict(direction=-1)),
    ("Perpetual mark gap, smaller fresh deviations", dict(gap=.003, max_mark_move=.0015, min_quote=25_000., fraction=.035)),
    ("Perpetual mark gap, deeper dislocations", dict(gap=.010, max_mark_move=.006, hold=35, fraction=.05)),
    ("Perpetual mark gap, either clock phase", dict(fresh_only=False, gap=.0065, min_quote=30_000., fraction=.05)),
]


def run_wave(port, folder, candidates, *, full=False, mode="peer"):
    dataset = q.data.open(request(port, "/service")["coverage"]["source"])
    start, end = FULL if full else SCREEN
    selection = q.data.select(dataset, start=start, end=end)
    fingerprint = q.data.fingerprint(selection)
    reclaim_mechanism = "A completed reclaim after a failed range probe can precede a sustained move back into the range."
    roll_mechanism = "An expiring hourly return changes the displayed daily return and may shift near-term demand."
    adaptive_mechanism = "A model trained on matured price changes can adapt to time-varying relative return patterns."
    mark_mechanism = "A large trade-price deviation from a recently published perpetual mark may close when mark movement and recent trend are modest."
    family, selector, description = {
        "peer": ("Peer rotation", STRATEGY, None),
        "reclaim": ("Range reclaim", RECLAIM, reclaim_mechanism),
        "reference": ("Displayed return roll", REFERENCE_ROLL, roll_mechanism),
        "adaptive": ("Adaptive peer forecast", ADAPTIVE_PEER, adaptive_mechanism),
        "mark": ("Perpetual mark dislocation", MARK_REVERSION, mark_mechanism),
    }[mode]
    def mechanism(params):
        return description or REASONS[params.get("mode", "momentum")]
    items = [
        dict(name=name, family=family, stage="simulation", mechanism=mechanism(params), prediction=mechanism(params),
             definition="Use completed bars and explicit booked entries and exits.",
             protocol=dict(start=selection.start, end=selection.end, universe=selection.symbols,
                           dataset_fingerprint=fingerprint, seed=716, costs={},
                           evaluation=dict(research_window="full_history" if full else "six_month",
                                           minimum_net_sharpe=3., minimum_closed_positions=1001, origin_job=origin)),
             candidates=[{"kind": "native", "strategy": selector, "parameters": params}])
        for name, params, origin in candidates
    ]
    envelope = {"campaign": f"3 Sharpe search / {family} / " + ("full" if full else "screen"), "hypotheses": items}
    folder.mkdir(parents=True, exist_ok=False)
    (folder / "spec.json").write_text(json.dumps(envelope, indent=2), encoding="utf8")
    receipt = request(port, "/hypotheses", envelope)
    (folder / "receipt.json").write_text(json.dumps(receipt, indent=2), encoding="utf8")
    ids = receipt["submission_ids"]
    print("full" if full else "screen", receipt, flush=True)
    asyncio.run(wait_for_outcomes(port, set(ids)))
    rows = [outcome(port, h) for h in ids]
    (folder / "evidence.json").write_text(json.dumps(rows, indent=2), encoding="utf8")
    for row in rows:
        print({k: row.get(k) for k in ("id", "name", "status", "job", "roi", "sharpe", "lifecycles",
                                       "entry_days", "drawdown", "failure")}, flush=True)
    return rows


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--out", type=Path, required=True)
    p.add_argument("--port", type=int, default=8765)
    mode = p.add_mutually_exclusive_group()
    for key in ("reclaim", "reference", "adaptive", "mark"):
        mode.add_argument(f"--{key}", action="store_const", const=key, dest="family")
    p.set_defaults(family="peer")
    args = p.parse_args()
    items, selector = {
        "peer": (CANDIDATES, STRATEGY),
        "reclaim": (RECLAIM_CANDIDATES, RECLAIM),
        "reference": (ROLL_CANDIDATES, REFERENCE_ROLL),
        "adaptive": (ADAPTIVE_CANDIDATES, ADAPTIVE_PEER),
        "mark": (MARK_CANDIDATES, MARK_REVERSION),
    }[args.family]
    rows = run_wave(args.port, args.out / "screen", [(name, params, None) for name, params in items],
                    mode=args.family)
    positive = [row for row in rows if row.get("roi", 0) > 0 and row.get("sharpe") is not None]
    if args.family == "reference":
        positive = [row for row in positive if row["parameters"]["lag_hours"] == 23]
    viable = [row for row in positive if row["lifecycles"] >= 250] or positive
    promoted = []
    for row in sorted(viable, key=lambda line: line["sharpe"], reverse=True)[:4]:
        job, = request(args.port, f"/hypotheses/{row['id']}/results")["experiments"]
        spec = job["specification"]
        resolved = prepare(selector, spec["parameters"], rebalance_ticks=1)[3]
        if resolved != {k: v for k, v in spec["strategy"].items() if k != "warmup_fingerprint"}:
            raise ValueError(f"Frozen configuration changed for {row['id']}")
        promoted.append((row["name"], spec["parameters"], job["id"]))
    if promoted:
        run_wave(args.port, args.out / "full", promoted, full=True, mode=args.family)


if __name__ == "__main__":
    main()
