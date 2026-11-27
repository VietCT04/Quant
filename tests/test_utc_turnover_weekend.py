from types import SimpleNamespace

import numpy as np

from marketdata.model import DAY
from quant.strategy import Portfolio, prepare
from quant.trading import Orders
from research_strategies import utc_turnover_weekend as strategy


def test_weekend_window_uses_utc_day_and_limits_entry_attempts():
    _, _, p, _ = prepare({"id": "fixed_clock_turnover_handoff", "version": "1.1.0"}, {}, rebalance_ticks=1)
    symbols = tuple(f"S{i}" for i in range(24))
    state = strategy.STRATEGY.initialize(p, symbols, None)
    row = SimpleNamespace(symbols=symbols, status=np.zeros((1, 24), dtype="u1"), values={
        "close": np.full((1, 24), 100.), "prior_move": np.zeros((1, 24)), "turn": np.full((1, 24), .007),
        "quote_hour": np.full((1, 24), 3e6), "same_clock_quote": np.full((1, 24), 1e6),
    })
    row.values["prior_move"][0, 0] = .027
    position = {"qty": np.zeros(24), "entry": np.zeros(24)}

    def decide(day, hour):
        t = day * DAY + hour * 3_600_000
        row.timestamps_ms = np.array([t])
        account = Portfolio(t + 5000, position, 10_000., available_margin=10_000., ready=symbols)
        orders = Orders(symbols)
        strategy.trade(state, row, account, orders)
        return [order.size for order in orders.items]

    assert not decide(1, 13)  # Friday
    np.testing.assert_allclose(decide(1, 14), [130.])
    np.testing.assert_allclose(decide(2, 13), [650.])  # Saturday
    assert not decide(2, 14)
    np.testing.assert_allclose(decide(3, 15), [650.])  # Sunday
