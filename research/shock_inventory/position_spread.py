"""Submit and review the spread-out position campaign on the native queue."""

import argparse
import json
from pathlib import Path

import quant as q
from quant.control_cli import request
from quant.strategy import prepare

from .review import summarize
from .sharpe_search import BREAKOUT, FULL, SCREEN


SPREAD = BREAKOUT | {"version": "4.0.0"}
CONFIRMED = BREAKOUT | {"version": "5.0.0"}
REFERENCE = dict(alignment=.07, depth=.05, fraction=.22, hold=168, market_floor=-.003,
                 side=1, sigma=3., slots=4, trail=.12)
COMMON = dict(alignment=.035, depth=.022, fraction=.10, hold=24, market_floor=-.003,
              sigma=2.25, slots=8, trail=.05, interval_minutes=30, daily_entries=6)
VARIANTS = [
    ("Hourly moderate shocks", dict(interval_minutes=60, depth=.025, alignment=.02, sigma=2., hold=30)),
    ("Hourly relative surprises", dict(interval_minutes=60, depth=.02, sigma=2.75, hold=18, daily_entries=5)),
    ("Half-hour spread", {}),
    ("Half-hour stronger prior trend", dict(alignment=.065, depth=.018, sigma=2., hold=30)),
    ("Half-hour lower absolute threshold", dict(depth=.015, sigma=2.5, hold=18, trail=.045)),
    ("Half-hour steadier market", dict(depth=.017, sigma=2., market_floor=-.0005, daily_entries=5)),
    ("Quarter-hour spread", dict(interval_minutes=15, depth=.014, sigma=2.25, hold=18)),
    ("Quarter-hour stronger trend", dict(interval_minutes=15, depth=.012, alignment=.06, sigma=2., hold=24)),
    ("Quarter-hour extra diversification", dict(interval_minutes=15, depth=.013, fraction=.075, slots=10,
                                                 daily_entries=8, hold=18, trail=.045)),
]
HOURLY = COMMON | dict(interval_minutes=60, depth=.02, sigma=2.75, hold=18, daily_entries=5)
BRIDGE = [
    ("Hourly lower surprise", dict(sigma=2.35)),
    ("Hourly smaller absolute shock", dict(depth=.016, sigma=2.55, alignment=.03)),
    ("Hourly more moderate shocks in a steady market", dict(depth=.017, sigma=2.30, market_floor=-.0015)),
    ("Hourly broader eligibility with smaller risk", dict(depth=.019, sigma=2.35, alignment=.025,
                                                              fraction=.075, slots=10, hold=16)),
]
DEMAND_BASE = COMMON | dict(interval_minutes=60, depth=.019, sigma=2.35, alignment=.025,
                            fraction=.075, slots=10, hold=16, daily_entries=5)
DEMAND = [
    ("Hourly demand, selective flow", dict(depth=.017, sigma=2.2, flow_floor=.035)),
    ("Hourly demand, stronger flow", dict(depth=.0155, sigma=2.1, flow_floor=.11)),
    ("Hourly demand, positive pre-shock drift", dict(depth=.017, sigma=2.2, drift_floor=.003)),
    ("Hourly demand, flow and drift", dict(depth=.016, sigma=2.05, flow_floor=.015, drift_floor=.002)),
    ("Hourly demand, invalidate weak fills", dict(entry_stop=.024)),
    ("Hourly demand and failed-breakout exit", dict(depth=.017, sigma=2.2, flow_floor=.025, entry_stop=.030)),
    ("Half-hour demand and strength", dict(interval_minutes=30, depth=.015, sigma=2.10, flow_floor=.045,
                                           alignment=.06, hold=30, daily_entries=6, fraction=.09)),
    ("Half-hour demand, faster loss exit", dict(interval_minutes=30, depth=.0165, sigma=2.05, flow_floor=.02,
                                                alignment=.052, hold=27, daily_entries=6, entry_stop=.028, fraction=.09)),
]


