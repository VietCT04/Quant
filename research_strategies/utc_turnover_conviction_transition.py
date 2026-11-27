"""Apply a weekend-transition cap to confirmed move-based sizing."""

from dataclasses import replace
from pathlib import Path

from marketdata.model import DAY

from . import utc_turnover_conviction as base
from . import utc_turnover_handoff, utc_turnover_transition, utc_turnover_weekend


def trade(state, row, account, orders):
    p, attempted = state
    day = (int(row.timestamps_ms[0]) // DAY + 3) % 7
    if day == 4 or (day == 0 and p["monday"]):
        p = p | {"weekday_weight": p["transition_weight"]}
    return base.trade((p, attempted), row, account, orders)


STRATEGY = replace(base.STRATEGY, version="1.9.0", orders=trade,
                   sources=(Path(utc_turnover_handoff.__file__), Path(utc_turnover_weekend.__file__),
                            Path(base.__file__), Path(utc_turnover_transition.__file__)),
                   parameters=base.STRATEGY.parameters | {
                       k: utc_turnover_transition.STRATEGY.parameters[k] for k in ("transition_weight", "monday")
                   })
