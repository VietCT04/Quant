"""Qualify frequent breakout entries using completed-bar demand."""

from dataclasses import replace
from pathlib import Path

import numpy as np

import quant as q
from quant.strategy import Parameter, Plot
from quant.trading import Orders

from . import shock_breakout, shock_breakout_distributed as base


def prepare(p):
    plan = base.prepare(p)
    expressions = q.features.expression
    features, plots = {}, []
    if p["flow_floor"] > -1:
        features["shock_flow"] = expressions("quant.microstructure.quote_volume_imbalance",
                                              "buy_quote_volume", "sell_quote_volume", units="fraction")
        plots.append(Plot("shock_flow", "Completed shock-bar buying imbalance", "fraction"))
    if p["drift_floor"] > -1:
        drift = expressions("quant.ts.pct_change", "close",
                            periods=360 // p["interval_minutes"], units="fraction")
        features["prior_drift"] = expressions("quant.ts.lag", drift, periods=1, units="fraction")
        plots.append(Plot("prior_drift", "Six-hour pre-shock return", "fraction"))
    return replace(plan, features=plan.features | features, plots=(*plan.plots, *plots),
                   feature_periods=plan.feature_periods | dict.fromkeys(features, plan.decision_ticks))


def trade(state, row, account, orders):
    p = state[0][0]
    if "shock_flow" in row.values or "prior_drift" in row.values:
        valid = np.ones(len(row.symbols), dtype=bool)
        if "shock_flow" in row.values:
            valid &= p["side"] * row.values["shock_flow"][0] >= p["flow_floor"]
        if "prior_drift" in row.values:
            valid &= p["side"] * row.values["prior_drift"][0] >= p["drift_floor"]
        eligible = {symbol for symbol, ok in zip(row.symbols, valid, strict=True) if ok}
        account = replace(account, ready=tuple(symbol for symbol in account.ready if symbol in eligible))

    if not p["entry_stop"]:
        return base.trade(state, row, account, orders)
    planned = Orders(row.symbols)
    observations = base.trade(state, row, account, planned)
    orders.items.extend(planned.items)
    closing = {order.symbol for order in planned.items if order.kind == 2}
    price = row.values["close"][0]
    entry = account.positions["entry"]
    valid = (row.status[0] == 0) & (entry > 0) & (price > 0) & np.isfinite(price)
    with np.errstate(divide="ignore", invalid="ignore"):
        invalidated = p["side"] * (price / entry - 1) <= -p["entry_stop"]
    for i in np.flatnonzero(valid & invalidated & (p["side"] * account.positions["qty"] > 0)):
        if row.symbols[i] not in closing:
            orders.close(row.symbols[i])
    return observations


STRATEGY = replace(
    base.STRATEGY, version="5.0.0", fields=("close", "buy_quote_volume", "sell_quote_volume"),
    prepare=prepare, orders=trade, sources=(Path(shock_breakout.__file__),),
    parameters=base.STRATEGY.parameters | {
        "flow_floor": Parameter("number", -1., -1., .8),
        "drift_floor": Parameter("number", -1., -1., .30),
        "entry_stop": Parameter("number", 0., 0., .15),
    },
)
