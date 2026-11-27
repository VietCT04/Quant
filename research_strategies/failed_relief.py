"""Follow established trends after a countertrend move fails."""

import numpy as np

import quant as q
from quant.strategy import Parameter, Plan, Plot, Strategy
from quant.trading import proportional_size


TICKS = 1440
HOUR = 3_600_000


def prepare(p):
    e = q.features.expression

    def ret(n):
        return e("quant.ts.pct_change", "close", periods=n, units="fraction")

    def lag(node, n):
        return e("quant.ts.lag", node, periods=n, units=node.units)

    graph = {
        "drift": lag(ret(36), 4),
        "relief": lag(ret(3), 1),
        "turn": ret(1),
        "turnover": e("quant.ts.rolling_sum", "quote_volume", window=3, units="USDT"),
    }
    return Plan(graph, warmup_bars=41 * TICKS, decision_ticks=TICKS,
                feature_periods=dict.fromkeys(graph, TICKS), plots=(
                    Plot("drift", "Prior three-day trend", "fraction"),
                    Plot("relief", "Preceding six-hour countertrend return", "fraction"),
                    Plot("turn", "Completed confirmation return", "fraction"),
                ))


def initialize(p, symbols, rng):
    return p, np.full(len(symbols), -(1 << 60), dtype="i8")


def trade(state, row, account, orders):
    p, attempted = state
    px, drift, relief, turn, turnover = (row.values[k][0] for k in ("close", "drift", "relief", "turn", "turnover"))
    qty, entry = (account.positions[k] for k in ("qty", "entry"))
    booked = qty != 0
    side = p["side"]
    valid = (row.status[0] == 0) & np.isfinite(px) & (px > 0)
    age = (account.clock_ms - attempted) / HOUR
    pnl = side * (np.divide(px, entry, out=np.full_like(px, np.nan), where=entry > 0) - 1.)
    for i in np.flatnonzero(booked & valid & ((age >= p["hold"]) | (pnl < -p["stop"]) | (pnl >= p["take"]))):
        orders.close(row.symbols[i])
    peers = valid & np.isfinite(drift)
    if np.count_nonzero(peers) < 20 or np.median(side * drift[peers]) < p["market"]:
        return {}
    opposed = -side * relief
    reversal = side * turn
    qualify = (peers & ~booked & (age >= p["cooldown"]) & (side * drift >= p["trend"])
               & (opposed >= p["relief_min"]) & (opposed <= p["relief_max"])
               & (reversal >= p["turn_min"]) & (reversal <= .85 * opposed)
               & (turnover >= p["liquidity"]))
    candidates = np.flatnonzero(qualify)
    score = np.minimum(side * drift[candidates], .25) * opposed[candidates]
    size = proportional_size(account, p["fraction"])
    margin = account.available_margin
    room = p["slots"] - np.count_nonzero(booked)
    ready = set(account.ready)
    if margin is not None and size >= 10.:
        for i in candidates[np.argsort(-score, kind="stable")]:
            if room <= 0 or margin < 1.01 * size:
                break
            if row.symbols[i] not in ready:
                continue
            order = orders.buy if side > 0 else orders.sell
            order(row.symbols[i], notional=size)
            attempted[i] = account.clock_ms
            margin -= 1.01 * size
            room -= 1
    return {}


STRATEGY = Strategy("failed_countertrend_relief", "1.0.0", {
    "side": Parameter("integer", -1, choices=(-1, 1)),
    "market": Parameter("number", .01, -.03, .12),
    "trend": Parameter("number", .05, .02, .30),
    "relief_min": Parameter("number", .018, .007, .07),
    "relief_max": Parameter("number", .09, .05, .22),
    "turn_min": Parameter("number", .004, .001, .018),
    "liquidity": Parameter("number", 2_000_000., 200_000., 30_000_000.),
    "hold": Parameter("integer", 20, 6, 72),
    "stop": Parameter("number", .031, .009, .12),
    "take": Parameter("number", .065, .02, .18),
    "cooldown": Parameter("integer", 30, 8, 100),
    "slots": Parameter("integer", 10, 4, 15),
    "fraction": Parameter("number", .06, .02, .10),
}, ("close", "quote_volume"), prepare, initialize, orders=trade)
