"""Use BTC-only history to size altcoin shock entries without extra altcoin warm-up."""

from collections import deque
from dataclasses import replace
from pathlib import Path

import numpy as np

import quant as q
from quant.strategy import Plot
from quant.trading import Orders

from . import shock_breakout, shock_breakout_distributed, shock_breakout_rolling as base, shock_stress


def prepare(p):
    plan = base.prepare(p)
    anchor = q.features.expression("quant.ts.rolling_mean", "close", window=1, units="USDT/base")
    return replace(plan, features=plan.features | {"stress_close": anchor},
                   feature_periods=plan.feature_periods | {"stress_close": plan.decision_ticks},
                   plots=(*plan.plots, Plot("entry_risk", "Entry size multiplier", "ratio")))


def initialize(p, symbols, rng):
    original, btc = shock_stress.initialize(p, symbols, rng)
    span = (10080 + 60) // p["interval_minutes"] + 1
    return original, btc, deque(maxlen=span)


def stress_return(history, p, clock, close):
    continuous = history and clock - history[-1][0] == p["interval_minutes"] * 60_000
    valid_run = (history[-1][2] + 1 if continuous else 1) if np.isfinite(close) and close > 0 else 0
    history.append((clock, close, valid_run))
    if len(history) < history.maxlen:
        return np.nan
    shift = 60 // p["interval_minutes"]
    oldest, latest = history[0], history[-1 - shift]
    if (latest[0] - oldest[0] != 7 * 24 * shock_breakout.HOUR
            or clock - latest[0] != shock_breakout.HOUR):
        return np.nan
    if latest[2] < 10080 // p["interval_minutes"] + 1:
        return np.nan
    return latest[1] / oldest[1] - 1.


def trade(state, row, account, orders):
    original, btc, history = state
    p = original[0][0]
    weight = p["ordinary_size"]
    ready = ()
    if btc is not None:
        move = stress_return(history, p, account.clock_ms, row.values["stress_close"][0, btc])
        if np.isfinite(move):
            weight += (1. - weight) * np.clip(-move / p["stress"], 0., 1.)
        ready = tuple(symbol for symbol in account.ready if symbol != row.symbols[btc])
    planned = Orders(row.symbols)
    observations = base.STRATEGY.orders(original, row, replace(account, ready=ready), planned)
    for order in planned.items:
        if order.kind == 0:
            size = abs(order.size) * weight
            if size >= 10.:
                orders.buy(order.symbol, notional=size)
        else:
            orders.items.append(order)
    return observations | {"entry_risk": np.full((1, len(row.symbols)), weight)}


STRATEGY = replace(
    shock_stress.STRATEGY, version="8.4.0", prepare=prepare, initialize=initialize, orders=trade,
    sources=(Path(shock_breakout.__file__), Path(shock_breakout_distributed.__file__),
             Path(base.__file__), Path(shock_stress.__file__)),
)
