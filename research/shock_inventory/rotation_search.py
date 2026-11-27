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
SHOCK_STRESS_BTC = {"id": "shock_repricing_breakout", "version": "8.4.0"}
FLOW_SIZE = {"id": "quote_count_flow_divergence", "version": "1.0.0"}
UTC_HANDOFF = {"id": "fixed_clock_turnover_handoff", "version": "1.0.0"}
UTC_WEEKEND = {"id": "fixed_clock_turnover_handoff", "version": "1.1.0"}
UTC_SEASONAL = {"id": "fixed_clock_turnover_handoff", "version": "1.2.0"}
UTC_RISK = {"id": "fixed_clock_turnover_handoff", "version": "1.3.0"}
UTC_FLOW = {"id": "fixed_clock_turnover_handoff", "version": "1.4.0"}
UTC_TRAIL = {"id": "fixed_clock_turnover_handoff", "version": "1.5.0"}
UTC_CONVICTION = {"id": "fixed_clock_turnover_handoff", "version": "1.6.0"}
UTC_HORIZON = {"id": "fixed_clock_turnover_handoff", "version": "1.7.0"}
UTC_TRANSITION = {"id": "fixed_clock_turnover_handoff", "version": "1.8.0"}
UTC_COMBINED_RISK = {"id": "fixed_clock_turnover_handoff", "version": "1.9.0"}
UTC_RELATIVE = {"id": "fixed_clock_turnover_handoff", "version": "2.0.0"}
SIGNED_VARIANCE = {"id": "signed_variance_reversal", "version": "1.0.0"}
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
STRESS_BTC_CANDIDATES = [
    ("BTC-only stress history, moderate haircut", STRESS_BASE | dict(ordinary_size=.55)),
    ("BTC-only stress history, stronger haircut", STRESS_BASE | dict(ordinary_size=.35)),
    ("BTC-only stress history, mild haircut", STRESS_BASE | dict(ordinary_size=.72)),
    ("BTC-only stress history, lower threshold", STRESS_BASE | dict(stress=.04, ordinary_size=.48)),
    ("BTC-only stress history, broader events", STRESS_BASE | dict(ordinary_size=.55, alignment=.025, sigma=2.35)),
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
VARIANCE_CANDIDATES = [
    ("Signed variance, recover from downside dominance", {}),
    ("Signed variance, fade upside dominance", dict(direction=-1)),
    ("Signed variance, either side", dict(direction=0, fraction=.05)),
    ("Signed variance, broader eligibility", dict(skew=.48, separation=.17, min_move=.016,
                                                 turn_min=.003, fraction=.052, slots=12)),
    ("Signed variance, 12-hour exhaustion", dict(history=6, min_scale=.019, hold=14, cooldown=22,
                                                min_move=.018, skew=.56, separation=.20)),
    ("Signed variance, slower recovery", dict(history=24, min_scale=.038, max_scale=.18,
                                             hold=36, cooldown=48, min_move=.028, max_move=.18)),
]
WEEKEND_CANDIDATES = [
    ("Weekend risk, same 14 UTC entry", dict(direction=0, fraction=.05, clock_window="14")),
    ("Weekend risk, adjacent clocks", dict(direction=0, fraction=.05)),
    ("Weekend risk, broader daily window", dict(direction=0, fraction=.048, clock_window="12-16",
                                                activity=1.3, drift=.009, slots=12)),
    ("Weekend risk only, wider turnover window", dict(direction=0, fraction=.058, clock_window="12-16",
                                                       weekday_weight=0., activity=1.22, drift=.008,
                                                       confirmation=.0015, slots=13)),
    ("Weekend risk, alternate clocks", dict(direction=0, fraction=.05, weekday_weight=.1, clock_window="14-20",
                                             hold=6, activity=1.30, min_quote=1_250_000.)),
    ("Weekend risk, stricter range and weekday control", dict(direction=0, fraction=.05, weekday_weight=.35,
                                                                chase=.024, min_quote=1_200_000., activity=1.35)),
]
SEASONAL_CANDIDATES = [
    ("14 UTC turnover relative to the same weekday", dict(direction=0, fraction=.05, clock_window="14")),
    ("14 UTC same-weekday turnover, smaller ordinary risk", dict(direction=0, fraction=.05, clock_window="14",
                                                                 weekday_weight=.10, hold=5)),
    ("14 UTC same-weekday turnover, broader opportunities", dict(direction=0, fraction=.045, clock_window="14",
                                                                  weekday_weight=.18, activity=1.24, slots=12, hold=6)),
    ("14 UTC requires activity above yesterday and last week", dict(direction=0, fraction=.055, clock_window="14",
                                                                     anchor="conservative", activity=1.20, hold=6)),
    ("14 UTC same-weekday turnover, stronger confirmation", dict(direction=0, fraction=.05, clock_window="14",
                                                                  confirmation=.003, min_quote=1_250_000., hold=7)),
]
UTC_RISK_CANDIDATES = [
    ("14 UTC prior volatility cap", dict(direction=0, fraction=.05, clock_window="14")),
    ("14 UTC tighter prior volatility cap", dict(direction=0, fraction=.055, clock_window="14", vol_target=.007)),
    ("14 UTC prior volatility cap, five-hour holding", dict(direction=0, fraction=.055, clock_window="14",
                                                            vol_target=.01, hold=5, stop=.022)),
    ("14 UTC prior volatility cap, smaller weekday risk", dict(direction=0, fraction=.05, clock_window="14",
                                                               vol_target=.01, weekday_weight=.10, hold=6)),
    ("14 UTC prior volatility cap, broader opportunities", dict(direction=0, fraction=.045, clock_window="14",
                                                                vol_target=.008, activity=1.32, drift=.009, slots=12)),
]
UTC_FLOW_CANDIDATES = [
    ("14 UTC quote confirmation, eight-hour base", dict(direction=0, fraction=.05, clock_window="14", weekday_weight=.08)),
    ("14 UTC quote confirmation, peer-relative pressure", dict(direction=0, fraction=.05, clock_window="14",
                                                                weekday_weight=.08, relative_pressure=True)),
    ("14 UTC quote confirmation, moderate haircut", dict(direction=0, fraction=.05, clock_window="14",
                                                          weekday_weight=.08, weak_weight=.55)),
    ("14 UTC quote confirmation, stricter activity", dict(direction=0, fraction=.05, clock_window="14",
                                                       weekday_weight=.10, pressure_min=.042, weak_weight=.30,
                                                       min_quote=1_200_000., activity=1.36)),
    ("14 UTC quote confirmation, shorter risk window", dict(direction=0, fraction=.055, clock_window="14",
                                                            weekday_weight=.10, hold=6, stop=.023)),
]
UTC_TRAIL_CANDIDATES = [
    ("14 UTC protection after an initial gain", dict(direction=0, fraction=.05, clock_window="14", weekday_weight=.08)),
    ("14 UTC protection after a small gain", dict(direction=0, fraction=.05, clock_window="14", weekday_weight=.08,
                                                   arm_profit=.012, trail_gap=.008)),
    ("14 UTC protection for larger gains", dict(direction=0, fraction=.05, clock_window="14", weekday_weight=.08,
                                                arm_profit=.026, trail_gap=.016)),
    ("14 UTC later protection with liquidity bias", dict(direction=0, fraction=.05, clock_window="14", weekday_weight=.12,
                                                          arm_profit=.016, trail_gap=.01, trail_delay=3,
                                                          min_quote=1_400_000., activity=1.38, chase=.026)),
    ("14 UTC gain protection, wider holding", dict(direction=0, fraction=.05, clock_window="14", weekday_weight=.10,
                                                   hold=10, arm_profit=.018, trail_gap=.01, trail_delay=2)),
    ("Liquid 14 UTC, matched gain protection", dict(direction=0, fraction=.05, clock_window="14", weekday_weight=.20,
                                                    min_quote=1_500_000., activity=1.38, chase=.025)),
    ("Liquid 14 UTC, earlier gain protection", dict(direction=0, fraction=.05, clock_window="14", weekday_weight=.20,
                                                    min_quote=1_500_000., activity=1.38, chase=.025,
                                                    arm_profit=.012, trail_gap=.007)),
]
UTC_CONVICTION_BASE = dict(direction=0, min_quote=1_600_000., activity=1.29, drift=.014,
                           confirmation=.0017, chase=.025, weekday_weight=.20, clock_window="14", fraction=.05)
UTC_CONVICTION_CANDIDATES = [
    ("Clocked continuation, gradual conviction", UTC_CONVICTION_BASE),
    ("Clocked continuation, gentle conviction", UTC_CONVICTION_BASE | dict(weak_size=.72, move_band=.010)),
    ("Clocked continuation, emphasize larger moves", UTC_CONVICTION_BASE | dict(weak_size=.30, move_band=.014)),
    ("Clocked continuation, keep moderate signal count", UTC_CONVICTION_BASE | dict(drift=.012, activity=1.30,
                                                                                  weak_size=.38, slots=11)),
    ("Clocked continuation, tighter execution liquidity", UTC_CONVICTION_BASE | dict(min_quote=2_000_000.,
                                                                                     activity=1.23, drift=.0125,
                                                                                     weak_size=.5)),
]
UTC_HORIZON_CANDIDATES = [
    ("Liquid clock, four-hour prior move", UTC_CONVICTION_BASE | dict(prior_hours=4)),
    ("Liquid clock, eight-hour prior move", UTC_CONVICTION_BASE | dict(prior_hours=8)),
    ("Liquid clock, ten-hour prior move", UTC_CONVICTION_BASE | dict(prior_hours=10)),
    ("Liquid clock, twelve-hour prior move", UTC_CONVICTION_BASE | dict(prior_hours=12)),
    ("Liquid clock, four hours with volatility-scaled hurdle", UTC_CONVICTION_BASE | dict(prior_hours=4, drift=.0115)),
    ("Liquid clock, ten hours with volatility-scaled hurdle", UTC_CONVICTION_BASE | dict(prior_hours=10, drift=.018)),
]
UTC_TRANSITION_CANDIDATES = [
    ("Liquid clock, smaller orders on weekend transitions", UTC_CONVICTION_BASE),
    ("Liquid clock, Friday size reduction", UTC_CONVICTION_BASE | dict(monday=False)),
    ("Liquid clock, general weekday reduction", UTC_CONVICTION_BASE | dict(weekday_weight=.10, transition_weight=.04)),
    ("Liquid clock, moderate transition reduction", UTC_CONVICTION_BASE | dict(transition_weight=.10)),
    ("Liquid clock, transition reduction with wider price gate", UTC_CONVICTION_BASE | dict(drift=.0125, activity=1.30,
                                                                                           min_quote=1_750_000.)),
]
UTC_COMBINED_BASE = UTC_CONVICTION_BASE | dict(weak_size=.30, move_band=.014, monday=False, transition_weight=.05)
UTC_COMBINED_CANDIDATES = [
    ("Move-based size with Friday reduction", UTC_COMBINED_BASE),
    ("Move-based size with both transition reductions", UTC_COMBINED_BASE | dict(monday=True)),
    ("Move-based size with stronger Friday reduction", UTC_COMBINED_BASE | dict(transition_weight=.032)),
    ("Move-based size with moderate Friday reduction", UTC_COMBINED_BASE | dict(transition_weight=.09)),
    ("Move-based size and a nine-hour expiry", UTC_COMBINED_BASE | dict(hold=9, weekday_weight=.18)),
    ("Move-based size and broader qualifying opportunities", UTC_COMBINED_BASE | dict(drift=.012, activity=1.30,
                                                                                      weak_size=.38, slots=11)),
]
UTC_RELATIVE_CANDIDATES = [
    ("Liquid clock, relative strength of prior move", UTC_CONVICTION_BASE),
    ("Liquid clock, stronger relative hurdle", UTC_CONVICTION_BASE | dict(relative_start=.012, weak_size=.27)),
    ("Liquid clock, gentler relative scaling", UTC_CONVICTION_BASE | dict(relative_start=.010, weak_size=.55)),
    ("Liquid clock, relative strength with broader absolute gate", UTC_CONVICTION_BASE | dict(drift=.0115,
                                                                                              activity=1.32, slots=11)),
    ("Liquid clock, relative sizing with deeper liquidity", UTC_CONVICTION_BASE | dict(min_quote=2_400_000., activity=1.22,
                                                                                       drift=.012, relative_width=.022)),
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
    variance = "An unusually one-sided contribution to realized variance may reverse after an observed move in the opposite direction."
    weekend = "The initial 14 UTC rule earned stronger weekend returns. A causal weekend risk rule tests if that effect survives controlled widening."
    seasonal = "Turnover relative to the same weekday may distinguish unusual demand from a regular weekday-to-weekend change."
    utc_risk = "Capping the size of previously volatile contracts may reduce portfolio variation without tightening the turnover signal."
    utc_flow = "Directional continuation may be more durable when observed aggressive quote flow confirms the entry."
    utc_trail = "Observed intraday gains may reverse before a fixed exit. Trailing exits test whether only weak continuations should close early."
    utc_conviction = "Stronger preceding directional moves improved the liquid-clock test. A causal size ramp tests the effect without dropping modest entries."
    utc_horizon = "Persistence may depend on whether the preceding direction has held for four to twelve completed hours."
    utc_transition = "Smaller risk immediately before and after the weekend may reduce the hold-to-hold variation of a clocked signal."
    utc_combined = "Observed move-based sizing and a smaller weekend-transition budget may improve the same clocked signal jointly."
    utc_relative = "A prevailing market move can inflate absolute momentum. Size depends on the move relative to observed peer returns."
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
        "stress_btc": ("Shock breakout with BTC-only stress history", SHOCK_STRESS_BTC, stress),
        "flow_size": ("Quote-count flow divergence", FLOW_SIZE, flow_size),
        "utc_handoff": ("Fixed-clock turnover handoff", UTC_HANDOFF, handoff),
        "variance": ("Signed variance exhaustion", SIGNED_VARIANCE, variance),
        "weekend": ("Weekend turnover continuation", UTC_WEEKEND, weekend),
        "seasonal": ("Same-weekday turnover normalization", UTC_SEASONAL, seasonal),
        "utc_risk": ("Observed volatility sizing for clocked turnover", UTC_RISK, utc_risk),
        "utc_flow": ("Quote pressure sizing for clocked turnover", UTC_FLOW, utc_flow),
        "utc_trail": ("Clocked turnover with gain protection", UTC_TRAIL, utc_trail),
        "utc_conviction": ("Clocked turnover with move-based sizing", UTC_CONVICTION, utc_conviction),
        "utc_horizon": ("Clocked turnover with alternative preceding horizons", UTC_HORIZON, utc_horizon),
        "utc_transition": ("Clocked turnover with weekend transition sizing", UTC_TRANSITION, utc_transition),
        "utc_combined": ("Clocked turnover with move and transition sizing", UTC_COMBINED_RISK, utc_combined),
        "utc_relative": ("Clocked turnover with peer-relative sizing", UTC_RELATIVE, utc_relative),
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
                "flow_size", "utc_handoff", "stress_btc", "variance", "weekend", "seasonal", "utc_risk",
                "utc_flow", "utc_trail", "utc_conviction", "utc_horizon", "utc_transition", "utc_combined", "utc_relative"):
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
        "stress_btc": (STRESS_BTC_CANDIDATES, SHOCK_STRESS_BTC),
        "flow_size": (FLOW_SIZE_CANDIDATES, FLOW_SIZE),
        "utc_handoff": (UTC_HANDOFF_CANDIDATES, UTC_HANDOFF),
        "variance": (VARIANCE_CANDIDATES, SIGNED_VARIANCE),
        "weekend": (WEEKEND_CANDIDATES, UTC_WEEKEND),
        "seasonal": (SEASONAL_CANDIDATES, UTC_SEASONAL),
        "utc_risk": (UTC_RISK_CANDIDATES, UTC_RISK),
        "utc_flow": (UTC_FLOW_CANDIDATES, UTC_FLOW),
        "utc_trail": (UTC_TRAIL_CANDIDATES, UTC_TRAIL),
        "utc_conviction": (UTC_CONVICTION_CANDIDATES, UTC_CONVICTION),
        "utc_horizon": (UTC_HORIZON_CANDIDATES, UTC_HORIZON),
        "utc_transition": (UTC_TRANSITION_CANDIDATES, UTC_TRANSITION),
        "utc_combined": (UTC_COMBINED_CANDIDATES, UTC_COMBINED_RISK),
        "utc_relative": (UTC_RELATIVE_CANDIDATES, UTC_RELATIVE),
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
