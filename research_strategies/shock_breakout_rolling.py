"""Review a 60-minute shock every 30 minutes without using a partial bar."""

from dataclasses import replace
from pathlib import Path

import quant as q

from . import shock_breakout, shock_breakout_distributed as base


def prepare(p):
    plan = base.prepare(p)
    e = q.features.expression
    span = 60 // p["interval_minutes"]
    bars_per_day = 1440 // p["interval_minutes"]
    impulse = e("quant.ts.pct_change", "close", periods=span, units="fraction")
    trend = e("quant.ts.pct_change", "close", periods=bars_per_day, units="fraction")
    deviation = e("quant.ts.rolling_std", impulse, window=2 * bars_per_day, units="fraction")
    features = {
        "impulse": impulse,
        "prior_trend": e("quant.ts.lag", trend, periods=span, units="fraction"),
        "prior_vol": e("quant.ts.lag", deviation, periods=span, units="fraction"),
    }
    return replace(plan, features=features, feature_periods=dict.fromkeys(features, plan.decision_ticks),
                   warmup_bars=52 * shock_breakout.HOURLY_TICKS)


STRATEGY = replace(base.STRATEGY, version="6.0.0", prepare=prepare,
                   sources=(Path(shock_breakout.__file__),))
