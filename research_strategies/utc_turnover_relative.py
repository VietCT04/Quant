"""Size the clocked turnover signal by strength relative to its current peer set."""

from dataclasses import replace
from pathlib import Path

import numpy as np

from quant.strategy import Parameter, Plot
from quant.trading import Orders

from . import utc_turnover_handoff, utc_turnover_weekend as base


def prepare(p):
    plan = base.prepare(p)
    return replace(plan, plots=(*plan.plots, Plot("relative_risk", "Peer-relative size multiplier", "ratio")))


def trade(state, row, account, orders):
    p = state[0]
    px, previous, last = (row.values[name][0] for name in ("close", "prior_move", "turn"))
    observed = (row.status[0] == 0) & np.isfinite(px) & (px > 0) & np.isfinite(previous) & np.isfinite(last)
    center = np.median(previous[observed]) if np.count_nonzero(observed) >= 20 else np.nan
    planned = Orders(row.symbols)
    trace = base.trade(state, row, account, planned)
    indices = {symbol: i for i, symbol in enumerate(row.symbols)}
    weights = np.full(len(indices), np.nan)
    for order in planned.items:
        if order.kind != 0:
            orders.items.append(order)
            continue
        i = indices[order.symbol]
        difference = np.sign(order.size) * (previous[i] - center)
        strength = np.clip((difference - p["relative_start"]) / p["relative_width"], 0., 1.)
        weights[i] = p["weak_size"] + (1. - p["weak_size"]) * strength
        notional = max(10., abs(order.size) * weights[i])
        action = orders.buy if order.size > 0 else orders.sell
        action(order.symbol, notional=notional)
    return trace | {"relative_risk": weights[None, :]}


STRATEGY = replace(base.STRATEGY, version="2.0.0", prepare=prepare, orders=trade,
                   sources=(Path(utc_turnover_handoff.__file__), Path(base.__file__)),
                   parameters=base.STRATEGY.parameters | {
                       "weak_size": Parameter("number", .35, .15, .85),
                       "relative_start": Parameter("number", .008, 0., .035),
                       "relative_width": Parameter("number", .019, .003, .06),
                   })
