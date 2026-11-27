from types import SimpleNamespace

import numpy as np

from marketdata.model import DAY
from quant.strategy import Portfolio, prepare
from quant.trading import Orders
from research_strategies import utc_turnover_conviction_transition as strategy


def test_transition_reduction_composes_with_move_size_before_fill():
    _, _, p, _ = prepare({"id": "fixed_clock_turnover_handoff", "version": "1.9.0"},
                        {"drift": .014, "clock_window": "14"}, rebalance_ticks=1)
    symbols = tuple(f"S{i}" for i in range(24))
    state = strategy.STRATEGY.initialize(p, symbols, None)
    row = SimpleNamespace(symbols=symbols, status=np.zeros((1, 24), dtype="u1"), values={
        "close": np.full((1, 24), 100.), "prior_move": np.zeros((1, 24)),
        "turn": np.full((1, 24), .007), "quote_hour": np.full((1, 24), 3e6),
        "same_clock_quote": np.full((1, 24), 1e6),
    })
    row.values["prior_move"][0, 0] = .0205
    for day, expected in ((1, 24.375), (2, 487.5), (5, 97.5)):
        stamp = day * DAY + 14 * 3_600_000
        row.timestamps_ms = np.array([stamp])
        account = Portfolio(stamp + 5000, {"qty": np.zeros(24), "entry": np.zeros(24)}, 10_000.,
                            available_margin=10_000., ready=symbols)
        orders = Orders(symbols)
        strategy.trade(state, row, account, orders)
        np.testing.assert_allclose([o.size for o in orders.items], [expected])
