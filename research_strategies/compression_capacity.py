"""Size quiet range breakouts using only liquidity observed before the order."""

from dataclasses import replace
from pathlib import Path

import numpy as np

from quant.strategy import Parameter, Plot
from quant.trading import Orders

from . import compression_breakout as base


def prepare(p):
    plan = base.prepare(p)
    return replace(plan, plots=(*plan.plots, Plot("capacity_cap", "Entry notional cap", "USDT")))


def trade(state, row, account, orders):
    p = state[0]
    quote = row.values["quote_volume"][0]
    long_vol = row.values["long_vol"][0]
    cap = np.where(np.isfinite(quote), np.maximum(0, quote) * p["participation_cap"], 0.)
    ready = set(account.ready)
    qualified = tuple(symbol for symbol, size in zip(row.symbols, cap, strict=True)
                      if symbol in ready and size >= 10.)
    planned = Orders(row.symbols)
    result = base.trade(state, row, replace(account, ready=qualified), planned)
    indices = {s: i for i, s in enumerate(row.symbols)}
    for order in planned.items:
        if order.kind != 0:
            orders.items.append(order)
            continue
        i = indices[order.symbol]
        # Retain the original sign and size at most to the known completed bar's flow.
        ratio = min(1., p["vol_target"] / max(.002, long_vol[i]))
        size = min(cap[i], max(10., abs(order.size) * ratio))
        action = orders.buy if order.size > 0 else orders.sell
        action(order.symbol, notional=size)
    return result | {"capacity_cap": cap[None, :]}


STRATEGY = replace(base.STRATEGY, version="1.2.0", prepare=prepare, orders=trade,
                   sources=(Path(base.__file__),),
                   parameters=base.STRATEGY.parameters | {
                       "participation_cap": Parameter("number", .15, .02, .70),
                       "vol_target": Parameter("number", .02, .003, .10),
                   })
