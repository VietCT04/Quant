from types import SimpleNamespace

import numpy as np

from quant.strategy import Portfolio, prepare
from quant.trading import Orders
from research_strategies import utc_turnover_handoff as strategy


def test_handoff_observes_fixed_bars_and_prior_same_clock_turnover():
    _, plan, p, _ = prepare({"id": strategy.STRATEGY.id, "version": "1.0.0"}, {}, rebalance_ticks=1)
    assert plan.features["same_clock_quote"].name == "quant.ts.lag"
    symbols = tuple(f"S{i}" for i in range(24))
    state = strategy.initialize(p, symbols, None)
    qty, entry = np.zeros(24), np.zeros(24)
    row = SimpleNamespace(symbols=symbols, status=np.zeros((1, 24), dtype="u1"), values={
        "close": np.full((1, 24), 100.), "prior_move": np.zeros((1, 24)), "turn": np.full((1, 24), .007),
        "quote_hour": np.full((1, 24), 3e6), "same_clock_quote": np.full((1, 24), 1e6),
    })
    row.values["prior_move"][0, :2] = .027
    row.values["same_clock_quote"][0, 1] = 2.8e6

    def decide(hour):
        row.timestamps_ms = np.array([hour * strategy.HOUR])
        orders = Orders(symbols)
        account = Portfolio(hour * strategy.HOUR + 5000, {"qty": qty, "entry": entry}, 10_000.,
                            available_margin=10_000., ready=symbols)
        strategy.trade(state, row, account, orders)
        return [(o.symbol, o.kind) for o in orders.items]

    assert not decide(13)
    assert decide(14) == [("S0", 0)]
    qty[0], entry[0] = 6.5, 100.
    assert not decide(21)
    assert decide(22) == [("S0", 2)]
