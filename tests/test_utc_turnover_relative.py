from types import SimpleNamespace

import numpy as np

from marketdata.model import DAY
from quant.strategy import Portfolio, prepare
from quant.trading import Orders
from research_strategies import utc_turnover_relative as strategy


def test_relative_sizing_uses_current_peer_median_not_absolute_move():
    _, _, p, _ = prepare({"id": "fixed_clock_turnover_handoff", "version": "2.0.0"},
                        {"clock_window": "14"}, rebalance_ticks=1)
    symbols = tuple(f"S{i}" for i in range(24))
    state = strategy.STRATEGY.initialize(p, symbols, None)
    t = 2 * DAY + 14 * 3_600_000
    row = SimpleNamespace(symbols=symbols, status=np.zeros((1, 24), dtype="u1"),
                          timestamps_ms=np.array([t]), values={
        "close": np.full((1, 24), 100.), "prior_move": np.full((1, 24), .02),
        "turn": np.full((1, 24), .007), "quote_hour": np.full((1, 24), 3e6),
        "same_clock_quote": np.full((1, 24), 1e6),
    })
    row.values["prior_move"][0, 0] = .04
    account = Portfolio(t + 5000, {"qty": np.zeros(24), "entry": np.zeros(24)}, 10_000.,
                        available_margin=10_000., ready=symbols)
    orders = Orders(symbols)
    values = strategy.trade(state, row, account, orders)
    order, = orders.items
    expected = .35 + .65 * (.02 - .008) / .019
    assert order.symbol == "S0"
    np.testing.assert_allclose([order.size, values["relative_risk"][0, 0]], [650. * expected, expected])
