"""Size fixed-clock entries from the strength of their preceding price move."""

from dataclasses import replace
from pathlib import Path

import numpy as np

from quant.strategy import Parameter, Plot
from quant.trading import Orders

from . import utc_turnover_handoff, utc_turnover_weekend as base


def prepare(p):
    plan = base.prepare(p)
    return replace(plan, plots=(*plan.plots, Plot("move_risk", "Preceding directional move size multiplier", "ratio")))


def trade(state, row, account, orders):
    p = state[0]
    move = row.values["prior_move"][0]
    planned = Orders(row.symbols)
    observations = base.trade(state, row, account, planned)
    index = {symbol: i for i, symbol in enumerate(row.symbols)}
    weights = np.full(len(index), np.nan)
    for order in planned.items:
        if order.kind != 0:
            orders.items.append(order)
            continue
        i = index[order.symbol]
        directional = np.sign(order.size) * move[i]
        strength = np.clip((directional - p["drift"]) / p["move_band"], 0., 1.)
        weight = weights[i] = p["weak_size"] + (1. - p["weak_size"]) * strength
        size = max(10., abs(order.size) * weight)
        action = orders.buy if order.size > 0 else orders.sell
        action(order.symbol, notional=size)
    return observations | {"move_risk": weights[None, :]}


STRATEGY = replace(base.STRATEGY, version="1.6.0", prepare=prepare, orders=trade,
                   sources=(Path(utc_turnover_handoff.__file__), Path(base.__file__)),
                   parameters=base.STRATEGY.parameters | {
                       "weak_size": Parameter("number", .5, .15, .95),
                       "move_band": Parameter("number", .013, .003, .05),
                   })
