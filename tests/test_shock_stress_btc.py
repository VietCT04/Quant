from collections import deque

import numpy as np

from quant.strategy import prepare
from research_strategies import shock_stress_btc as strategy


def test_btc_only_history_uses_prior_week_and_preserves_altcoin_warmup():
    _, plan, p, _ = prepare({"id": "shock_repricing_breakout", "version": "8.4.0"},
                              {"interval_minutes": 60}, rebalance_ticks=1)
    assert plan.warmup_bars == 52 * 720
    assert "prior_week" not in plan.features
    history = deque(maxlen=170)
    for i in range(169):
        result = strategy.stress_return(history, p, i * 3_600_000, 100. if i < 168 else 85.)
        assert np.isnan(result)
    assert np.isclose(strategy.stress_return(history, p, 169 * 3_600_000, np.nan), -.15)
    assert np.isnan(strategy.stress_return(history, p, 170 * 3_600_000, 90.))
