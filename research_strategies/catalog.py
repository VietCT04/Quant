"""Only implementations imported and listed here can be submitted to the service."""

from . import ema, flow, momentum, phase5, scaling, shock, slow_trend

STRATEGIES = (ema.STRATEGY, shock.STRATEGY, flow.STRATEGY, momentum.STRATEGY, scaling.STRATEGY, phase5.STRATEGY, slow_trend.STRATEGY)
