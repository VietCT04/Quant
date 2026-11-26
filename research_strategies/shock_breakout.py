"""Test whether a fresh price shock starts a persistent repricing."""

import numpy as np

import quant as q
from quant.strategy import Parameter, Plan, Plot, Strategy
from quant.trading import proportional_size


HOUR = 3_600_000
HOURLY_TICKS = 720


def prepare(p):
    expression = q.features.expression
    impulse = expression("quant.ts.pct_change", "close", periods=1, units="fraction")
    trend = expression("quant.ts.pct_change", "close", periods=24, units="fraction")
    deviation = expression("quant.ts.rolling_std", impulse, window=48, units="fraction")
    features = {
        "impulse": impulse,
        "prior_trend": expression("quant.ts.lag", trend, periods=1, units="fraction"),
        "prior_vol": expression("quant.ts.lag", deviation, periods=1, units="fraction"),
    }
    return Plan(features, warmup_bars=50 * HOURLY_TICKS, decision_ticks=HOURLY_TICKS,
                feature_periods=dict.fromkeys(features, HOURLY_TICKS), plots=(
                    Plot("shock_score", "Signed shock relative to recent volatility", "ratio"),
                    Plot("extreme", "Trailing reference price", "USDT/base", pane="price"),
                ))


def initialize(p, symbols, rng):
    n = len(symbols)
    return p, np.zeros(n, dtype="i8"), np.full(n, np.nan), np.zeros(n, dtype="i8"), np.zeros(n, dtype=bool)


def trade(state, row, account, orders):
    p, opened, extreme, cooldown, previous = state
    now, side = account.clock_ms, p["side"]
    prices, impulse, trend, vol = (row.values[key][0] for key in ("close", "impulse", "prior_trend", "prior_vol"))
    qty = side * account.positions["qty"]
    held = qty > 0
    cooldown[previous & ~held] = now + 12 * HOUR
    previous[:] = held
    valid = (row.status[0] == 0) & np.isfinite(prices) & (prices > 0)
    for i in np.flatnonzero(held & valid):
        price = prices[i]
        if side * price > side * extreme[i]:
            extreme[i] = price
        retraced = side * (price / extreme[i] - 1) <= -p["trail"]
        if retraced or now - opened[i] >= p["hold"] * HOUR:
            orders.close(row.symbols[i])

    observed = valid & np.isfinite(impulse)
    if np.count_nonzero(observed) >= 20:
        market = np.median(impulse[observed])
    else:
        market = np.nan
    relative = side * (impulse - market)
    score = relative / np.maximum(.002, vol)
    eligible = (
        observed & ~held & (now >= cooldown)
        & (side * impulse >= p["depth"]) & (score >= p["sigma"])
        & (side * trend >= p["alignment"]) & (side * market >= p["market_floor"])
    )
    margin = account.available_margin
    if margin is not None:
        ready = set(account.ready)
        remaining = p["slots"] - np.count_nonzero(held)
        notional = proportional_size(account, p["fraction"])
        candidates = np.flatnonzero(eligible)
        ranked = candidates[np.argsort(-score[candidates], kind="stable")]
        for i in ranked:
            if remaining <= 0 or margin < 1.01 * notional or notional < 10.:
                break
            symbol = row.symbols[i]
            if symbol not in ready:
                continue
            if side > 0:
                orders.buy(symbol, notional=notional)
            else:
                orders.sell(symbol, notional=notional)
            opened[i], extreme[i] = now, prices[i]
            remaining -= 1
            margin -= 1.01 * notional
    return {"shock_score": score[None, :], "extreme": np.where(held, extreme, np.nan)[None, :]}


STRATEGY = Strategy("shock_repricing_breakout", "1.0.0", {
    "side": Parameter("integer", 1, choices=(-1, 1)),
    "depth": Parameter("number", .035, .01, .25),
    "sigma": Parameter("number", 2.5, 1, 10),
    "alignment": Parameter("number", -.02, -.30, .30),
    "market_floor": Parameter("number", -.007, -.10, .05),
    "hold": Parameter("integer", 72, 6, 336),
    "trail": Parameter("number", .06, .015, .40),
    "slots": Parameter("integer", 4, 1, 10),
    "fraction": Parameter("number", .24, .05, .80),
}, ("close",), prepare, initialize, orders=trade)
