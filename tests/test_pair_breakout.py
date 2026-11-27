from types import SimpleNamespace

import numpy as np

from quant.strategy import Portfolio, prepare
from quant.trading import Orders
from research_strategies import pair_breakout as strategy


def test_follows_widening_spread_and_latches_exit_for_unavailable_peer():
    _, _, p, _ = prepare({"id": strategy.STRATEGY.id, "version": "1.0.0"}, {}, rebalance_ticks=1)
    symbols = ("BTCUSDT", "ETHUSDT")
    state = strategy.STRATEGY.initialize(p, symbols, None)
    steps = np.random.default_rng(29).normal(0, .015, p["history"]).cumsum()
    path = np.column_stack([5 + steps, 4 + steps])
    path[-1, 0] += .005
    state[2].extend(path)
    qty = np.zeros(2)
    quotes = np.full((1, 2), 5_000_000.)
    px = np.exp(path[-1] + [.014, 0.])[None, :]
    row = SimpleNamespace(symbols=symbols, status=np.zeros((1, 2), dtype="u1"),
                          values={"close": px, "turnover": quotes})

    def decide(hour):
        orders = Orders(symbols)
        account = Portfolio(hour * 3_600_000, {"qty": qty}, 10_000., available_margin=10_000., ready=symbols)
        strategy.trade(state, row, account, orders)
        return [(o.symbol, o.size) for o in orders.items]

    (buy, buy_value), (sell, sell_value) = decide(0)
    assert (buy, sell) == symbols and buy_value > 0 and sell_value < 0
    qty[:] = [1., -1.]
    assert not decide(2)
    px[0, 0] *= .98
    assert decide(4) == [("BTCUSDT", 0.), ("ETHUSDT", 0.)]
    qty[0] = 0.
    row.status[0, 1] = 2
    assert not decide(6)
    row.status[0, 1] = 0
    assert decide(8) == [("ETHUSDT", 0.)]
