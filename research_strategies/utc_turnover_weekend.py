"""Test whether UTC turnover continuation survives weekend-focused risk sizing."""

from dataclasses import replace
from pathlib import Path

import numpy as np

from marketdata.model import DAY
from quant.strategy import Parameter, Plot
from quant.trading import Orders, proportional_size

from . import utc_turnover_handoff as base


WINDOWS = {
    "14": (14,),
    "13-15": (13, 14, 15),
    "12-16": (12, 13, 14, 15, 16),
    "14-20": (14, 17, 20),
}


def prepare(p):
    plan = base.prepare(p)
    return replace(plan, plots=(*plan.plots, Plot("entry_weight", "Day-of-week order size multiplier", "ratio")))


def trade(state, row, account, orders):
    p, attempts = state
    ts = int(row.timestamps_ms[0])
    day = ts // DAY
    hour = ts // base.HOUR % 24
    weekend = (day + 3) % 7 >= 5
    weight = 1. if weekend else p["weekday_weight"]
    allowed_hours = WINDOWS[p["clock_window"]] if weekend else (p["utc_hour"],)
    ready = ()
    if hour in allowed_hours and weight * proportional_size(account, p["fraction"]) >= 10.:
        observed = set(account.ready)
        ready = tuple(symbol for symbol, attempted in zip(row.symbols, attempts, strict=True)
                      if attempted // DAY != day and symbol in observed)
    plan = Orders(row.symbols)
    parameters = p | {"utc_hour": hour} if ready else p
    result = base.trade((parameters, attempts), row, replace(account, ready=ready), plan)
    for order in plan.items:
        if order.kind == 0:
            size = abs(order.size) * weight
            if size >= 10.:
                action = orders.buy if order.size > 0 else orders.sell
                action(order.symbol, notional=size)
        else:
            orders.items.append(order)
    return result | {"entry_weight": np.full((1, len(row.symbols)), weight)}


STRATEGY = replace(
    base.STRATEGY, version="1.1.0", prepare=prepare, orders=trade, sources=(Path(base.__file__),),
    parameters=base.STRATEGY.parameters | {
        "clock_window": Parameter("string", "13-15", choices=tuple(WINDOWS)),
        "weekday_weight": Parameter("number", .2, 0., 1.),
    },
)
