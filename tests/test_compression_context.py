from types import SimpleNamespace

import numpy as np

from quant.strategy import Portfolio, prepare
from quant.trading import Orders
from research_strategies import compression_context as strategy


def test_weak_prior_breadth_suppresses_new_orders_without_suppressing_book_exits():
    _, plan, p, _ = prepare({"id": "quiet_range_breakout", "version": "1.1.0"}, {}, rebalance_ticks=1)
    assert plan.features["prior_peer_move"].name == "quant.ts.lag"
    symbols = tuple(f"S{i}" for i in range(22))
    state = strategy.STRATEGY.initialize(p, symbols, None)
    qty, entry = np.zeros(22), np.zeros(22)
    qty[1], entry[1], state[1][1] = 3., 110., 0
    row = SimpleNamespace(symbols=symbols, status=np.zeros((1, 22), dtype="u1"), values={
        "close": np.full((1, 22), 101.), "short_vol": np.full((1, 22), .006),
        "long_vol": np.full((1, 22), .012), "upper": np.full((1, 22), 100.),
        "lower": np.full((1, 22), 90.), "quote_now": np.full((1, 22), 3e6),
        "quote_prior": np.full((1, 22), 1.5e6), "prior_peer_move": np.full((1, 22), -.02),
    })
    account = Portfolio(3_600_000, {"qty": qty, "entry": entry}, 10_000.,
                        available_margin=10_000., ready=symbols)
    orders = Orders(symbols)
    strategy.trade(state, row, account, orders)
    assert [(o.symbol, o.kind) for o in orders.items] == [("S1", 2)]
    row.values["prior_peer_move"][0, :12] = .02
    orders = Orders(symbols)
    strategy.trade(state, row, account, orders)
    assert orders.items[0].symbol == "S1" and orders.items[0].kind == 2
    assert len(orders.items) == 8
