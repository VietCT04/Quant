from types import SimpleNamespace

import numpy as np

from marketdata.model import DAY
from quant.strategy import Portfolio, prepare
from quant.trading import Orders
from research_strategies import utc_turnover_conviction as strategy


def test_clocked_conviction_retains_notional_sign_and_floors_risk():
    _, _, p, _ = prepare({"id": "fixed_clock_turnover_handoff", "version": "1.6.0"},
                        {"direction": 0, "drift": .014}, rebalance_ticks=1)
    symbols = tuple(f"S{i}" for i in range(24))
    state = strategy.STRATEGY.initialize(p, symbols, None)
    t = 2 * DAY + 14 * 3_600_000
    row = SimpleNamespace(symbols=symbols, status=np.zeros((1, 24), dtype="u1"),
                          timestamps_ms=np.array([t]), values={
        "close": np.full((1, 24), 100.), "prior_move": np.zeros((1, 24)),
        "turn": np.zeros((1, 24)), "quote_hour": np.full((1, 24), 3e6),
        "same_clock_quote": np.full((1, 24), 1e6),
    })
    row.values["prior_move"][0, :2] = [.0205, -.04]
    row.values["turn"][0, :2] = [.01, -.01]
    account = Portfolio(t + 5000, {"qty": np.zeros(24), "entry": np.zeros(24)}, 10_000.,
                        available_margin=10_000., ready=symbols)
    orders = Orders(symbols)
    observations = strategy.trade(state, row, account, orders)
    assert [o.symbol for o in orders.items] == ["S1", "S0"]
    np.testing.assert_allclose([o.size for o in orders.items], [-650., 487.5])
    np.testing.assert_allclose(observations["move_risk"][0, :2], [.75, 1.])
