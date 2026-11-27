"""Schedule inventory hedges by the UTC start of the completed decision bar."""

from dataclasses import replace
from pathlib import Path

import numpy as np

from . import shock_breakout, shock_breakout_hedged as archived


def trade(state, row, account, orders):
    original, lead = state
    if lead is None:
        return {}
    p = original[0][0]
    symbol = row.symbols[lead]
    ready = tuple(name for name in account.ready if name != symbol)
    qty = account.positions["qty"]
    spot = row.values["close"][0]
    longs = qty > 0
    longs[lead] = False
    if row.status[0, lead] == 0 and np.isfinite(spot[lead]) and spot[lead] > 0:
        if not longs.any() and qty[lead]:
            orders.close(symbol)
        elif longs.any() and int(row.timestamps_ms[0]) % (p["hedge_hours"] * shock_breakout.HOUR) == 0:
            value = np.where(np.isfinite(spot) & (spot > 0), spot, account.positions["mark"])[longs]
            if np.all(np.isfinite(value) & (value > 0)):
                exposure = float(np.dot(qty[longs], value))
                orders.rebalance(symbol, target=-p["hedge_fraction"] * exposure,
                                 observable_price=spot[lead], booked_quantity=qty[lead],
                                 tolerance=max(10., .025 * account.equity))
    return archived.base.STRATEGY.orders(original, row, replace(account, ready=ready), orders)


STRATEGY = replace(archived.STRATEGY, version="8.1.0", orders=trade,
                   sources=(*archived.STRATEGY.sources, Path(archived.__file__)))
