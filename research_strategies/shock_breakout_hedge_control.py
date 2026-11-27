"""Allow a zero hedge for matched tests of the same directional universe."""

from dataclasses import replace
from pathlib import Path

from quant.strategy import Parameter

from . import shock_breakout_hedged_clock as base


STRATEGY = replace(
    base.STRATEGY, version="8.2.0", sources=(*base.STRATEGY.sources, Path(__file__)),
    parameters=base.STRATEGY.parameters | {"hedge_fraction": Parameter("number", .35, 0., .9)},
)
