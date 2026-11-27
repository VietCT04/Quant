"""Test independently confirmed inventory handoffs in active markets."""

from dataclasses import replace
from pathlib import Path

import numpy as np

from quant.strategy import Parameter, Plot
from quant.trading import Orders

from . import active_pullback_inventory as base


def prepare(p):
    plan = base.prepare(p)
    return replace(plan, plots=(*plan.plots, Plot("handoff", "Confirmed opposite pressure", "flag")))


def trade(state, row, account, orders):
    p, opened, previous_qty, _, _, last_order = state
    qty = account.positions["qty"]
    reversed_since_observation = qty * previous_qty < 0.
    previous_qty[reversed_since_observation] = 0.
    planned = Orders(row.symbols)
    observations = base.trade(state, row, account, planned)
    v = row.values
    px, fast, swing, trend, quote = (v[k][0] for k in ("close", "fast", "swing", "trend", "minute_quote"))
    pressure = observations["entry_pressure"][0]
    activity = observations["activity_ratio"][0]
    side = np.sign(qty)
    age = account.clock_ms - opened
    qualifies = ((qty != 0.) & (row.status[0] == 0) & (px > 0.) & (age >= p["minimum_age"] * base.MINUTE)
                 & (side * fast <= -p["shock"]) & (side * swing <= -p["handoff_move"])
                 & (side * pressure <= -.12) & (side * trend <= p["trend"] * .5)
                 & (activity >= p["activity"]) & (quote >= p["min_quote"]))
    ready = set(account.ready)
    index = {symbol: i for i, symbol in enumerate(row.symbols)}
    handed_off = np.zeros(len(row.symbols), dtype=float)
    margin = account.available_margin or 0.
    for order in planned.items:
        i = index[order.symbol]
        if order.kind == 0:
            margin -= abs(order.size) * 1.01
        if (order.kind != 2 or not qualifies[i] or order.symbol not in ready
                or margin < p["order_size"] * 1.25):
            orders.items.append(order)
            continue
        action = orders.sell if side[i] > 0 else orders.buy
        # Convert only the new inventory leg at the observable mark. Headroom
        # above 10 USDT leaves room for a small subsequent open-price change.
        action(order.symbol, quantity=abs(qty[i]) + p["order_size"] * 1.15 / px[i])
        margin -= p["order_size"] * 1.25
        last_order[i] = account.clock_ms
        handed_off[i] = 1.
    return observations | {"handoff": np.where(qty != 0, handed_off, np.nan)[None, :]}


STRATEGY = replace(base.STRATEGY, version="2.0.0", prepare=prepare, orders=trade,
                   sources=(Path(base.__file__),), parameters=base.STRATEGY.parameters | {
                       "minimum_age": Parameter("integer", 30, 5, 360),
                       "handoff_move": Parameter("number", .009, .004, .04),
                   })
