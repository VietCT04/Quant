"""Scale continuation exposure using the observed return of the eligible market."""

from dataclasses import replace
from pathlib import Path

import numpy as np

from quant.strategy import Parameter, Plot
from quant.trading import Orders

from . import utc_turnover_conviction, utc_turnover_handoff, utc_turnover_transition, utc_turnover_weekend
from . import utc_turnover_conviction_transition as base


def prepare(p):
    plan = base.STRATEGY.prepare(p)
    return replace(plan, plots=(*plan.plots, Plot("market_context", "Market context size multiplier", "ratio")))


def trade(state, row, account, orders):
    p, _ = state
    planned = Orders(row.symbols)
    observed = base.trade(state, row, account, planned)
    weights = np.full(len(row.symbols), np.nan)
    if not any(order.kind == 0 for order in planned.items):
        orders.items.extend(planned.items)
        return observed | {"market_context": weights[None, :]}
    px, prior, turn = (row.values[key][0] for key in ("close", "prior_move", "turn"))
    valid = (row.status[0] == 0) & np.isfinite(px) & (px > 0) & np.isfinite(prior) & np.isfinite(turn)
    market = np.median(prior[valid])
    index = {symbol: i for i, symbol in enumerate(row.symbols)}
    for order in planned.items:
        if order.kind != 0:
            orders.items.append(order)
            continue
        side = np.sign(order.size)
        against = max(0., -side * market)
        strength = min(1., against / p["against_band"])
        weight = 1. - (1. - p["against_size"]) * strength
        weights[index[order.symbol]] = weight
        action = orders.buy if side > 0 else orders.sell
        action(order.symbol, notional=max(10., abs(order.size) * weight))
    return observed | {"market_context": weights[None, :]}


STRATEGY = replace(base.STRATEGY, version="2.2.0", prepare=prepare, orders=trade,
                   sources=tuple(Path(module.__file__) for module in (
                       base, utc_turnover_conviction, utc_turnover_handoff,
                       utc_turnover_transition, utc_turnover_weekend,
                   )),
                   parameters=base.STRATEGY.parameters | {
                       "against_band": Parameter("number", .005, .001, .02),
                       "against_size": Parameter("number", .40, .1, 1.),
                   })