def submit(port, folder, promote=(), *, control=True, bridge=False, confirmed=False):
    dataset = q.data.open(request(port, "/service")["coverage"]["source"])
    screen = q.data.select(dataset, start=SCREEN[0], end=SCREEN[1])
    selected = q.data.select(dataset, start=FULL[0], end=FULL[1]) if promote else screen
    fingerprint = q.data.fingerprint(selected)
    base, variants = (DEMAND_BASE, DEMAND) if confirmed else (HOURLY, BRIDGE) if bridge else (COMMON, VARIANTS)
    strategy = CONFIRMED if confirmed else SPREAD
    rows = [(name, base | changes, strategy, None) for name, changes in variants] if not promote else []
    if promote:
        source_fingerprint = q.data.fingerprint(screen)
        for key in promote:
            h = request(port, f"/hypotheses/{key}/results")
            job, = h["experiments"]
            spec = job["specification"]
            frozen = {k: v for k, v in spec["strategy"].items() if k != "warmup_fingerprint"}
            same_configuration = prepare(strategy, spec["parameters"], rebalance_ticks=1)[3] == frozen
            if (job["status"] != "completed" or spec["dataset_fingerprint"] != source_fingerprint
                    or spec["costs"] or spec["seed"] != 716 or not same_configuration
                    or not spec["evaluation"].get("position_spread")):
                raise ValueError(f"{key} is not a compatible completed screen")
            rows.append((h["name"], spec["parameters"], strategy, job["id"]))
        if control:
            original = request(port, "/hypotheses/H-0143/results")["experiments"][0]
            if original["specification"]["parameters"] != REFERENCE:
                raise ValueError("Reference parameters differ from H-0143")
            rows.append(("H-0143 position-day comparison", REFERENCE, BREAKOUT, original["id"]))
    items = [
        dict(name=name, family="Shock repricing", stage="simulation",
             mechanism="Smaller qualified shocks and spare portfolio capacity can distribute entries across more dates.",
             prediction="Close more positions on a broader set of UTC entry days while retaining positive net results.",
             definition="Reuse the prior-strength breakout and booked accounting with a fixed daily entry quota.",
             protocol=dict(start=selected.start, end=selected.end, universe=selected.symbols, seed=716,
                           dataset_fingerprint=fingerprint, costs={}, evaluation=dict(
                               research_window="full_history" if promote else "six_month", position_spread=True,
                               target_full_positions=1000, baseline_hypothesis="H-0143", origin_job=origin)),
             candidates=[{"kind": "native", "strategy": strategy, "parameters": parameters}])
        for name, parameters, strategy, origin in rows
    ]
    round_name = "confirmed demand" if confirmed else "hourly follow-up" if bridge else "broader position days"
    payload = {"campaign": f"Shock repricing / {round_name} / " + ("full" if promote else "screen"),
               "hypotheses": items}
    folder.mkdir(parents=True, exist_ok=False)
    (folder / "spec.json").write_text(json.dumps(payload, indent=2), encoding="utf8")
    receipt = request(port, "/hypotheses", payload)
    (folder / "receipt.json").write_text(json.dumps(receipt, indent=2), encoding="utf8")
    return receipt


def outcome(port, key):
    h = request(port, f"/hypotheses/{key}/results")
    job, = h["experiments"]
    result = {"id": key, "name": h["name"], **summarize(job)}
    if job["status"] == "completed":
        dates = job["outcome"]["metrics"]["position_lifecycles"]["entries_by_utc_day"]
        if sum(count for _, count in dates) != result["lifecycles"]:
            raise ValueError(f"{key} dates do not reconcile to closed positions")
        days = sorted(count for _, count in dates)
        result |= {"entry_days": len(days), "max_entries_per_day": max(days, default=0),
                   "top_ten_day_fraction": sum(days[-10:]) / result["lifecycles"] if days else 0.,
                   "entries_by_utc_day": dates}
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--promote", nargs="+", default=[])
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--bridge", action="store_true")
    mode.add_argument("--confirmed", action="store_true")
    args = parser.parse_args()
    print(json.dumps(submit(args.port, args.out, args.promote, control=not (args.bridge or args.confirmed),
                            bridge=args.bridge, confirmed=args.confirmed)), flush=True)


if __name__ == "__main__":
    main()
