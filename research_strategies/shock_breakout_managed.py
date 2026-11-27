"""Apply causal risk budgets to price-shock exposure."""

from dataclasses import replace
from pathlib import Path

import numpy as np

import quant as q
from quant.strategy import Parameter, Plot
from quant.trading import Orders

from . import shock_breakout as base


def prepare(p):
    plan = base.prepare(p)
    if p["volume_budget"] == 0:
        return plan
    turnover = q.features.expression("quant.ts.rolling_mean", "quote_volume", window=3, units="USDT")
    return replace(plan, features=plan.features | {"prior_turnover": turnover},
                   feature_periods=plan.feature_periods | {"prior_turnover": base.HOURLY_TICKS},
                   plots=(*plan.plots, Plot("prior_turnover", "Mean completed hourly turnover", "USDT")))


def initialize(p, symbols, rng):
    return base.initialize(dict(p), symbols, rng), {"peak": 0., "fraction": p["fraction"]}


def trade(state, row, account, orders):
    inner, risk = state
    p = inner[0]
    risk["peak"] = max(risk["peak"], account.equity)
    drawdown = max(0., 1 - account.equity / risk["peak"]) if risk["peak"] > 0 else 0.
    p["fraction"] = risk["fraction"] * max(.25, 1 - p["drawdown_sensitivity"] * drawdown)
    planned = Orders(row.symbols)
    result = base.trade(inner, row, account, planned)
    prior_vol = dict(zip(row.symbols, row.values["prior_vol"][0], strict=True))
    turnover = (dict(zip(row.symbols, row.values["prior_turnover"][0], strict=True))
                if p["volume_budget"] else {})
    for intent in planned.items:
        if intent.kind:
            orders.items.append(intent)
            continue
        amount = abs(intent.size)
        if p["vol_target"]:
            amount *= min(1., p["vol_target"] / max(.002, prior_vol[intent.symbol]))
        if p["volume_budget"]:
            mean_bar = turnover[intent.symbol] / base.HOURLY_TICKS
            amount = min(amount, mean_bar * p["volume_budget"]) if np.isfinite(mean_bar) else 0.
        if amount >= 10.:
            if intent.size > 0:
                orders.buy(intent.symbol, notional=amount)
            else:
                orders.sell(intent.symbol, notional=amount)
    return result


STRATEGY = replace(
    base.STRATEGY, version="2.0.0", prepare=prepare, initialize=initialize, orders=trade,
    fields=("close", "quote_volume"), sources=(Path(base.__file__),),
    parameters=base.STRATEGY.parameters | {
        "drawdown_sensitivity": Parameter("number", 0., 0., 10.),
        "vol_target": Parameter("number", 0., 0., .20),
        "volume_budget": Parameter("number", 0., 0., 1.),
    },
)
