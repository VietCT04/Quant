"""Require independent market participation before following a quiet range escape."""

from dataclasses import replace
from pathlib import Path

import numpy as np

import quant as q
from quant.strategy import Parameter, Plot

from . import compression_breakout as base


def prepare(p):
    plan = base.prepare(p)
    e = q.features.expression
    day = e("quant.ts.pct_change", "close", periods=24, units="fraction")
    features = {"prior_peer_move": e("quant.ts.lag", day, periods=1, units="fraction")}
    return replace(plan, features=plan.features | features,
                   feature_periods=plan.feature_periods | dict.fromkeys(features, base.TICKS),
                   plots=(*plan.plots, Plot("prior_peer_move", "Prior 48-hour return", "fraction"),
                          Plot("prior_participation", "Breadth before the range escape", "fraction")))


def trade(state, row, account, orders):
    p = state[0]
    move = row.values["prior_peer_move"][0]
    valid = (row.status[0] <= 1) & np.isfinite(move)
    breadth = np.nan
    ready = ()
    if np.count_nonzero(valid) >= 20:
        median = np.median(move[valid])
        breadth = np.mean(move[valid] > 0)
        if median >= p["market_floor"] and breadth >= p["participation"]:
            allowed = valid & (move >= median + p["relative_floor"])
            ready = tuple(symbol for symbol, accepted in zip(row.symbols, allowed, strict=True)
                          if accepted and symbol in account.ready)
    observations = base.trade(state, row, replace(account, ready=ready), orders)
    return observations | {"prior_participation": np.full((1, len(move)), breadth)}


STRATEGY = replace(base.STRATEGY, version="1.1.0", prepare=prepare, orders=trade,
                   sources=(Path(base.__file__),),
                   parameters=base.STRATEGY.parameters | {
                       "market_floor": Parameter("number", -.035, -.15, .04),
                       "participation": Parameter("number", .44, .25, .80),
                       "relative_floor": Parameter("number", -.025, -.10, .05),
                   })
