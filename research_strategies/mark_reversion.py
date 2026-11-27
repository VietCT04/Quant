"""Fade unusually wide deviations from a recently observed perpetual mark."""

import numpy as np

import quant as q
from quant.strategy import Parameter, Plan, Plot, Strategy
from quant.trading import proportional_size


MINUTE = 60_000


def prepare(p):
    feature = q.features.expression
    return Plan({
        "mark_return": feature("quant.ts.pct_change", "mark", periods=12, units="fraction"),
        "close_return": feature("quant.ts.pct_change", "close", periods=60, units="fraction"),
    }, warmup_bars=61, decision_ticks=6, plots=(
        Plot("mark_gap", "Trade close relative to observable mark", "fraction"),
        Plot("mark_return", "Change in observable mark", "fraction"),
    ))


def initialize(p, symbols, rng):
    return p, np.full(len(symbols), -1, dtype="i8"), np.full(len(symbols), np.nan)


def trade(state, row, account, orders):
    p, attempted, initial = state
    price, mark, mark_return, close_return, quote = (row.values[k][0] for k in
        ("close", "mark", "mark_return", "close_return", "quote_volume"))
    qty = account.positions["qty"]
    held = qty != 0
    now = account.clock_ms
    traded = (row.status[0] == 0) & np.isfinite(price) & (price > 0)
    gap = np.divide(price, mark, where=np.isfinite(mark) & (mark > 0), out=np.full(len(mark), np.nan)) - 1.
    side = np.sign(qty)
    adverse = side * (price / initial - 1.)
    close = held & traded & (
        (side * gap >= -p["exit_gap"]) | (adverse <= -p["stop"])
        | (now - attempted >= p["hold"] * MINUTE)
    )
    for i in np.flatnonzero(close):
        orders.close(row.symbols[i])

    # The mark feed publishes minute samples after the minute boundary.
    # Deny openings at that boundary by default, and still manage existing positions.
    if p["fresh_only"] and now % MINUTE == 0:
        return {"mark_gap": gap[None, :]}
    intended = -np.sign(gap)
    valid = (traded & ~held & (np.abs(gap) >= p["gap"]) & (np.abs(gap) <= p["cap"])
             & (np.abs(mark_return) <= p["max_mark_move"])
             & (intended * close_return >= -p["max_trend"])
             & (quote >= p["min_quote"]) & (now - attempted >= p["cooldown"] * MINUTE))
    if p["direction"] != 0:
        valid &= intended == p["direction"]
    eligible = np.flatnonzero(valid)
    size = proportional_size(account, p["fraction"])
    room = p["slots"] - np.count_nonzero(held)
    margin = account.available_margin
    ready = set(account.ready)
    if margin is not None and size >= 10.:
        for i in eligible[np.argsort(-np.abs(gap[eligible]), kind="stable")]:
            if room <= 0 or margin < size * 1.01:
                break
            symbol = row.symbols[i]
            if symbol not in ready:
                continue
            action = orders.buy if intended[i] > 0 else orders.sell
            action(symbol, notional=size)
            attempted[i], initial[i] = now, price[i]
            room -= 1
            margin -= size * 1.01
    return {"mark_gap": gap[None, :]}


STRATEGY = Strategy("perpetual_mark_reversion", "1.0.0", {
    "direction": Parameter("integer", 0, choices=(-1, 0, 1)),
    "gap": Parameter("number", .005, .002, .04),
    "exit_gap": Parameter("number", .0005, 0., .002),
    "cap": Parameter("number", .045, .010, .15),
    "max_mark_move": Parameter("number", .003, .0005, .020),
    "max_trend": Parameter("number", .018, .004, .10),
    "min_quote": Parameter("number", 15_000., 0., 2_000_000.),
    "fresh_only": Parameter("boolean", True),
    "hold": Parameter("integer", 20, 3, 90),
    "cooldown": Parameter("integer", 90, 10, 240),
    "stop": Parameter("number", .012, .004, .08),
    "slots": Parameter("integer", 6, 2, 12),
    "fraction": Parameter("number", .075, .001, .15),
}, ("close", "mark", "quote_volume"), prepare, initialize, orders=trade)
