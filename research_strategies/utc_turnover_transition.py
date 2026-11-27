"""Test lower risk before and after the weekend in the clocked strategy."""

from dataclasses import replace
from pathlib import Path

from marketdata.model import DAY
from quant.strategy import Parameter

from . import utc_turnover_handoff, utc_turnover_weekend as base


def trade(state, row, account, orders):
    p, attempted = state
    day = (int(row.timestamps_ms[0]) // DAY + 3) % 7
    if day == 4 or (day == 0 and p["monday"]):
        p = p | {"weekday_weight": p["transition_weight"]}
    return base.trade((p, attempted), row, account, orders)


STRATEGY = replace(base.STRATEGY, version="1.8.0", orders=trade,
                   sources=(Path(utc_turnover_handoff.__file__), Path(base.__file__)),
                   parameters=base.STRATEGY.parameters | {
                       "transition_weight": Parameter("number", .05, .025, .35),
                       "monday": Parameter("boolean", True),
                   })
