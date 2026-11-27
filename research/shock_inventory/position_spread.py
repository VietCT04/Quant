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
ROLLING = BREAKOUT | {"version": "6.0.0"}
CONTEXT = BREAKOUT | {"version": "7.0.0"}
HEDGED = BREAKOUT | {"version": "8.1.0"}
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
EXITS = [
    ("Hourly invalidate at 1.6%", dict(entry_stop=.016)),
    ("Hourly invalidate at 3.4%", dict(entry_stop=.034)),
    ("Hourly reject failures and hold winners one day", dict(entry_stop=.024, hold=24, trail=.06)),
    ("Hourly strength and wider winner trail", dict(entry_stop=.025, hold=30, trail=.065, alignment=.035,
                                                   depth=.017, sigma=2.2)),
    ("Hourly pre-shock drift with longer winners", dict(entry_stop=.028, hold=24, trail=.06, drift_floor=.003,
                                                     depth=.017, sigma=2.2)),
]
ROLLING_BASE = DEMAND_BASE | dict(interval_minutes=30)
ROLLING_VARIANTS = [
    ("Rolling hour, balanced", {}),
    ("Rolling hour, smaller shocks", dict(depth=.016, sigma=2.55, alignment=.03, hold=18)),
    ("Rolling hour, persistence", dict(depth=.018, sigma=2.4, alignment=.05, hold=22, trail=.055)),
    ("Rolling hour, stronger surprise", dict(depth=.016, sigma=2.9, alignment=.02, hold=20, daily_entries=6)),
    ("Rolling hour, steady market", dict(depth=.0165, sigma=2.3, alignment=.035, market_floor=-.0005, hold=20)),
    ("Rolling hour, more capacity", dict(depth=.0165, sigma=2.35, fraction=.06, hold=14, daily_entries=6)),
]
CONTEXT_BASE = ROLLING_BASE | dict(depth=.0165, sigma=2.3, alignment=.035, market_floor=-.0005, hold=20)
CONTEXT_VARIANTS = [
    ("Steady two-day momentum", dict(context_min=.012, breadth_min=.40, depth=.015, sigma=2.15)),
    ("Widespread prior momentum", dict(context_min=-.025, breadth_min=.55, depth=.0135, sigma=2.0,
                                        daily_entries=7, alignment=.026)),
    ("Avoid the largest spikes", dict(max_shock=.065, context_min=-.04, breadth_min=.25, depth=.0145,
                                      sigma=2.25, daily_entries=6)),
    ("Broad momentum with capped volatility", dict(context_min=-.02, breadth_min=.50, depth=.0135,
                                                  sigma=2.15, vol_target=.009, daily_entries=7, fraction=.10)),
    ("Persistent two-day trend and resilient market", dict(context_min=.035, breadth_min=.38, depth=.013,
                                                            sigma=2.0, alignment=.04, daily_entries=7, hold=26)),
    ("Fewer extreme shocks and tighter daily dispersion", dict(max_shock=.047, context_min=-.025,
                                                              breadth_min=.30, depth=.013, sigma=2.05,
                                                              daily_entries=6, fraction=.09)),
    ("Persistent winners with volatility cap", dict(context_min=.02, breadth_min=.35, depth=.013,
                                                    sigma=2.0, daily_entries=7, vol_target=.012, fraction=.095)),
    ("Allow weaker market breadth for persistent winners", dict(context_min=.05, breadth_min=.2, depth=.013,
                                                                sigma=2.05, daily_entries=7, hold=24)),
]
HEDGE_VARIANTS = [
    ("BTC hedge, quarter exposure", dict(hedge_fraction=.25)),
    ("BTC hedge, 40% exposure", dict(hedge_fraction=.40, fraction=.06)),
    ("BTC hedge, one-half exposure", dict(hedge_fraction=.50, fraction=.055)),
    ("BTC hedge, broader eligible shocks", dict(hedge_fraction=.35, fraction=.06, depth=.0155,
                                                 sigma=2.4, daily_entries=6, hedge_hours=6)),
    ("BTC hedge with faster adjustment", dict(hedge_fraction=.4, fraction=.06, hedge_hours=2,
                                               depth=.0155, sigma=2.4, daily_entries=6)),
]
PERSISTENCE_BASE = CONTEXT_BASE | dict(context_min=.05, breadth_min=.2, depth=.013, sigma=2.05,
                                       daily_entries=7, hold=24)
