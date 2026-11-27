"""Test whether mechanically changing displayed 24-hour returns predict demand."""

import numpy as np

import quant as q
from quant.strategy import Parameter, Plan, Plot, Strategy
from quant.trading import proportional_size


HOUR = 3_600_000
TICKS = 720


def prepare(p):
    e = q.features.expression
    one = e("quant.ts.pct_change", "close", periods=1, units="fraction")
    vol = e("quant.ts.rolling_std", one, window=36, units="fraction")
    graph = {
        "expiring": e("quant.ts.lag", one, periods=p["lag_hours"], units="fraction"),
        "recent": e("quant.ts.pct_change", "close", periods=2, units="fraction"),
        "vol": e("quant.ts.lag", vol, periods=1, units="fraction"),
        "turnover": e("quant.ts.lag", e("quant.ts.rolling_mean", "quote_volume", window=24, units="USDT"),
                      periods=1, units="USDT"),
    }
    return Plan(graph, warmup_bars=40 * TICKS, decision_ticks=TICKS,
                feature_periods=dict.fromkeys(graph, TICKS), plots=(
                    Plot("expiring", "Hourly return dropping out of the display", "fraction"),
                    Plot("roll_score", "Normalized display change", "ratio"),
                ))


def initialize(p, symbols, rng):
    return p, np.zeros(len(symbols), dtype="i8")


def trade(state, row, account, orders):
    p, opened = state
    price, expiring, recent, vol, turnover = (row.values[k][0] for k in ("close", "expiring", "recent", "vol", "turnover"))
    qty = account.positions["qty"]
    held = qty != 0
    valid = (row.status[0] == 0) & np.isfinite(price) & (price > 0)
    for i in np.flatnonzero(held & valid & (account.clock_ms - opened >= p["hold"] * HOUR)):
        orders.close(row.symbols[i])

    impact = -expiring
    score = impact / np.maximum(vol, .002)
    magnitude = np.abs(impact)
    eligible = (valid & ~held & np.isfinite(score) & (magnitude >= p["depth"]) & (magnitude <= p["cap"])
                & (np.abs(score) >= p["score"]) & (turnover >= p["liquidity"])
                & (np.sign(impact) * recent >= -p["adverse"]))
    candidates = np.flatnonzero(eligible)
    candidates = candidates[np.argsort(-np.abs(score[candidates]), kind="stable")]
    margin = account.available_margin
    size = proportional_size(account, p["fraction"])
    ready = set(account.ready)
    room = {1: p["slots"] - np.count_nonzero(qty > 0), -1: p["slots"] - np.count_nonzero(qty < 0)}
    if margin is not None and size >= 10.:
        for i in candidates:
            if margin < 1.01 * size:
                break
            side = 1 if score[i] > 0 else -1
            symbol = row.symbols[i]
            if room[side] <= 0 or symbol not in ready or (side < 0 and not p["short"]):
                continue
            if side > 0:
                orders.buy(symbol, notional=size)
            else:
                orders.sell(symbol, notional=size)
            opened[i] = account.clock_ms
            room[side] -= 1
            margin -= 1.01 * size
    return {"roll_score": score[None, :]}


STRATEGY = Strategy("displayed_return_roll", "1.0.0", {
    "lag_hours": Parameter("integer", 23, choices=(22, 23, 24)),
    "short": Parameter("boolean", True),
    "slots": Parameter("integer", 4, 2, 8),
    "hold": Parameter("integer", 2, 1, 8),
    "fraction": Parameter("number", .10, .025, .20),
    "depth": Parameter("number", .022, .004, .12),
    "cap": Parameter("number", .15, .04, .50),
    "score": Parameter("number", 1.7, .2, 10.),
    "liquidity": Parameter("number", 500_000., 0., 20_000_000.),
    "adverse": Parameter("number", .012, .001, .20),
}, ("close", "quote_volume"), prepare, initialize, orders=trade)
