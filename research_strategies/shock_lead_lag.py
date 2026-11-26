"""Test BTC-led shock propagation and resilience after broad capitulation."""

import numpy as np

import quant as q
from quant.strategy import Parameter, Plan, Plot, Strategy
from quant.trading import proportional_size


HOUR = 3_600_000
TICKS = 720


def prepare(p):
    e = q.features.expression
    returns = lambda hours: e("quant.ts.pct_change", "close", periods=hours, units="fraction")
    features = {
        "hour": returns(1),
        "prior_six": e("quant.ts.lag", returns(6), periods=1, units="fraction"),
        "prior_day": e("quant.ts.lag", returns(24), periods=1, units="fraction"),
    }
    return Plan(features, warmup_bars=26 * TICKS, decision_ticks=TICKS,
                feature_periods=dict.fromkeys(features, TICKS), plots=(
                    Plot("candidate_score", "Cross-sectional entry score", "fraction"),
                    Plot("trailing_price", "Trailing reference price", "USDT/base", pane="price"),
                ))


def initialize(p, symbols, rng):
    n = len(symbols)
    return p, symbols.index("BTCUSDT") if "BTCUSDT" in symbols else None, np.zeros(n, dtype="i8"), np.full(n, np.nan)


def scores(p, leader, hour, prior_six, prior_day, valid):
    side = p["side"]
    if leader is None or not valid[leader]:
        return np.full(len(hour), np.nan)
    if p["mode"] == "propagation":
        pulse = side * hour[leader]
        lag = pulse - side * hour
        eligible = valid & (pulse >= p["shock"]) & (lag >= p["relative"]) & (side * hour >= -.004)
        return np.where(eligible & (side * prior_day >= -.01), lag + .10 * side * prior_day, np.nan)
    market = valid & np.isfinite(prior_six)
    if np.count_nonzero(market) < 20:
        return np.full(len(hour), np.nan)
    drawdown = np.median(prior_six[market])
    resilience = prior_six - drawdown
    return np.where(valid & (drawdown <= -p["shock"]) & (hour[leader] > 0)
                    & (hour > 0) & (resilience >= p["relative"]), resilience, np.nan)


def trade(state, row, account, orders):
    p, leader, submitted, peak = state
    side = 1 if p["mode"] == "resilience" else p["side"]
    spot, hour, prior_six, prior_day = (row.values[k][0] for k in ("close", "hour", "prior_six", "prior_day"))
    valid = (row.status[0] == 0) & np.isfinite(spot) & (spot > 0)
    held = side * account.positions["qty"] > 0
    for i in np.flatnonzero(held & valid):
        price = spot[i]
        if side * price > side * peak[i]:
            peak[i] = price
        if side * (price / peak[i] - 1) <= -p["trail"] or account.clock_ms - submitted[i] >= p["hold"] * HOUR:
            orders.close(row.symbols[i])
    observed = valid & np.isfinite(hour)
    score = scores(p, leader, hour, prior_six, prior_day, observed)
    eligible = observed & ~held & np.isfinite(score)
    if leader is not None:
        eligible[leader] = False
    margin = account.available_margin
    if margin is not None:
        ready = set(account.ready)
        slots = p["slots"] - np.count_nonzero(held)
        size = proportional_size(account, p["fraction"])
        indices = np.flatnonzero(eligible)
        for i in indices[np.argsort(-score[indices], kind="stable")]:
            if slots <= 0 or margin < 1.01 * size or size < 10.:
                break
            symbol = row.symbols[i]
            if symbol not in ready:
                continue
            if side > 0:
                orders.buy(symbol, notional=size)
            else:
                orders.sell(symbol, notional=size)
            submitted[i], peak[i] = account.clock_ms, spot[i]
            slots -= 1
            margin -= 1.01 * size
    return {"candidate_score": score[None, :], "trailing_price": np.where(held, peak, np.nan)[None, :]}


STRATEGY = Strategy("shock_lead_lag", "1.0.0", {
    "mode": Parameter("string", "propagation", choices=("propagation", "resilience")),
    "side": Parameter("integer", 1, choices=(-1, 1)),
    "shock": Parameter("number", .02, .005, .20),
    "relative": Parameter("number", .008, .001, .12),
    "hold": Parameter("integer", 48, 4, 336),
    "trail": Parameter("number", .06, .015, .30),
    "slots": Parameter("integer", 4, 1, 8),
    "fraction": Parameter("number", .24, .05, .80),
}, ("close",), prepare, initialize, orders=trade)
