"""Exit a clocked continuation when completed returns invalidate the entry."""

from dataclasses import replace
from pathlib import Path

import numpy as np

from quant.strategy import Parameter, Plot
from quant.trading import Orders

from . import utc_turnover_conviction, utc_turnover_handoff, utc_turnover_transition, utc_turnover_weekend
from . import utc_turnover_conviction_transition as base


def prepare(p):
    plan = base.STRATEGY.prepare(p)
    return replace(plan, plots=(*plan.plots, Plot("invalidation", "Continuation invalidated at close", "boolean")))


def trade(state, row, account, orders):
    p, submitted = state
    planned = Orders(row.symbols)
    observed = base.trade(state, row, account, planned)
    orders.items.extend(planned.items)
    already_closed = {order.symbol for order in planned.items if order.kind == 2}
    qty, entry = (account.positions[key] for key in ("qty", "entry"))
    px, turn = (row.values[key][0] for key in ("close", "turn"))
    active = (qty != 0) & (row.status[0] == 0) & np.isfinite(px) & (px > 0) & (entry > 0)
    side = np.sign(qty)
    mark = side * (np.divide(px, entry, out=np.full_like(px, np.nan), where=active) - 1.)
    age = account.clock_ms - submitted
    invalid = (active & (age >= p["check_after"] * utc_turnover_handoff.HOUR)
               & (mark <= -p["adverse_mark"]) & (side * turn <= -p["opposing_hour"]))
    for i in np.flatnonzero(invalid):
        if row.symbols[i] not in already_closed:
            orders.close(row.symbols[i])
    return observed | {"invalidation": np.where(active, invalid, np.nan)[None, :]}


STRATEGY = replace(base.STRATEGY, version="2.1.0", prepare=prepare, orders=trade,
                   sources=tuple(Path(module.__file__) for module in (
                       base, utc_turnover_conviction, utc_turnover_handoff,
                       utc_turnover_transition, utc_turnover_weekend,
                   )),
                   parameters=base.STRATEGY.parameters | {
                       "check_after": Parameter("integer", 2, 1, 6),
                       "adverse_mark": Parameter("number", .006, .001, .025),
                       "opposing_hour": Parameter("number", .005, .001, .025),
                   })
