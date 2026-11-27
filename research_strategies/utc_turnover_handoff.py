"""Test directional persistence at fixed UTC clocks when observed turnover picks up."""

import numpy as np

import quant as q
from quant.strategy import Parameter, Plan, Plot, Strategy
from quant.trading import proportional_size


HOUR = 3_600_000
TICKS = 720


def prepare(p):
    e = q.features.expression

    def ret(periods):
        return e("quant.ts.pct_change", "close", periods=periods, units="fraction")

    graph = {
        "prior_move": e("quant.ts.lag", ret(6), periods=1, units="fraction"),
        "turn": ret(1),
        "quote_hour": e("quant.ts.rolling_sum", "quote_volume", window=1, units="USDT"),
        "same_clock_quote": e("quant.ts.lag", "quote_volume", periods=24, units="USDT"),
    }
    return Plan(graph, warmup_bars=26 * TICKS, decision_ticks=TICKS,
                feature_periods=dict.fromkeys(graph, TICKS), plots=(
                    Plot("prior_move", "Six-hour return before observed turnover", "fraction"),
                    Plot("turnover_ratio", "Observed turnover versus preceding day at the same hour", "ratio"),
                ))


def initialize(p, symbols, rng):
    return p, np.full(len(symbols), -(1 << 60), dtype="i8")


def trade(state, row, account, orders):
    p, submitted = state
    px, previous, turn, quote, prior_quote = (row.values[k][0] for k in
                                             ("close", "prior_move", "turn", "quote_hour", "same_clock_quote"))
    qty, entry = (account.positions[key] for key in ("qty", "entry"))
    held = qty != 0
    valid = (row.status[0] == 0) & np.isfinite(px) & (px > 0)
    side = np.sign(qty)
    pnl = side * (np.divide(px, entry, out=np.full_like(px, np.nan), where=entry > 0) - 1.)
    age = account.clock_ms - submitted
    for i in np.flatnonzero(valid & held & ((age >= p["hold"] * HOUR) | (pnl <= -p["stop"]))):
        orders.close(row.symbols[i])
    activity = np.divide(quote, prior_quote, out=np.full_like(px, np.nan), where=prior_quote > 0)
    result = {"turnover_ratio": activity[None, :]}
    if int(row.timestamps_ms[0]) // HOUR % 24 != p["utc_hour"]:
        return result
    observed = valid & np.isfinite(previous) & np.isfinite(turn)
    if np.count_nonzero(observed) < 20:
        return result
    market = np.median(previous[observed])
    direction = np.sign(previous - market)
    eligible = (observed & ~held & (direction * previous >= p["drift"])
                & (direction * market >= -p["market_against"]) & (direction * turn >= p["confirmation"])
                & (direction * turn <= p["chase"])
                & (quote >= p["min_quote"]) & (activity >= p["activity"]))
    if p["direction"]:
        eligible &= direction == p["direction"]
    candidates = np.flatnonzero(eligible)
    score = np.abs(previous[candidates] - market) * np.sqrt(np.minimum(activity[candidates], 8.))
    notional = proportional_size(account, p["fraction"])
    margin = account.available_margin
    ready = set(account.ready)
    room = p["slots"] - np.count_nonzero(held)
    if margin is not None and notional >= 10.:
        for i in candidates[np.argsort(-score, kind="stable")]:
            if room <= 0 or margin < 1.01 * notional:
                break
            if row.symbols[i] not in ready:
                continue
            action = orders.buy if direction[i] > 0 else orders.sell
            action(row.symbols[i], notional=notional)
            submitted[i] = account.clock_ms
            margin -= 1.01 * notional
            room -= 1
    return result


STRATEGY = Strategy("fixed_clock_turnover_handoff", "1.0.0", {
    "utc_hour": Parameter("integer", 14, choices=(8, 14, 20)),
    "direction": Parameter("integer", 1, choices=(-1, 0, 1)),
    "drift": Parameter("number", .011, .003, .065),
    "market_against": Parameter("number", .009, .001, .035),
    "confirmation": Parameter("number", .002, .0005, .018),
    "chase": Parameter("number", .035, .010, .080),
    "activity": Parameter("number", 1.45, 1.05, 4.),
    "min_quote": Parameter("number", 750_000., 150_000., 15_000_000.),
    "hold": Parameter("integer", 8, 3, 16),
    "stop": Parameter("number", .028, .010, .090),
    "slots": Parameter("integer", 9, 4, 14),
    "fraction": Parameter("number", .065, .02, .12),
}, ("close", "quote_volume"), prepare, initialize, orders=trade)
