from types import SimpleNamespace

import numpy as np

from quant.strategy import Portfolio, prepare
from quant.trading import Orders
from research_strategies import utc_turnover_invalidation as strategy


def test_invalidation_uses_booked_exposure_and_completed_opposing_move():
    _, _, p, _ = prepare({"id": "fixed_clock_turnover_handoff", "version": "2.1.0"}, {}, rebalance_ticks=1)
    symbols = ("LONG", "SHORT", "FLAT", "MISSING")
    hour = 3_600_000
    state = strategy.STRATEGY.initialize(p, symbols, None)
    state[1][:] = 14 * hour + 5000

    def decide(clock, turn):
        row = SimpleNamespace(
            symbols=symbols, timestamps_ms=np.array([clock * hour]), status=np.array([[0, 0, 0, 1]]),
            values={"close": np.array([[99., 101., 99., 99.]]), "prior_move": np.zeros((1, 4)),
                    "turn": np.array([[turn, -turn, turn, turn]]), "quote_hour": np.full((1, 4), 3e6),
                    "same_clock_quote": np.full((1, 4), 1e6)},
        )
        account = Portfolio(clock * hour + 5000, {"qty": np.array([2., -2., 0., 2.]),
                                                    "entry": np.array([100., 100., 0., 100.])},
                            10_000., available_margin=10_000.)
        orders = Orders(symbols)
        series = strategy.trade(state, row, account, orders)
        assert np.isnan(series["invalidation"][0, 2:]).all()
        return [(order.symbol, order.kind) for order in orders.items]

    assert decide(15, -.01) == []
    assert decide(16, -.001) == []
    assert decide(16, -.01) == [("LONG", 2), ("SHORT", 2)]
    assert decide(22, -.01) == [("LONG", 2), ("SHORT", 2)]
