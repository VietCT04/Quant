"""Equity-proportional inventory around causal shock and recovery signals."""

from dataclasses import replace
from pathlib import Path

import numpy as np

from quant.strategy import Parameter, Plot

from . import shock_liquidity


def prepare(p):
    plan = shock_liquidity.prepare(p)
    return replace(plan, plots=(*plan.plots, Plot("leg_budget", "Equity-proportional order budget", "USDT")))


def initialize(parameters, symbols, rng):
    return shock_liquidity.initialize(dict(parameters), symbols, rng)


def trade(state, row, account, orders):
    p = state[0]
    equity = account.equity
    if not np.isfinite(equity) or equity <= 0:
        return {}
    minimum = 10.
    p["order_usdt"] = max(minimum, equity * p["leg_fraction"])
    p["inventory"] = max(p["order_usdt"], equity * p["inventory_fraction"])
    observations = shock_liquidity.trade(state, row, account, orders)
    observations["leg_budget"] = np.full((1, len(row.symbols)), p["order_usdt"])
    return observations


STRATEGY = replace(
    shock_liquidity.STRATEGY, version="3.0.0", prepare=prepare, initialize=initialize, orders=trade,
    sources=(Path(__file__).with_name("shock_liquidity.py"),),
    parameters=({k: v for k, v in shock_liquidity.STRATEGY.parameters.items() if k not in ("order_usdt", "inventory")}
                | {"leg_fraction": Parameter("number", .04, .001, .15),
                   "inventory_fraction": Parameter("number", .12, .01, .30)}),
)
