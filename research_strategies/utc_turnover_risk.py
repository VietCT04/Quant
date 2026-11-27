"""Keep fixed-clock entry decisions and cap size by observed local volatility."""

from dataclasses import replace
from pathlib import Path

import numpy as np

import quant as q
from quant.strategy import Parameter, Plot
from quant.trading import Orders

from . import utc_turnover_handoff, utc_turnover_weekend as base


def prepare(p):
    plan = base.prepare(p)
    e = q.features.expression
    volatility = e("quant.ts.rolling_std", plan.features["turn"], window=24, units="fraction")
    prior = e("quant.ts.lag", volatility, periods=1, units="fraction")
    return replace(plan, features=plan.features | {"prior_vol": prior},
                   feature_periods=plan.feature_periods | {"prior_vol": plan.decision_ticks},
                   warmup_bars=27 * utc_turnover_handoff.TICKS,
                   plots=(*plan.plots, Plot("vol_risk", "Prior-volatility size multiplier", "ratio")))


def trade(state, row, account, orders):
    p = state[0]
    estimate = row.values["prior_vol"][0]
    observed = np.isfinite(estimate)
    weight = np.minimum(1., np.divide(p["vol_target"], np.maximum(estimate, .001),
                                      out=np.zeros_like(estimate), where=observed))
    ready = set(account.ready)
    qualified = tuple(name for name, valid in zip(row.symbols, observed, strict=True)
                      if valid and name in ready)
    projected = Orders(row.symbols)
    observations = base.trade(state, row, replace(account, ready=qualified), projected)
    index = {symbol: i for i, symbol in enumerate(row.symbols)}
    for order in projected.items:
        if order.kind != 0:
            orders.items.append(order)
            continue
        risk_size = max(10., abs(order.size) * weight[index[order.symbol]])
        action = orders.buy if order.size > 0 else orders.sell
        action(order.symbol, notional=risk_size)
    return observations | {"vol_risk": weight[None, :]}


STRATEGY = replace(base.STRATEGY, version="1.3.0", prepare=prepare, orders=trade,
                   sources=(Path(utc_turnover_handoff.__file__), Path(base.__file__)),
                   parameters=base.STRATEGY.parameters | {
                       "vol_target": Parameter("number", .012, .002, .05),
                   })