PERSISTENCE_VARIANTS = [
    ("Persistent trend with lower 24-hour hurdle", dict(alignment=.02, sigma=1.9)),
    ("Persistent trend with lower 48-hour hurdle", dict(context_min=.026, sigma=1.95, alignment=.026)),
    ("Persistent trend with moderate market pullbacks", dict(context_min=.036, market_floor=-.0025,
                                                               alignment=.024, depth=.0125)),
    ("Persistent trend with smaller positions", dict(context_min=.023, depth=.012, sigma=1.8, alignment=.024,
                                                     fraction=.055, hold=22, daily_entries=8)),
    ("Persistent trend with wider winner trail", dict(context_min=.032, sigma=1.85, depth=.012,
                                                      alignment=.022, hold=32, trail=.06, fraction=.065)),
    ("Persistent trend with lower absolute hurdle", dict(context_min=.036, depth=.0115, sigma=1.8,
                                                        alignment=.021, hold=20, daily_entries=7)),
]


def submit(port, folder, promote=(), *, control=True, bridge=False, confirmed=False, exits=False,
           rolling=False, contextual=False, hedged=False, persistence=False):
    dataset = q.data.open(request(port, "/service")["coverage"]["source"])
    screen = q.data.select(dataset, start=SCREEN[0], end=SCREEN[1])
    selected = q.data.select(dataset, start=FULL[0], end=FULL[1]) if promote else screen
    fingerprint = q.data.fingerprint(selected)
    base, variants = ((PERSISTENCE_BASE, PERSISTENCE_VARIANTS) if persistence else
                      (CONTEXT_BASE, HEDGE_VARIANTS) if hedged else
                      (CONTEXT_BASE, CONTEXT_VARIANTS) if contextual else (ROLLING_BASE, ROLLING_VARIANTS) if rolling else
                      (DEMAND_BASE, EXITS) if exits else (DEMAND_BASE, DEMAND) if confirmed else
                      (HOURLY, BRIDGE) if bridge else (COMMON, VARIANTS))
    strategy = (HEDGED if hedged else CONTEXT if contextual or persistence else
                ROLLING if rolling else CONFIRMED if confirmed or exits else SPREAD)
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
    round_name = ("persistent trend follow-up" if persistence else "market risk hedge" if hedged else
                  "prior trend and breadth" if contextual else "rolling hour" if rolling else
                  "failed breakout exits" if exits else "confirmed demand" if confirmed else
                  "hourly follow-up" if bridge else "broader position days")
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
        lifecycle = job["outcome"]["metrics"]["position_lifecycles"]
        dates = lifecycle["entries_by_utc_day"]
        if sum(count for _, count in dates) != result["lifecycles"]:
            raise ValueError(f"{key} dates do not reconcile to closed positions")
        days = sorted(count for _, count in dates)
        result |= {"entry_days": len(days), "max_entries_per_day": max(days, default=0),
                   "top_ten_day_fraction": sum(days[-10:]) / result["lifecycles"] if days else 0.,
                   "entries_by_utc_day": dates}
        strategy = job["specification"]["strategy"]
        if strategy["id"] == HEDGED["id"] and strategy["version"] in ("8.0.0", HEDGED["version"]):
            hedge_positions = lifecycle["per_symbol"].get("BTCUSDT", {}).get("completed_positions", 0)
            result |= {"hedge_positions": hedge_positions, "directional_positions": result["lifecycles"] - hedge_positions,
                       "directional_entry_days_lower_bound": max(0, len(days) - hedge_positions)}
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--promote", nargs="+", default=[])
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--bridge", action="store_true")
    mode.add_argument("--confirmed", action="store_true")
    mode.add_argument("--exits", action="store_true")
    mode.add_argument("--rolling", action="store_true")
    mode.add_argument("--contextual", action="store_true")
    mode.add_argument("--hedged", action="store_true")
    mode.add_argument("--persistence", action="store_true")
    args = parser.parse_args()
    print(json.dumps(submit(args.port, args.out, args.promote,
                            control=not (args.bridge or args.confirmed or args.exits or args.rolling or
                                         args.contextual or args.hedged or args.persistence),
                            bridge=args.bridge, confirmed=args.confirmed, exits=args.exits, rolling=args.rolling,
                            contextual=args.contextual, hedged=args.hedged, persistence=args.persistence)),
          flush=True)


if __name__ == "__main__":
    main()
