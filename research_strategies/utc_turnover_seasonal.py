"""Compare turnover with the same UTC clock and day of week."""

from dataclasses import replace
from pathlib import Path

import quant as q
from quant.strategy import Parameter

from . import utc_turnover_handoff, utc_turnover_weekend as base


def prepare(p):
    plan = base.prepare(p)
    e = q.features.expression
    week = e("quant.ts.lag", "quote_volume", periods=168, units="USDT")
    if p["anchor"] == "conservative":
        week = e("quant.math.maximum", plan.features["same_clock_quote"], week, units="USDT")
    anchor = "larger of yesterday and last week" if p["anchor"] == "conservative" else "last week"
    plots = tuple(replace(plot, label=f"Observed turnover versus {anchor} at the same UTC hour")
                  if plot.name == "turnover_ratio" else plot for plot in plan.plots)
    return replace(plan, features=plan.features | {"same_clock_quote": week},
                   warmup_bars=170 * utc_turnover_handoff.TICKS, plots=plots)


STRATEGY = replace(base.STRATEGY, version="1.2.0", prepare=prepare,
                   sources=(Path(utc_turnover_handoff.__file__), Path(base.__file__)),
                   parameters=base.STRATEGY.parameters | {
                       "anchor": Parameter("string", "same_weekday", choices=("same_weekday", "conservative")),
                   })
