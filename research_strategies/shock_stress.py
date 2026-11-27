"""Risk-size an established long shock signal with an observable BTC state."""

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
    move = e("quant.ts.pct_change", "close", periods=10080 // p["interval_minutes"], units="fraction")
    lag = e("quant.ts.lag", move, periods=60 // p["interval_minutes"], units="fraction")
    return replace(plan, features=plan.features | {"prior_week": lag},
                   feature_periods=plan.feature_periods | {"prior_week": plan.decision_ticks},
                   warmup_bars=170 * shock_breakout.HOURLY_TICKS,
                   plots=(*plan.plots, Plot("entry_risk", "Entry size multiplier", "ratio")))


def initialize(p, symbols, rng):
    btc = symbols.index("BTCUSDT") if "BTCUSDT" in symbols else None
    return base.STRATEGY.initialize(p, symbols, rng), btc


def trade(state, row, account, orders):
    original, btc = state
    p = original[0][0]
    change = row.values["prior_week"][0, btc] if btc is not None else np.nan
    if np.isfinite(change):
        weight = p["ordinary_size"] + (1. - p["ordinary_size"]) * np.clip(-change / p["stress"], 0., 1.)
        ready = tuple(symbol for symbol in account.ready if symbol != row.symbols[btc])
    else:
        weight, ready = 0., ()
    planned = Orders(row.symbols)
    result = base.STRATEGY.orders(original, row, replace(account, ready=ready), planned)
    for order in planned.items:
        if order.kind == 0:
            size = abs(order.size) * weight
            if size >= 10.:
                orders.buy(order.symbol, notional=size)
        else:
            orders.items.append(order)
    return result | {"entry_risk": np.full((1, len(row.symbols)), weight)}


STRATEGY = replace(
    base.STRATEGY, version="8.3.0", prepare=prepare, initialize=initialize, orders=trade,
    sources=(Path(shock_breakout.__file__), Path(shock_breakout_distributed.__file__), Path(base.__file__)),
    parameters=base.STRATEGY.parameters | {
        "side": Parameter("integer", 1, choices=(1,)),
        "ordinary_size": Parameter("number", .30, .10, .95),
        "stress": Parameter("number", .055, .025, .15),
    },
)
