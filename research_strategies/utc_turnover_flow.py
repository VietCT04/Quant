"""Scale fixed-clock continuation from observed aggressive quote flow."""

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
    buy = e("quant.ts.rolling_sum", "buy_quote_volume", window=2, units="USDT")
    sell = e("quant.ts.rolling_sum", "sell_quote_volume", window=2, units="USDT")
    flow = e("quant.microstructure.quote_volume_imbalance", buy, sell, units="fraction")
    return replace(plan, features=plan.features | {"taker_pressure": flow},
                   feature_periods=plan.feature_periods | {"taker_pressure": plan.decision_ticks},
                   plots=(*plan.plots, Plot("taker_pressure", "Completed two-hour quote imbalance", "fraction"),
                          Plot("pressure_weight", "Quote-pressure size multiplier", "ratio")))


def trade(state, row, account, orders):
    p = state[0]
    pressure = row.values["taker_pressure"][0]
    valid = (row.status[0] == 0) & np.isfinite(pressure)
    reference = np.median(pressure[valid]) if p["relative_pressure"] and np.count_nonzero(valid) >= 20 else 0.
    planned = Orders(row.symbols)
    observed = set(account.ready)
    ready = tuple(s for s, ok in zip(row.symbols, valid, strict=True) if ok and s in observed)
    result = base.trade(state, row, replace(account, ready=ready), planned)
    symbols = {symbol: i for i, symbol in enumerate(row.symbols)}
    weight = np.full(len(symbols), np.nan)
    for order in planned.items:
        if order.kind != 0:
            orders.items.append(order)
            continue
        i = symbols[order.symbol]
        signed = np.sign(order.size) * (pressure[i] - reference)
        weight[i] = p["weak_weight"] if signed < p["pressure_min"] else 1.
        notional = max(10., abs(order.size) * weight[i])
        action = orders.buy if order.size > 0 else orders.sell
        action(order.symbol, notional=notional)
    return result | {"pressure_weight": weight[None, :]}


STRATEGY = replace(base.STRATEGY, version="1.4.0", prepare=prepare, orders=trade,
                   fields=(*base.STRATEGY.fields, "buy_quote_volume", "sell_quote_volume"),
                   sources=(Path(utc_turnover_handoff.__file__), Path(base.__file__)),
                   parameters=base.STRATEGY.parameters | {
                       "weak_weight": Parameter("number", .35, .10, .95),
                       "pressure_min": Parameter("number", .02, -.10, .15),
                       "relative_pressure": Parameter("boolean", False),
                   })
