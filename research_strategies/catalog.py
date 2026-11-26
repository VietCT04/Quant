"""Only implementations imported and listed here can be submitted to the service."""

from . import ema, flow, momentum, scaling, shock

STRATEGIES = (ema.STRATEGY, shock.STRATEGY, flow.STRATEGY, momentum.STRATEGY, scaling.STRATEGY)
