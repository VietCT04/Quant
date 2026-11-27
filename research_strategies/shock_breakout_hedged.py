"""Hedge part of the booked rolling-shock inventory with BTCUSDT."""

from dataclasses import replace
from pathlib import Path

import numpy as np

from quant.strategy import Parameter

from . import shock_breakout, shock_breakout_distributed, shock_breakout_rolling as base


def initialize(p, symbols, rng):
    lead = symbols.index("BTCUSDT") if "BTCUSDT" in symbols else None
    return base.STRATEGY.initialize(p, symbols, rng), lead


def trade(state, row, account, orders):
    original, lead = state
    if lead is None:
        return {}
    p = original[0][0]
    symbol = row.symbols[lead]
    ready = tuple(item for item in account.ready if item != symbol)
    position = account.positions
    spot = row.values["close"][0]
    qty = position["qty"]
    held = np.maximum(qty, 0.)
    held[lead] = 0.
    if row.status[0, lead] == 0 and np.isfinite(spot[lead]) and spot[lead] > 0:
        if not np.any(held > 0):
            if qty[lead]:
                orders.close(symbol)
        elif account.clock_ms % (p["hedge_hours"] * shock_breakout.HOUR) == 0:
            # Marks can value inventory with an unavailable current trade bar.
            value = np.where(np.isfinite(spot) & (spot > 0), spot, position["mark"])
            marked = (held > 0) & np.isfinite(value) & (value > 0)
            if np.count_nonzero(marked) == np.count_nonzero(held):
                exposure = float(np.dot(held[marked], value[marked]))
                orders.rebalance(symbol, target=-p["hedge_fraction"] * exposure, observable_price=spot[lead],
                                 booked_quantity=qty[lead], tolerance=max(10., .025 * account.equity))
    return base.STRATEGY.orders(original, row, replace(account, ready=ready), orders)


STRATEGY = replace(
    base.STRATEGY, version="8.0.0", initialize=initialize, orders=trade,
    sources=(Path(shock_breakout.__file__), Path(shock_breakout_distributed.__file__), Path(base.__file__)),
    parameters=base.STRATEGY.parameters | {
        "side": Parameter("integer", 1, choices=(1,)),
        "hedge_fraction": Parameter("number", .35, .05, .9),
        "hedge_hours": Parameter("integer", 4, choices=(2, 4, 6)),
    },
)
