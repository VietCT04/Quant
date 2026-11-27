"""Preserve clocked entries and test dynamic intraday profit protection."""

from dataclasses import replace
from pathlib import Path

import numpy as np

from quant.strategy import Parameter, Plot
from quant.trading import Orders

from . import utc_turnover_handoff, utc_turnover_weekend as base


def prepare(p):
    plan = base.prepare(p)
    return replace(plan, plots=(*plan.plots, Plot("favorable_move", "Peak booked-direction return", "fraction")))


def initialize(p, symbols, rng):
    return base.STRATEGY.initialize(p, symbols, rng), np.zeros(len(symbols))


def trade(state, row, account, orders):
    original, favorable = state
    p, submitted = original
    qty, entry = (account.positions[key] for key in ("qty", "entry"))
    px = row.values["close"][0]
    held = qty != 0
    valid = (row.status[0] == 0) & np.isfinite(px) & (px > 0) & (entry > 0)
    mark = np.sign(qty) * (np.divide(px, entry, out=np.full_like(px, np.nan), where=valid) - 1.)
    active = held & valid
    favorable[~held] = 0.
    favorable[active] = np.maximum(favorable[active], mark[active])

    planned = Orders(row.symbols)
    observed = base.trade(original, row, account, planned)
    orders.items.extend(planned.items)
    already_closed = {order.symbol for order in planned.items if order.kind == 2}
    age = account.clock_ms - submitted
    trailing = (active & (age >= p["trail_delay"] * utc_turnover_handoff.HOUR)
                & (favorable >= p["arm_profit"]) & (favorable - mark >= p["trail_gap"]))
    for i in np.flatnonzero(trailing):
        if row.symbols[i] not in already_closed:
            orders.close(row.symbols[i])
    return observed | {"favorable_move": np.where(held, favorable, np.nan)[None, :]}


STRATEGY = replace(base.STRATEGY, version="1.5.0", prepare=prepare, initialize=initialize, orders=trade,
                   sources=(Path(utc_turnover_handoff.__file__), Path(base.__file__)),
                   parameters=base.STRATEGY.parameters | {
                       "arm_profit": Parameter("number", .018, .005, .05),
                       "trail_gap": Parameter("number", .012, .003, .035),
                       "trail_delay": Parameter("integer", 2, 1, 6),
                   })
