"""Accumulate into local or systemic price pressure against a frozen reference."""

import numpy as np

import quant as q
from quant.strategy import Parameter, Plan, Plot, Strategy


MINUTE = 60_000


def prepare(p):
    e = q.features.expression
    lag = lambda x, periods, units: e("quant.ts.lag", x, periods=periods, units=units)
    returns = lambda periods: e("quant.ts.pct_change", "close", periods=periods, units="fraction")
    features = {
        "move": returns(120),
        "recent": returns(24),
        "prior_trend": lag(returns(720), 120, "fraction"),
        "reference": lag(e("quant.ts.rolling_mean", "close", window=360, units="USDT/base"), 120, "USDT/base"),
        "prior_turnover": lag(e("quant.ts.rolling_mean", "quote_volume", window=360, units="USDT"), 120, "USDT"),
    }
    return Plan(features, warmup_bars=841, decision_ticks=p["cadence"], plots=(
        Plot("reference", "Pre-shock mean", "USDT/base", pane="price"),
        Plot("anchor", "Frozen pre-shock mean", "USDT/base", pane="price"),
        Plot("move", "Ten-minute return", "fraction"),
        Plot("shock", "Signed dislocation", "fraction"),
        Plot("phase_code", "Inventory decision", "code", labels={
            "1": "Initial order", "2": "Add on continued pressure", "3": "Reduce on rebound",
            "4": "Recovered to reference", "5": "Dislocation invalidated", "6": "Expiry",
        }),
    ))


def initialize(parameters, symbols, rng):
    n = len(symbols)
    return (parameters, np.full(n, np.nan), np.full(n, np.nan), np.full(n, np.nan),
            np.zeros(n, dtype="i8"), np.zeros(n, dtype="i8"), np.full(n, np.nan), np.zeros(n),
            np.zeros(n, dtype="u1"), np.full(n, np.nan))


def trade(state, row, account, orders):
    p, anchor, extreme, last_step, opened, cooldown, last_trim, previous, pending, decision_price = state
    now, side = account.clock_ms, p["side"]
    spot, move, recent, trend, ref, turnover = (
        row.values[k][0] for k in ("close", "move", "recent", "prior_trend", "reference", "prior_turnover")
    )
    qty = side * account.positions["qty"]
    held = qty > 0
    added = (pending == 2) & (qty > previous)
    trimmed = (pending == 3) & (qty < previous)
    last_step[added] = decision_price[added]
    last_trim[trimmed] = decision_price[trimmed]
    pending[:] = 0
    cooldown[(previous > 0) & ~held] = now + 120 * MINUTE
    previous[:] = qty
    valid = (row.status[0] == 0) & np.isfinite(spot) & (spot > 0)
    observed = valid & np.isfinite(move)
    market = float(np.median(move[observed])) if np.count_nonzero(observed) >= 20 else np.nan
    with np.errstate(divide="ignore", invalid="ignore"):
        shock = side * (ref - spot) / ref
    eligible = (
        observed & (shock >= p["gap"]) & (-side * move >= p["depth"])
        & (-side * recent >= .1 * p["depth"]) & (turnover >= p["turnover"])
        & (side * trend >= p["prior_alignment"]) & (side * market >= -.08)
    )
    if p["market"] == "isolated":
        eligible &= (-side * (move - market) >= .75 * p["depth"]) & (side * market >= -.006)
    else:
        eligible &= -side * market >= .004
    ready = set(account.ready)
    phase = np.full(len(spot), np.nan)
    margin = account.available_margin
    for i in np.flatnonzero((valid & held) | (eligible & ~held & (now >= cooldown))):
        symbol, price = row.symbols[i], spot[i]
        size = p["order_usdt"]
        if held[i]:
            loss, age = side * (anchor[i] - price) / anchor[i], (now - opened[i]) / MINUTE
            new_extreme = side * (price - extreme[i]) < 0
            if new_extreme:
                extreme[i] = price
            bounce = side * (price - extreme[i]) / extreme[i]
            excursion = side * (anchor[i] - extreme[i]) / anchor[i]
            since_trim = side * (price - last_trim[i]) / last_trim[i]
            if loss <= 0 or loss >= p["stop"] * p["gap"] or age >= p["hold"]:
                orders.close(symbol)
                phase[i] = 4 if loss <= 0 else 5 if loss >= p["stop"] * p["gap"] else 6
                continue
            if bounce >= p["recovery"] * excursion and (not np.isfinite(since_trim) or since_trim >= p["step"]):
                orders.reduce(symbol, fraction=min(1., size / (qty[i] * price)))
                decision_price[i], pending[i], phase[i] = price, 3, 3
                continue
            if (not new_extreme or age > p["add_minutes"] or loss > 2 * p["gap"]
                    or -side * recent[i] <= 0 or np.isfinite(last_trim[i])
                    or side * (last_step[i] - price) / price < p["step"]):
                continue
        elif symbol not in ready:
            continue
        if margin is None or margin < size * 1.01 or qty[i] * price + size > p["inventory"]:
            continue
        if side == 1:
            orders.buy(symbol, notional=size)
        else:
            orders.sell(symbol, notional=size)
        margin -= size * 1.01
        if held[i]:
            decision_price[i], pending[i], phase[i] = price, 2, 2
        else:
            opened[i], anchor[i], extreme[i], last_trim[i], last_step[i], phase[i] = now, ref[i], price, np.nan, price, 1
    return {"shock": shock[None, :], "anchor": np.where(held, anchor, np.nan)[None, :],
            "phase_code": phase[None, :]}


STRATEGY = Strategy("shock_liquidity_recovery", "1.0.0", {
    "side": Parameter("integer", 1, choices=(-1, 1)),
    "market": Parameter("string", "isolated", choices=("isolated", "systemic")),
    "cadence": Parameter("integer", 48, 12, 60),
    "depth": Parameter("number", .026, .01, .1),
    "gap": Parameter("number", .030, .01, .12),
    "prior_alignment": Parameter("number", -.007, -.06, .03),
    "turnover": Parameter("number", 400, 0, 100_000),
    "order_usdt": Parameter("number", 10, 10, 30),
    "inventory": Parameter("number", 100, 10, 300),
    "step": Parameter("number", .006, .002, .04),
    "hold": Parameter("integer", 1440, 60, 2880),
    "add_minutes": Parameter("integer", 90, 15, 180),
    "recovery": Parameter("number", .55, .3, .9),
    "stop": Parameter("number", 2.5, 2, 5),
}, ("close", "quote_volume"), prepare, initialize, orders=trade)
