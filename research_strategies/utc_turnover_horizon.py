"""Test the time horizon of the preceding move independently of the entry clock."""

from dataclasses import replace
from pathlib import Path

import quant as q
from quant.strategy import Parameter

from . import utc_turnover_handoff, utc_turnover_weekend as base


def prepare(p):
    plan = base.prepare(p)
    e = q.features.expression
    lookback = p["prior_hours"]
    move = e("quant.ts.pct_change", "close", periods=lookback, units="fraction")
    preceding = e("quant.ts.lag", move, periods=1, units="fraction")
    plots = tuple(replace(plot, label=f"Preceding {lookback}-hour return")
                  if plot.name == "prior_move" else plot for plot in plan.plots)
    return replace(plan, features=plan.features | {"prior_move": preceding}, plots=plots)


STRATEGY = replace(base.STRATEGY, version="1.7.0", prepare=prepare,
                   sources=(Path(utc_turnover_handoff.__file__), Path(base.__file__)),
                   parameters=base.STRATEGY.parameters | {
                       "prior_hours": Parameter("integer", 8, choices=(4, 6, 8, 10, 12)),
                   })
