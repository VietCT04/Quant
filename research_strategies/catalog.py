"""Only implementations imported and listed here can be submitted to the service."""

from . import ema, flow, momentum, phase5, scaling, shock, shock_inventory, shock_inventory_patient, slow_trend

STRATEGIES = (ema.STRATEGY, shock.STRATEGY, flow.STRATEGY, momentum.STRATEGY, scaling.STRATEGY, phase5.STRATEGY, slow_trend.STRATEGY, shock_inventory.STRATEGY, shock_inventory_patient.STRATEGY)
