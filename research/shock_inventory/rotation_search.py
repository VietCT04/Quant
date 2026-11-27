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
FLOW_ABSORPTION = {"id": "taker_flow_absorption", "version": "1.0.0"}
PAIR_DISLOCATION = {"id": "economic_peer_spread_reversion", "version": "1.0.0"}
PAIR_BREAKOUT = {"id": "economic_peer_spread_breakout", "version": "1.0.0"}
BREADTH_REBOUND = {"id": "market_breadth_rebound", "version": "1.0.0"}
COMPRESSION = {"id": "quiet_range_breakout", "version": "1.0.0"}
COMPRESSION_CONTEXT = {"id": "quiet_range_breakout", "version": "1.1.0"}
COMPRESSION_CAPACITY = {"id": "quiet_range_breakout", "version": "1.2.0"}
FAILED_RELIEF = {"id": "failed_countertrend_relief", "version": "1.0.0"}
DEFENSIVE_ROTATION = {"id": "defensive_demand_rotation", "version": "1.0.0"}
SHOCK_STRESS = {"id": "shock_repricing_breakout", "version": "8.3.0"}
FLOW_SIZE = {"id": "quote_count_flow_divergence", "version": "1.0.0"}
UTC_HANDOFF = {"id": "fixed_clock_turnover_handoff", "version": "1.0.0"}
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
ABSORPTION_CANDIDATES = [
    ("Absorbed aggressive selling", {}),
    ("Absorbed aggressive buying", dict(direction=-1)),
    ("Absorbed flow in either direction", dict(direction=0, fraction=.06)),
    ("Absorbed selling, higher pressure", dict(imbalance=.19, activity=1.55, hold=30)),
    ("Absorbed selling, broader turnover", dict(imbalance=.09, activity=1.12, hold=14, fraction=.07, slots=10)),
    ("Absorbed selling, strict relative resilience", dict(slip=.002, market_limit=.025, trend_limit=.065, hold=24)),
    ("Absorbed selling, slower exit", dict(imbalance=.11, activity=1.18, hold=36, cooldown=36, fraction=.07)),
]
PAIR_CANDIDATES = [
    ("Economic peers, rolling beta", {}),
    ("Economic peers, equal notional legs", dict(beta=False)),
    ("Economic peers, strongly correlated", dict(correlation=.80, entry_z=1.65)),
    ("Economic peers, rapid historical anchor", dict(history=60, min_gap=.010, hold=24, cooldown=12)),
    ("Economic peers, deep relative moves", dict(entry_z=2.6, min_gap=.024, hold=60, slots=7)),
    ("Economic peers, broader entry", dict(entry_z=1.55, history=60, min_gap=.009, correlation=.58, hold=30, fraction=.115)),
]
PAIR_BREAKOUT_CANDIDATES = [
    ("Peer repricing continuation, beta weighted", {}),
    ("Peer repricing continuation, equal notional", dict(beta=False)),
    ("Peer repricing continuation, fresh breaks", dict(history=60, entry_z=1.5, min_gap=.010, hold=24, cooldown=12)),
    ("Peer repricing continuation, sustained relative demand", dict(entry_z=2.35, extension_z=.75, hold=60, trail_z=1.8)),
    ("Peer repricing continuation, liquid strong links", dict(entry_z=1.6, correlation=.78, min_turnover=3_000_000.)),
    ("Peer repricing continuation, broader breaks", dict(entry_z=1.25, min_gap=.009, correlation=.57, extension_z=.25,
                                                         history=60, hold=32, cooldown=12, fraction=.115)),
]
BREADTH_CANDIDATES = [
    ("Broad washout, recovery leaders", {}),
    ("Broad washout, lagging recoveries", dict(leaders=False)),
    ("Broad squeeze, short the subsequent decline", dict(side=-1, market_shock=.023, hold=12, take=.05)),
    ("Broad washout, stronger confirmation", dict(recovery_breadth=.72, peer_bounce=.003, hold=21)),
    ("Broad washout, less severe events", dict(market_shock=.012, event_gap=12, hold=12, event_slots=12)),
    ("Broad washout, persistent prior market", dict(prior_limit=.020, market_shock=.014, hold=26)),
    ("Broad washout, deeper damage and longer recovery", dict(market_shock=.026, stop=.065, hold=30, take=.10)),
]
COMPRESSION_CANDIDATES = [
    ("Compression escape, long", {}),
    ("Compression escape, signed", dict(direction=0, fraction=.065)),
    ("Compression escape, short", dict(direction=-1, hold=20)),
    ("Compression escape, stronger expansion", dict(ratio=.57, activity=2.2, hold=48)),
    ("Compression escape, broaden activity", dict(ratio=.78, activity=1.3, fraction=.065, slots=10)),
    ("Compression escape, slow repricing", dict(ratio=.72, activity=1.35, hold=72, cooldown=72)),
    ("Compression escape, avoid chasing", dict(ratio=.73, chase=.020, activity=1.6, break_min=.003, hold=24)),
]
CONTEXT_BREAKOUT_CANDIDATES = [
    ("Compression with prior market breadth", dict(ratio=.78, activity=1.30, fraction=.065, slots=10)),
    ("Compression with positive market breadth", dict(ratio=.78, activity=1.30, fraction=.065, slots=10,
                                                       participation=.54, market_floor=0.)),
    ("Compression with relative pre-break strength", dict(ratio=.78, activity=1.30, fraction=.065, slots=10,
                                                            participation=.40, relative_floor=.008)),
    ("Compression respite, wider qualifying pressure", dict(ratio=.86, activity=1.18, fraction=.052, slots=13,
                                                              participation=.45, chase=.024)),
    ("Compression respite, moderate market floor", dict(ratio=.82, activity=1.20, fraction=.055, slots=12,
                                                          participation=.47, market_floor=-.012, hold=42)),
]
CAPACITY_BREAKOUT_CANDIDATES = [
    ("Compression, completed-bar cap 10%", dict(ratio=.83, activity=1.22, participation_cap=.10, slots=12)),
    ("Compression, completed-bar cap 20%", dict(ratio=.83, activity=1.22, participation_cap=.20, slots=12)),
    ("Compression, completed-bar cap 35%", dict(ratio=.83, activity=1.22, participation_cap=.35, slots=12)),
    ("Compression, lower volatility risk", dict(ratio=.85, activity=1.18, participation_cap=.22, vol_target=.009,
                                               slots=13, fraction=.07, min_quote=1_000_000.)),
    ("Compression, cap with less chasing", dict(ratio=.80, activity=1.25, participation_cap=.20, chase=.024,
                                                min_quote=2_000_000., slots=12)),
]
RELIEF_CANDIDATES = [
    ("Failed relief, resumption of downtrend", {}),
    ("Failed relief, stronger market stress", dict(market=.026, trend=.065, hold=24)),
    ("Failed relief, broader downtrends", dict(trend=.033, market=.005, relief_min=.012, turn_min=.003, hold=24)),
    ("Failed relief, fast risk reduction", dict(trend=.042, stop=.022, hold=12, take=.040, liquidity=3_000_000.)),
    ("Failed relief, deeper countertrend traps", dict(relief_min=.030, turn_min=.007, hold=32)),
    ("Failed relief, pullback ends in uptrend", dict(side=1)),
    ("Failed relief, broader long-side trend resumption", dict(side=1, trend=.036, market=.006, relief_min=.012,
                                                                turn_min=.003, hold=28)),
]
DEFENSIVE_CANDIDATES = [
    ("Defensive demand after BTC drawdown", {}),
    ("Defensive demand, broader opportunity window", dict(btc_drop=.025, relative_min=.012, slots=12, fraction=.052)),
    ("Defensive demand, deep BTC stress", dict(btc_drop=.065, relative_min=.025, hold=32)),
    ("Defensive demand, hold established resilience", dict(breakout=False, btc_drop=.035, relative_min=.018, hold=42)),
    ("Defensive demand in liquid contracts", dict(volume_min=2_500_000., activity=1.45, btc_drop=.035, chase=.035)),
    ("Defensive demand, rapid exits and repeated participation", dict(breakout=False, btc_drop=.030, relative_min=.011,
                                                                      hold=16, cooldown=18, slots=14, fraction=.045)),
]
STRESS_BASE = dict(alignment=.035, daily_entries=6, depth=.0155, fraction=.06, hold=20,
                   interval_minutes=30, market_floor=-.0005, sigma=2.4, slots=10, trail=.05)
