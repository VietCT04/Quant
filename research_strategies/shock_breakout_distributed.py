"""Trade smaller, qualified shocks with a daily entry budget."""

from dataclasses import replace
from pathlib import Path

import quant as q
from marketdata.model import DAY
from quant.strategy import Parameter
from quant.trading import Orders

from . import shock_breakout as base


def prepare(p):
    plan = base.prepare(p)
    e = q.features.expression
    minutes = p["interval_minutes"]
    ticks = minutes * 12
    bars_per_day = 1440 // minutes
    impulse = e("quant.ts.pct_change", "close", periods=1, units="fraction")
    trend = e("quant.ts.pct_change", "close", periods=bars_per_day, units="fraction")
    deviation = e("quant.ts.rolling_std", impulse, window=2 * bars_per_day, units="fraction")
    features = {
        "impulse": impulse,
        "prior_trend": e("quant.ts.lag", trend, periods=1, units="fraction"),
        "prior_vol": e("quant.ts.lag", deviation, periods=1, units="fraction"),
    }
    return replace(plan, decision_ticks=ticks, features=features, feature_periods=dict.fromkeys(features, ticks))


def initialize(p, symbols, rng):
    return base.initialize(p, symbols, rng), {"day": None, "submitted": 0}


def trade(state, row, account, orders):
    original, quota = state
    day = account.clock_ms // DAY
    if day != quota["day"]:
        quota.update(day=day, submitted=0)
    candidate_orders = Orders(row.symbols)
    trace = base.trade(original, row, account, candidate_orders)
    for order in candidate_orders.items:
        if order.kind == 0:
            if quota["submitted"] >= original[0]["daily_entries"]:
                continue
            quota["submitted"] += 1
        orders.items.append(order)
    return trace


STRATEGY = replace(
    base.STRATEGY, version="4.0.0", prepare=prepare, initialize=initialize, orders=trade,
    sources=(Path(base.__file__),), parameters=base.STRATEGY.parameters | {
        "interval_minutes": Parameter("integer", 30, choices=(15, 30, 60)),
        "daily_entries": Parameter("integer", 6, 1, 24),
    },
)
