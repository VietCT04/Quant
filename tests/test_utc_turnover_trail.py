from types import SimpleNamespace

import numpy as np

from quant.strategy import Portfolio, prepare
from quant.trading import Orders
from research_strategies import utc_turnover_trail as strategy


def test_profit_trailing_uses_booked_price_and_does_not_duplicate_expiry():
    _, _, p, _ = prepare({"id": "fixed_clock_turnover_handoff", "version": "1.5.0"}, {}, rebalance_ticks=1)
    symbols = ("A",)
    state = strategy.initialize(p, symbols, None)
    start = 14 * 3_600_000 + 5000
    state[0][1][0] = start
    row = SimpleNamespace(symbols=symbols, status=np.zeros((1, 1), dtype="u1"), values={
        "close": np.array([[100.]]), "prior_move": np.zeros((1, 1)), "turn": np.zeros((1, 1)),
        "quote_hour": np.full((1, 1), 3e6), "same_clock_quote": np.full((1, 1), 1e6),
    })

    def decide(hour, px):
        timestamp = hour * 3_600_000
        row.timestamps_ms = np.array([timestamp])
        row.values["close"][0, 0] = px
        account = Portfolio(timestamp + 5000, {"qty": np.array([-2.]), "entry": np.array([100.])},
                            10_000., available_margin=10_000.)
        orders = Orders(symbols)
        strategy.trade(state, row, account, orders)
        return [(o.symbol, o.kind) for o in orders.items]

    assert not decide(15, 98.)
    assert decide(17, 99.5) == [("A", 2)]
    assert decide(22, 99.5) == [("A", 2)]
