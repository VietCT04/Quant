"""Observe shocks every half hour with the original booked-position policy."""

from dataclasses import replace
from pathlib import Path

import quant as q

from . import shock_breakout as base


HALF_HOUR_TICKS = base.HOURLY_TICKS // 2


def prepare(p):
    plan = base.prepare(p)
    e = q.features.expression
    impulse = e("quant.ts.pct_change", "close", periods=1, units="fraction")
    trend = e("quant.ts.pct_change", "close", periods=48, units="fraction")
    deviation = e("quant.ts.rolling_std", impulse, window=96, units="fraction")
    features = {
        "impulse": impulse,
        "prior_trend": e("quant.ts.lag", trend, periods=1, units="fraction"),
        "prior_vol": e("quant.ts.lag", deviation, periods=1, units="fraction"),
    }
    return replace(plan, decision_ticks=HALF_HOUR_TICKS, features=features,
                   feature_periods=dict.fromkeys(features, HALF_HOUR_TICKS))


STRATEGY = replace(base.STRATEGY, version="3.0.0", prepare=prepare, sources=(Path(base.__file__),))
