"""Follow a range escape after a contraction in realized volatility."""

import numpy as np

import quant as q
from quant.strategy import Parameter, Plan, Plot, Strategy
from quant.trading import proportional_size


HOUR = 3_600_000
TICKS = 1440


def prepare(p):
    e = q.features.expression

    def prior(node):
        return e("quant.ts.lag", node, periods=1, units=node.units)

    returns = e("quant.ts.pct_change", "close", periods=1, units="fraction")
    graph = {
        "short_vol": prior(e("quant.ts.rolling_std", returns, window=12, units="fraction")),
        "long_vol": prior(e("quant.ts.rolling_std", returns, window=72, units="fraction")),
        "upper": prior(e("quant.ts.rolling_max", "high", window=12, units="USDT/base")),
        "lower": prior(e("quant.ts.rolling_min", "low", window=12, units="USDT/base")),
        "quote_now": e("quant.ts.rolling_sum", "quote_volume", window=1, units="USDT"),
        "quote_prior": prior(e("quant.ts.rolling_mean", "quote_volume", window=24, units="USDT")),
    }
    return Plan(graph, warmup_bars=74 * TICKS, decision_ticks=TICKS,
                feature_periods=dict.fromkeys(graph, TICKS), plots=(
                    Plot("upper", "Previous 24-hour high", "USDT/base", pane="price"),
                    Plot("lower", "Previous 24-hour low", "USDT/base", pane="price"),
                    Plot("compression", "Prior volatility ratio", "ratio"),
                ))


def initialize(p, symbols, rng):
    return p, np.full(len(symbols), -(1 << 60), dtype="i8"), np.full(len(symbols), np.nan)


def trade(state, row, account, orders):
    p, attempted, anchor = state
    close, short_vol, long_vol, upper, lower, quote, prior = (row.values[name][0] for name in (
        "close", "short_vol", "long_vol", "upper", "lower", "quote_now", "quote_prior"))
    qty, booked_entry = (account.positions[key] for key in ("qty", "entry"))
    now = account.clock_ms
    valid = (row.status[0] == 0) & np.isfinite(close) & (close > 0)
    held = qty != 0
    age = now - attempted
    side = np.sign(qty)
    entry_move = side * (np.divide(close, booked_entry, out=np.full_like(close, np.nan), where=booked_entry > 0)-1.)
    for i in np.flatnonzero(valid & held & ((age >= p["hold"] * HOUR) | (entry_move <= -p["stop"])
                                             | ((age >= 4 * HOUR) & (side * (close-anchor) < 0)))):
        orders.close(row.symbols[i])
    ratio = np.divide(short_vol, long_vol, out=np.full_like(close, np.nan), where=long_vol > .0015)
    up = np.divide(close, upper, out=np.full_like(close, np.nan), where=upper > 0) - 1.
    down = 1. - np.divide(close, lower, out=np.full_like(close, np.nan), where=lower > 0)
    direction = np.where(up >= p["break_min"], 1, np.where(down >= p["break_min"], -1, 0))
    breakout = np.where(direction > 0, up, down)
    burst = np.divide(quote, prior, out=np.full_like(close, np.nan), where=prior > 0)
    eligible = (valid & ~held & (age >= p["cooldown"] * HOUR) & (direction != 0)
                & (ratio <= p["ratio"]) & (breakout <= p["chase"])
                & (quote >= p["min_quote"]) & (burst >= p["activity"]))
    if p["direction"]:
        eligible &= direction == p["direction"]
    idx = np.flatnonzero(eligible)
    score = breakout[idx] / np.maximum(long_vol[idx], .003) * np.sqrt(np.minimum(burst[idx], 6.))
    size = proportional_size(account, p["fraction"])
    margin = account.available_margin
    ready = set(account.ready)
    room = p["slots"] - np.count_nonzero(held)
    if margin is not None and size >= 10.:
        for i in idx[np.argsort(-score, kind="stable")]:
            if room <= 0 or margin < 1.01 * size:
                break
            if row.symbols[i] not in ready:
                continue
            action = orders.buy if direction[i] > 0 else orders.sell
            action(row.symbols[i], notional=size)
            anchor[i], attempted[i] = (upper[i] if direction[i] > 0 else lower[i]), now
            margin -= 1.01 * size
            room -= 1
    return {"compression": ratio[None, :]}


STRATEGY = Strategy("quiet_range_breakout", "1.0.0", {
    "direction": Parameter("integer", 1, choices=(-1, 0, 1)),
    "ratio": Parameter("number", .65, .3, .95),
    "break_min": Parameter("number", .004, .001, .02),
    "chase": Parameter("number", .035, .015, .18),
    "activity": Parameter("number", 1.6, .8, 6.),
    "min_quote": Parameter("number", 500_000., 100_000., 15_000_000.),
    "hold": Parameter("integer", 32, 8, 120),
    "cooldown": Parameter("integer", 42, 6, 120),
    "stop": Parameter("number", .042, .01, .15),
    "slots": Parameter("integer", 8, 3, 14),
    "fraction": Parameter("number", .082, .02, .16),
}, ("close", "high", "low", "quote_volume"), prepare, initialize, orders=trade)
