from types import SimpleNamespace

import numpy as np

from quant.strategy import Portfolio, prepare
from quant.trading import Orders
from research_strategies import shock_stress as strategy


def test_shock_stress_sizes_from_prior_btc_state_and_excludes_btc():
    p0 = {"depth": .0155, "alignment": .035, "fraction": .06, "ordinary_size": .30}
    _, plan, p, _ = prepare({"id": "shock_repricing_breakout", "version": "8.3.0"}, p0, rebalance_ticks=1)
    assert plan.features["prior_week"].name == "quant.ts.lag"
    symbols = ("BTCUSDT", *(f"S{i}" for i in range(21)))
    n = len(symbols)
    row = SimpleNamespace(symbols=symbols, status=np.zeros((1, n), dtype="u1"), values={
        "close": np.full((1, n), 100.), "impulse": np.full((1, n), .002),
        "prior_trend": np.full((1, n), .045), "prior_vol": np.full((1, n), .006),
        "prior_week": np.zeros((1, n)),
    })
    row.values["impulse"][0, :2] = .035
    account = Portfolio(0, {"qty": np.zeros(n)}, 10_000., available_margin=10_000., ready=symbols)
    for drop, expected in ((0., 180.), (-.065, 600.)):
        row.values["prior_week"][0, 0] = drop
        orders = Orders(symbols)
        strategy.trade(strategy.initialize(p, symbols, None), row, account, orders)
        order, = orders.items
        assert order.symbol == "S0"
        np.testing.assert_allclose(order.size, expected)
