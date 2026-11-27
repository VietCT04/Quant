from types import SimpleNamespace

import numpy as np

from quant.strategy import prepare
from quant.trading import Orders
from research_strategies import utc_turnover_market_context as strategy


def test_market_context_sizes_against_market_entries_and_preserves_exits(monkeypatch):
    _, _, p, _ = prepare({"id": "fixed_clock_turnover_handoff", "version": "2.2.0"}, {}, rebalance_ticks=1)
    symbols = ("LONG", "SHORT", "EXIT", "VALID", "MISSING")
    row = SimpleNamespace(
        symbols=symbols, status=np.array([[0, 0, 0, 0, 1]]),
        values={"close": np.full((1, 5), 100.), "prior_move": np.array([[.03, -.04, .005, .005, -.5]]),
                "turn": np.zeros((1, 5))},
    )

    def base_trade(state, row, account, orders):
        orders.buy("LONG", notional=100.)
        orders.sell("SHORT", notional=100.)
        orders.close("EXIT")
        return {}

    monkeypatch.setattr(strategy.base, "trade", base_trade)
    orders = Orders(symbols)
    result = strategy.trade((p, None), row, None, orders)
    assert [(order.symbol, order.kind, order.size) for order in orders.items] == [
        ("LONG", 0, 100.), ("SHORT", 0, -40.), ("EXIT", 2, 0.),
    ]
    np.testing.assert_allclose(result["market_context"], [[1., .4, np.nan, np.nan, np.nan]])
