"""Fade completed range probes that have already reclaimed the boundary."""

import numpy as np

import quant as q
from quant.strategy import Parameter, Plan, Plot, Strategy
from quant.trading import proportional_size


HOUR = 3_600_000
TICKS = 720


def prepare(p):
    e = q.features.expression
    unit = "USDT/base"
    graph = {
        "lower": e("quant.ts.lag", e("quant.ts.rolling_min", "low", window=p["lookback"], units=unit),
                   periods=2, units=unit),
        "upper": e("quant.ts.lag", e("quant.ts.rolling_max", "high", window=p["lookback"], units=unit),
                   periods=2, units=unit),
        "probe_low": e("quant.ts.lag", "low", periods=1, units=unit),
        "probe_high": e("quant.ts.lag", "high", periods=1, units=unit),
        "drift": e("quant.ts.lag", e("quant.ts.pct_change", "close", periods=24, units="fraction"),
                   periods=2, units="fraction"),
    }
    return Plan(graph, warmup_bars=52 * TICKS, decision_ticks=TICKS,
                feature_periods=dict.fromkeys(graph, TICKS), plots=(
                    Plot("lower", "Pre-probe lower boundary", unit, pane="price"),
                    Plot("upper", "Pre-probe upper boundary", unit, pane="price"),
                ))


def initialize(p, symbols, rng):
    n = len(symbols)
    return p, np.zeros(n, dtype="i8"), np.full(n, np.nan), np.full(n, np.nan), np.zeros(n, dtype=bool), np.zeros(n, dtype="i8")


def trade(state, row, account, orders):
    p, opened, target, stop, was_held, cooldown = state
    price, lower, upper, low, high, drift = (row.values[k][0] for k in
                                            ("close", "lower", "upper", "probe_low", "probe_high", "drift"))
    qty = account.positions["qty"]
    held = qty != 0
    now = account.clock_ms
    cooldown[was_held & ~held] = now + 8 * HOUR
    was_held[:] = held
    valid = (row.status[0] == 0) & np.isfinite(price) & (price > 0)
    for i in np.flatnonzero(held & valid):
        side = np.sign(qty[i])
        if side * (price[i]-target[i]) >= 0 or side * (price[i]-stop[i]) <= 0 or now-opened[i] >= p["hold"] * HOUR:
            orders.close(row.symbols[i])

    with np.errstate(divide="ignore", invalid="ignore"):
        down_probe = 1 - low / lower
        up_probe = high / upper - 1
        gap_long = price / lower - 1
        gap_short = 1 - price / upper
    center = (lower + upper) * .5
    aims = ((price + center) * .5 if p["quick"] else center)
    long = ((down_probe >= p["probe"]) & (gap_long >= 0.) & (gap_long <= p["chase"])
            & (aims >= price * (1 + p["target_min"])) & (drift >= -p["drift_cap"]))
    short = ((up_probe >= p["probe"]) & (gap_short >= 0.) & (gap_short <= p["chase"])
             & (aims <= price * (1 - p["target_min"])) & (drift <= p["drift_cap"]))
    score = np.where(long, down_probe, np.where(short & p["short"], up_probe, np.nan))
    ready = set(account.ready)
    candidates = np.flatnonzero(valid & ~held & (now >= cooldown) & np.isfinite(score))
    size = proportional_size(account, p["fraction"])
    margin = account.available_margin
    room = p["slots"] - np.count_nonzero(held)
    if margin is not None and size >= 10.:
        for i in candidates[np.argsort(-score[candidates], kind="stable")]:
            if room <= 0 or margin < size * 1.01:
                break
            symbol = row.symbols[i]
            if symbol not in ready:
                continue
            if long[i]:
                orders.buy(symbol, notional=size)
                stop[i] = max(low[i] * .995, price[i] * (1 - p["loss_cap"]))
            else:
                orders.sell(symbol, notional=size)
                stop[i] = min(high[i] * 1.005, price[i] * (1 + p["loss_cap"]))
            opened[i], target[i] = now, aims[i]
            room -= 1
            margin -= size * 1.01
    return {}


STRATEGY = Strategy("failed_range_reclaim", "1.0.0", {
    "lookback": Parameter("integer", 24, choices=(12, 24, 48)),
    "probe": Parameter("number", .008, .001, .12),
    "chase": Parameter("number", .02, .003, .08),
    "drift_cap": Parameter("number", .10, .01, .35),
    "target_min": Parameter("number", .012, .003, .08),
    "loss_cap": Parameter("number", .045, .01, .15),
    "quick": Parameter("boolean", False),
    "short": Parameter("boolean", False),
    "hold": Parameter("integer", 24, 6, 72),
    "fraction": Parameter("number", .09, .025, .20),
    "slots": Parameter("integer", 8, 2, 10),
}, ("close", "high", "low"), prepare, initialize, orders=trade)
