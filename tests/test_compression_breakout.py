from types import SimpleNamespace

import numpy as np

from quant.strategy import Portfolio, prepare
from quant.trading import Orders
from research_strategies import compression_breakout as strategy


def test_range_escape_requires_prior_compression_and_sustained_break():
    _, plan, params, _ = prepare({"id": strategy.STRATEGY.id, "version": "1.0.0"}, {}, rebalance_ticks=1)
    assert all(plan.features[name].name == "quant.ts.lag" for name in ("short_vol", "long_vol", "upper", "lower"))
    symbols = ("A", "B", "C")
    state = strategy.initialize(params, symbols, None)
    qty, entry = np.zeros(3), np.zeros(3)
    row = SimpleNamespace(symbols=symbols, status=np.zeros((1, 3), dtype="u1"), values={
        "close": np.full((1, 3), 101.), "short_vol": np.array([[.006, .009, .006]]),
        "long_vol": np.full((1, 3), .012), "upper": np.full((1, 3), 100.),
        "lower": np.full((1, 3), 90.), "quote_now": np.array([[3e6, 3e6, 1e6]]),
        "quote_prior": np.full((1, 3), 1.5e6),
    })

    def decide(hour):
        orders = Orders(symbols)
        account = Portfolio(hour * strategy.HOUR, {"qty": qty, "entry": entry}, 10_000.,
                            available_margin=10_000., ready=symbols)
        strategy.trade(state, row, account, orders)
        return [(order.symbol, order.kind) for order in orders.items]

    assert decide(0) == [("A", 0)]
    qty[0], entry[0] = 8., 101.
    row.values["close"][0, 0] = 99.
    assert not decide(2)
    assert decide(4) == [("A", 2)]
