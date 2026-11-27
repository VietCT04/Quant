"""Only implementations imported and listed here can be submitted to the service."""

from . import ema, flow, momentum, phase5, scaling, shock, shock_inventory, shock_inventory_patient, slow_trend
from . import shock_liquidity, shock_liquidity_allocated, shock_liquidity_context, shock_liquidity_extension
from . import shock_breakout, shock_lead_lag
from . import shock_breakout_confirmed, shock_breakout_distributed, shock_breakout_fast, shock_breakout_managed
from . import shock_breakout_contextual, shock_breakout_hedged, shock_breakout_hedged_clock, shock_breakout_rolling
from . import shock_breakout_hedge_control
from . import adaptive_peer, flow_absorption, mark_reversion, pair_breakout, pair_dislocation, peer_rotation, range_reclaim, reference_roll
from . import breadth_rebound, compression_breakout, compression_capacity, compression_context, failed_relief
from . import defensive_rotation, flow_size_divergence
from . import shock_stress, shock_stress_btc
from . import utc_turnover_flow, utc_turnover_handoff, utc_turnover_risk, utc_turnover_seasonal, utc_turnover_weekend
from . import utc_turnover_trail
from . import utc_turnover_conviction
from . import utc_turnover_horizon
from . import utc_turnover_transition
from . import signed_variance

STRATEGIES = (ema.STRATEGY, shock.STRATEGY, flow.STRATEGY, momentum.STRATEGY, scaling.STRATEGY,
              phase5.STRATEGY, slow_trend.STRATEGY, shock_inventory.STRATEGY, shock_inventory_patient.STRATEGY,
              shock_liquidity.STRATEGY, shock_liquidity_context.STRATEGY, shock_liquidity_allocated.STRATEGY,
              shock_liquidity_extension.STRATEGY, shock_breakout.STRATEGY, shock_lead_lag.STRATEGY,
              shock_breakout_managed.STRATEGY, shock_breakout_fast.STRATEGY, shock_breakout_distributed.STRATEGY,
              shock_breakout_confirmed.STRATEGY, shock_breakout_rolling.STRATEGY,
              shock_breakout_contextual.STRATEGY, shock_breakout_hedged.STRATEGY,
              shock_breakout_hedged_clock.STRATEGY, shock_breakout_hedge_control.STRATEGY,
              peer_rotation.STRATEGY, range_reclaim.STRATEGY, reference_roll.STRATEGY,
              adaptive_peer.STRATEGY, mark_reversion.STRATEGY, flow_absorption.STRATEGY, pair_dislocation.STRATEGY,
              pair_breakout.STRATEGY, breadth_rebound.STRATEGY, compression_breakout.STRATEGY,
              compression_context.STRATEGY, compression_capacity.STRATEGY, failed_relief.STRATEGY,
              defensive_rotation.STRATEGY, shock_stress.STRATEGY, flow_size_divergence.STRATEGY,
              utc_turnover_handoff.STRATEGY, shock_stress_btc.STRATEGY, signed_variance.STRATEGY,
              utc_turnover_weekend.STRATEGY, utc_turnover_seasonal.STRATEGY, utc_turnover_risk.STRATEGY,
              utc_turnover_flow.STRATEGY, utc_turnover_trail.STRATEGY, utc_turnover_conviction.STRATEGY,
              utc_turnover_horizon.STRATEGY, utc_turnover_transition.STRATEGY)
