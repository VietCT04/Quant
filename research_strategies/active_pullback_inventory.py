"""Trade independent hypotheses motivated by publicly observed active-market inventory changes."""

import numpy as np

import quant as q
from quant.strategy import Parameter, Plan, Plot, Strategy


MINUTE = 60_000
TICKS = 12


def prepare(p):
    e = q.features.expression
    def returns(periods):
        return e("quant.ts.pct_change", "close", periods=periods, units="fraction")

    reference_volume = e("quant.ts.rolling_sum", "quote_volume", window=60, units="USDT")
    graph = {
        "fast": returns(1),
        "swing": returns(5),
        "trend": e("quant.ts.lag", returns(60), periods=5, units="fraction"),
        "reference_volume": e("quant.ts.lag", reference_volume, periods=1, units="USDT"),
        "minute_quote": e("quant.ts.rolling_sum", "quote_volume", window=1, units="USDT"),
        "minute_buys": e("quant.ts.rolling_sum", "buy_quote_volume", window=1, units="USDT"),
    }
    return Plan(graph, warmup_bars=68 * TICKS, decision_ticks=TICKS, feature_periods=dict.fromkeys(graph, TICKS),
                plots=(Plot("entry_pressure", "One-minute taker imbalance", "fraction"),
                       Plot("activity_ratio", "Minute quote turnover relative to preceding hour", "ratio"),
                       Plot("phase", "Order decision", "code",
                            labels={1: "Open", 2: "Add", 3: "Reduce", 4: "Close"})))


def initialize(p, symbols, rng):
    n = len(symbols)
    return p, np.zeros(n, "i8"), np.zeros(n), np.zeros(n), np.full(n, np.nan), np.zeros(n, "i8")


def trade(state, row, account, orders):
    p, opened, prior_qty, prior_entry, last_add, last_order = state
    now = account.clock_ms
    v = row.values
    px, fast, swing, trend, quote, buy, ref = (
        v[key][0] for key in ("close", "fast", "swing", "trend", "minute_quote", "minute_buys", "reference_volume")
    )
    qty, entry = (account.positions[key] for key in ("qty", "entry"))
    side = np.sign(qty)
    held = qty != 0.
    increased = held & (np.abs(qty) > np.abs(prior_qty))
    new_position = increased & (prior_qty == 0.)
    opened[new_position] = now
    before, after = np.abs(prior_qty[increased]), np.abs(qty[increased])
    last_add[increased] = (after * entry[increased] - before * prior_entry[increased]) / (after - before)
    prior_qty[:] = qty
    prior_entry[:] = np.where(held, entry, 0.)
    pressure = np.divide(2 * buy - quote, quote, out=np.full_like(px, np.nan), where=quote > 0.)
    activity = np.divide(60 * quote, ref, out=np.full_like(px, np.nan), where=ref > 0.)
    valid = (row.status[0] == 0) & np.isfinite(px) & (px > 0.)
    direction = np.sign(trend)
    strong = np.abs(trend) >= p["trend"]
    aligned = direction * fast
    active = (activity >= p["activity"]) & (quote >= p["min_quote"])
    if p["mode"] == "pullback":
        event = (aligned <= -p["shock"]) & (direction * swing >= -p["max_swing"])
    else:
        event = (aligned >= p["shock"]) & (aligned <= p["max_swing"]) & (direction * pressure >= .06)
    eligible = valid & strong & active & event
    if p["side"]:
        eligible &= direction == p["side"]
    phase = np.full_like(px, np.nan)
    margin = account.available_margin
    ready = set(account.ready)
    room = p["slots"] - np.count_nonzero(held)
    for i in np.flatnonzero(valid & (eligible | held)):
        symbol = row.symbols[i]
        size = abs(qty[i]) * px[i]
        age = (now - opened[i]) / MINUTE
        elapsed = (now - last_order[i]) / MINUTE
        if held[i]:
            booked_return = side[i] * (px[i] / entry[i] - 1.)
            invalid = side[i] * trend[i] <= -p["trend"] * .4
            if age >= p["hold"] or booked_return <= -p["stop"] or invalid:
                orders.close(symbol)
                phase[i], last_order[i] = 4, now
                continue
            # Reduce inventory into a local move, including profitable trims or adverse swings.
            favorable = side[i] * swing[i] >= p["trim"] and booked_return >= p["trim"] * .5
            adverse = side[i] * swing[i] <= -2 * p["trim"] and p["mode"] == "impulse"
            if age >= 5 and elapsed >= p["spacing"] and (favorable or adverse):
                orders.reduce(symbol, fraction=min(1., p["order_size"] / size))
                phase[i], last_order[i] = 3, now
                continue
            if direction[i] != side[i] or age >= p["hold"] * .5:
                continue
            displacement = side[i] * (px[i] / last_add[i] - 1.)
            if not np.isfinite(displacement) or (displacement > -p["step"] if p["mode"] == "pullback"
                                                    else displacement < p["step"]):
                continue
        elif room <= 0 or elapsed < p["cooldown"]:
            continue
        if not eligible[i] or elapsed < p["spacing"] or symbol not in ready or size + p["order_size"] > p["inventory"]:
            continue
        if margin is None or margin < p["order_size"] * 1.01:
            continue
        action = orders.buy if direction[i] > 0 else orders.sell
        action(symbol, notional=p["order_size"])
        margin -= p["order_size"] * 1.01
        if not held[i]:
            room -= 1
        phase[i], last_order[i] = (2 if held[i] else 1), now
    return {"entry_pressure": pressure[None, :], "activity_ratio": activity[None, :], "phase": phase[None, :]}


STRATEGY = Strategy("active_pullback_inventory", "1.0.0", {
    "mode": Parameter("string", "pullback", choices=("pullback", "impulse")),
    "side": Parameter("integer", 0, choices=(-1, 0, 1)),
    "shock": Parameter("number", .003, .001, .04),
    "trend": Parameter("number", .02, .005, .15),
    "activity": Parameter("number", 1.5, 1., 5.),
    "min_quote": Parameter("number", 6_000., 1_000., 100_000.),
    "max_swing": Parameter("number", .015, .003, .08),
    "order_size": Parameter("number", 10., 10., 100.),
    "inventory": Parameter("number", 100., 25., 500.),
    "slots": Parameter("integer", 12, 2, 30),
    "hold": Parameter("integer", 180, 15, 720),
    "stop": Parameter("number", .028, .006, .12),
    "trim": Parameter("number", .004, .001, .03),
    "step": Parameter("number", .004, .001, .03),
    "spacing": Parameter("integer", 3, 1, 30),
    "cooldown": Parameter("integer", 15, 1, 120),
}, ("close", "quote_volume", "buy_quote_volume"), prepare, initialize, orders=trade)