STRESS_CANDIDATES = [
    ("Shock breakout, BTC drawdown risk ramp", STRESS_BASE),
    ("Shock breakout, moderate haircut", STRESS_BASE | dict(ordinary_size=.55)),
    ("Shock breakout, strong ordinary-risk haircut", STRESS_BASE | dict(ordinary_size=.16, stress=.045)),
    ("Shock breakout, wider qualified events", STRESS_BASE | dict(ordinary_size=.25, alignment=.02, sigma=2.35)),
    ("Shock breakout, severe stress only for full risk", STRESS_BASE | dict(stress=.08, ordinary_size=.35)),
    ("Shock breakout, early stress response", STRESS_BASE | dict(stress=.035, ordinary_size=.25, hold=16)),
]
FLOW_SIZE_CANDIDATES = [
    ("Quote-count flow divergence, long", {}),
    ("Quote-count flow divergence, short", dict(direction=-1)),
    ("Quote-count flow divergence, either side", dict(direction=0, fraction=.058)),
    ("Quote-count divergence, ordinary mismatch", dict(size_gap=.062, flow_min=.04, hold=28, fraction=.06, slots=11)),
    ("Quote-count divergence, dollar leadership stronger", dict(size_gap=.12, flow_min=.075,
                                                                 count_max=0., hold=50, min_volume=3_000_000.)),
    ("Quote-count divergence, persistence amid soft trend", dict(size_gap=.08, flow_min=.05, recent_limit=.025,
                                                                  prior_limit=.04, hold=52, cooldown=52)),
]
UTC_HANDOFF_CANDIDATES = [
    ("Clocked turnover handoff, 14 UTC long", {}),
    ("Clocked turnover handoff, 08 UTC long", dict(utc_hour=8)),
    ("Clocked turnover handoff, 20 UTC long", dict(utc_hour=20)),
    ("Clocked turnover handoff, signed", dict(direction=0, fraction=.05)),
    ("Clocked turnover handoff, midday intensification", dict(activity=1.75, min_quote=1_600_000., drift=.017, hold=6)),
    ("Clocked turnover handoff, wider daily opportunities", dict(drift=.007, activity=1.18, confirmation=.0015,
                                                                hold=10, slots=12, fraction=.05)),
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
    absorption = "Directional taker flow may reverse when high turnover produces little price movement in its direction versus peers."
    pairs = "A highly correlated economic peer pair may converge from a large temporary spread deviation after both legs pay costs."
    pair_breakout = "A widening spread between previously correlated contracts may persist if it reflects relative repricing rather than a short-lived quote imbalance."
    breadth = "A broad reversal after an observed market-wide washout may persist when participation returns across many contracts."
    compression = "A price breakout may persist if it ends a relatively quiet period and arrives with higher observable turnover."
    compression_context = "Quiet range escapes may be more durable when the preceding market shows wider participation."
    capacity = "A prior-traded-notional size cap may mitigate the execution vulnerability measured in the compression replay."
    relief = "If a countertrend move fails in an established trend, the original direction may resume after execution costs."
    defensive = "Contracts with independent relative demand during a prior broad drawdown may continue to strengthen."
    stress = "The booked shock strategy earned more on days after BTC declines. Causal risk sizing tests whether scaling by that state improves net Sharpe."
    flow_size = "Directional quote value may predict continuation when constituent counts disagree with the quoted flow."
    handoff = "A preceding relative move may continue when it is confirmed by increased turnover at a fixed UTC window."
    family, selector, description = {
        "peer": ("Peer rotation", STRATEGY, None),
        "reclaim": ("Range reclaim", RECLAIM, reclaim_mechanism),
        "reference": ("Displayed return roll", REFERENCE_ROLL, roll_mechanism),
        "adaptive": ("Adaptive peer forecast", ADAPTIVE_PEER, adaptive_mechanism),
        "mark": ("Perpetual mark dislocation", MARK_REVERSION, mark_mechanism),
        "absorption": ("Taker flow absorption", FLOW_ABSORPTION, absorption),
        "pairs": ("Economic peer spread convergence", PAIR_DISLOCATION, pairs),
        "pair_breakout": ("Economic peer spread continuation", PAIR_BREAKOUT, pair_breakout),
        "breadth": ("Market-wide breadth rebound", BREADTH_REBOUND, breadth),
        "compression": ("Volatility compression breakout", COMPRESSION, compression),
        "compression_context": ("Compression breakout with prior participation", COMPRESSION_CONTEXT, compression_context),
        "compression_capacity": ("Compression breakout with observable capacity", COMPRESSION_CAPACITY, capacity),
        "failed_relief": ("Failed countertrend relief", FAILED_RELIEF, relief),
        "defensive": ("Defensive demand rotation", DEFENSIVE_ROTATION, defensive),
        "stress": ("Shock breakout with BTC stress sizing", SHOCK_STRESS, stress),
        "flow_size": ("Quote-count flow divergence", FLOW_SIZE, flow_size),
        "utc_handoff": ("Fixed-clock turnover handoff", UTC_HANDOFF, handoff),
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
    for key in ("reclaim", "reference", "adaptive", "mark", "absorption", "pairs", "pair_breakout", "breadth",
                "compression", "compression_context", "compression_capacity", "failed_relief", "defensive", "stress",
                "flow_size", "utc_handoff"):
        mode.add_argument("--" + key.replace("_", "-"), action="store_const", const=key, dest="family")
    p.set_defaults(family="peer")
    args = p.parse_args()
    items, selector = {
        "peer": (CANDIDATES, STRATEGY),
        "reclaim": (RECLAIM_CANDIDATES, RECLAIM),
        "reference": (ROLL_CANDIDATES, REFERENCE_ROLL),
        "adaptive": (ADAPTIVE_CANDIDATES, ADAPTIVE_PEER),
        "mark": (MARK_CANDIDATES, MARK_REVERSION),
        "absorption": (ABSORPTION_CANDIDATES, FLOW_ABSORPTION),
        "pairs": (PAIR_CANDIDATES, PAIR_DISLOCATION),
        "pair_breakout": (PAIR_BREAKOUT_CANDIDATES, PAIR_BREAKOUT),
        "breadth": (BREADTH_CANDIDATES, BREADTH_REBOUND),
        "compression": (COMPRESSION_CANDIDATES, COMPRESSION),
        "compression_context": (CONTEXT_BREAKOUT_CANDIDATES, COMPRESSION_CONTEXT),
        "compression_capacity": (CAPACITY_BREAKOUT_CANDIDATES, COMPRESSION_CAPACITY),
        "failed_relief": (RELIEF_CANDIDATES, FAILED_RELIEF),
        "defensive": (DEFENSIVE_CANDIDATES, DEFENSIVE_ROTATION),
        "stress": (STRESS_CANDIDATES, SHOCK_STRESS),
        "flow_size": (FLOW_SIZE_CANDIDATES, FLOW_SIZE),
        "utc_handoff": (UTC_HANDOFF_CANDIDATES, UTC_HANDOFF),
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
