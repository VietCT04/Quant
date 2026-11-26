"""Only implementations imported and listed here can be submitted to the service."""

from . import ema, flow, momentum, phase5, scaling, shock, shock_inventory, shock_inventory_patient, slow_trend
from . import shock_liquidity, shock_liquidity_allocated, shock_liquidity_context, shock_liquidity_extension
from . import shock_breakout, shock_lead_lag

STRATEGIES = (ema.STRATEGY, shock.STRATEGY, flow.STRATEGY, momentum.STRATEGY, scaling.STRATEGY,
              phase5.STRATEGY, slow_trend.STRATEGY, shock_inventory.STRATEGY, shock_inventory_patient.STRATEGY,
              shock_liquidity.STRATEGY, shock_liquidity_context.STRATEGY, shock_liquidity_allocated.STRATEGY,
              shock_liquidity_extension.STRATEGY, shock_breakout.STRATEGY, shock_lead_lag.STRATEGY)
