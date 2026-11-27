from types import SimpleNamespace

import numpy as np

from marketdata.model import DAY
from quant.strategy import Portfolio, prepare
from quant.trading import Orders
from research_strategies import utc_turnover_flow as strategy


def test_quote_pressure_scales_signed_orders_in_their_own_direction():
    _, plan, p, _ = prepare({"id": "fixed_clock_turnover_handoff", "version": "1.4.0"},
                             {"direction": 0}, rebalance_ticks=1)
    assert plan.features["taker_pressure"].name == "quant.microstructure.quote_volume_imbalance"
    symbols = tuple(f"S{i}" for i in range(24))
    state = strategy.STRATEGY.initialize(p, symbols, None)
    t = 2 * DAY + 14 * 3_600_000  # Saturday, 14:00 UTC
    row = SimpleNamespace(symbols=symbols, status=np.zeros((1, 24), dtype="u1"),
                          timestamps_ms=np.array([t]), values={
        "close": np.full((1, 24), 100.), "prior_move": np.zeros((1, 24)),
        "turn": np.zeros((1, 24)), "quote_hour": np.full((1, 24), 3e6),
        "same_clock_quote": np.full((1, 24), 1e6), "taker_pressure": np.zeros((1, 24)),
    })
    row.values["prior_move"][0, :2] = [.027, -.026]
    row.values["turn"][0, :2] = [.01, -.01]
    row.values["taker_pressure"][0, :2] = [-.08, -.08]
    account = Portfolio(t + 5000, {"qty": np.zeros(24), "entry": np.zeros(24)}, 10_000.,
                        available_margin=10_000., ready=symbols)
    orders = Orders(symbols)
    strategy.trade(state, row, account, orders)
    assert [order.symbol for order in orders.items] == ["S0", "S1"]
    np.testing.assert_allclose([o.size for o in orders.items], [227.5, -650.])
