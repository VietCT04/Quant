"""Trade rolling shocks with causal trend, breadth, and volatility controls."""

from dataclasses import replace
from pathlib import Path

import numpy as np

import quant as q
from quant.strategy import Parameter, Plot
from quant.trading import Orders

from . import shock_breakout, shock_breakout_distributed, shock_breakout_rolling as base


def prepare(p):
    plan = base.prepare(p)
    e = q.features.expression
    step = p["interval_minutes"]
    past = e("quant.ts.pct_change", "close", periods=2880 // step, units="fraction")
    trend = e("quant.ts.lag", past, periods=60 // step, units="fraction")
    return replace(plan, features=plan.features | {"context_return": trend},
                   feature_periods=plan.feature_periods | {"context_return": plan.decision_ticks},
                   plots=(*plan.plots, Plot("context_return", "48-hour pre-shock return", "fraction")))


def trade(state, row, account, orders):
    p = state[0][0]
    impulse = row.values["impulse"][0]
    prior_day = row.values["prior_trend"][0]
    context = row.values["context_return"][0]
    vol = row.values["prior_vol"][0]
    side = p["side"]
    observed = (row.status[0] == 0) & np.isfinite(prior_day)
    if np.count_nonzero(observed) >= 20:
        breadth = np.mean(side * prior_day[observed] > 0)
    else:
        breadth = 0.
    allowed = (
        np.isfinite(context) & (side * context >= p["context_min"])
        & (side * impulse <= p["max_shock"]) & (breadth >= p["breadth_min"])
    )
    eligible = {symbol for symbol, ok in zip(row.symbols, allowed, strict=True) if ok}
    ready = tuple(symbol for symbol in account.ready if symbol in eligible)
    if p["vol_target"] == 0:
        return base.STRATEGY.orders(state, row, replace(account, ready=ready), orders)
    planned = Orders(row.symbols)
    result = base.STRATEGY.orders(state, row, replace(account, ready=ready), planned)
    volatility = dict(zip(row.symbols, vol, strict=True))
    for order in planned.items:
        if order.kind:
            orders.items.append(order)
            continue
        cap = min(1., p["vol_target"] / max(.002, volatility[order.symbol]))
        size = cap * abs(order.size)
        if size >= 10.:
            action = orders.buy if order.size > 0 else orders.sell
            action(order.symbol, notional=size)
    return result


STRATEGY = replace(
    base.STRATEGY, version="7.0.0", prepare=prepare, orders=trade,
    sources=(Path(shock_breakout.__file__), Path(shock_breakout_distributed.__file__), Path(base.__file__)),
    parameters=base.STRATEGY.parameters | {
        "context_min": Parameter("number", -.03, -.50, .30),
        "breadth_min": Parameter("number", .35, 0., 1.),
        "max_shock": Parameter("number", .15, .025, 1.),
        "vol_target": Parameter("number", 0., 0., .20),
    },
)
