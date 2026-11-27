from types import SimpleNamespace

import numpy as np

from quant.strategy import Portfolio, prepare
from quant.trading import Orders

from research_strategies import compression_capacity as strategy


def test_cap_sizes_from_observed_flow_and_keeps_ordinary_exits():
    _, _, p, _ = prepare({"id": "quiet_range_breakout", "version": "1.2.0"}, {}, rebalance_ticks=1)
    symbols = ("A", "B", "C")
    state = strategy.STRATEGY.initialize(p, symbols, None)
    qty, entry = np.array([0., 0., 5.]), np.array([0., 0., 120.])
    row = SimpleNamespace(symbols=symbols, status=np.zeros((1, 3), dtype="u1"), values={
        "close": np.full((1, 3), 101.), "short_vol": np.full((1, 3), .006),
        "long_vol": np.full((1, 3), .012), "upper": np.full((1, 3), 100.),
        "lower": np.full((1, 3), 90.), "quote_now": np.full((1, 3), 3e6),
        "quote_prior": np.full((1, 3), 1.5e6), "quote_volume": np.array([[600., 30., 0.]]),
    })
    orders = Orders(symbols)
    account = Portfolio(0, {"qty": qty, "entry": entry}, 10_000., available_margin=10_000., ready=symbols)
    strategy.trade(state, row, account, orders)
    assert [(o.symbol, o.kind, o.size) for o in orders.items] == [("C", 2, 0.), ("A", 0, 90.)]
