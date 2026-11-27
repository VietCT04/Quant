from types import SimpleNamespace

import numpy as np

from quant.strategy import Portfolio
from quant.trading import Orders
from research_strategies import mark_reversion as strategy


def test_mark_reversion_uses_available_mark_and_booked_positions():
    p = {name: rule.default for name, rule in strategy.STRATEGY.parameters.items()}
    assert strategy.prepare(p).features["mark_return"].inputs == ("mark",)
    symbols = ("A", "B", "UNKNOWN")
    state = strategy.initialize(p, symbols, None)
    qty = np.zeros(3)
    row = SimpleNamespace(symbols=symbols, status=np.zeros((1, 3), dtype="u1"), values={
        "close": np.array([[99., 101., 98.]]), "mark": np.array([[100., 100., np.nan]]),
        "mark_return": np.zeros((1, 3)), "close_return": np.zeros((1, 3)),
        "quote_volume": np.full((1, 3), 25_000.),
    })

    def decide(ms):
        orders = Orders(symbols)
        account = Portfolio(ms, {"qty": qty}, 10_000., available_margin=10_000., ready=symbols)
        strategy.trade(state, row, account, orders)
        return [(o.symbol, o.kind, o.size) for o in orders.items]

    start = 120 * strategy.MINUTE
    assert not decide(start)
    assert decide(start + 30000) == [("A", 0, 750.), ("B", 0, -750.)]
    qty[:2] = [7.5, -7.5]
    assert not decide(start + strategy.MINUTE)
    row.values["close"][0, :2] = [100., 103.]
    assert decide(start + 2 * strategy.MINUTE) == [("A", 2, 0.), ("B", 2, 0.)]
