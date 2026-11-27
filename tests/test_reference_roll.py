import json
from types import SimpleNamespace

import numpy as np

from quant.strategy import Portfolio
from quant.trading import Orders
from research_strategies import reference_roll as strategy


def test_display_roll_uses_expiring_return_and_preserves_booked_exits():
    p = {name: rule.default for name, rule in strategy.STRATEGY.parameters.items()}
    assert json.loads(strategy.prepare(p).features["expiring"].parameters_json)["periods"] == 23
    symbols = ("A", "B", "SMALL")
    state = strategy.initialize(p, symbols, None)
    qty = np.zeros(3)
    row = SimpleNamespace(symbols=symbols, status=np.zeros((1, 3), dtype="u1"), values={
        "close": np.full((1, 3), 100.), "expiring": np.array([[-.06, .045, -.07]]),
        "recent": np.zeros((1, 3)), "vol": np.full((1, 3), .01),
        "turnover": np.array([[1_000_000., 1_000_000., 50_000.]]),
    })

    def decide(hours):
        orders = Orders(symbols)
        account = Portfolio(hours * strategy.HOUR, {"qty": qty}, 10_000.,
                            available_margin=10_000., ready=symbols)
        strategy.trade(state, row, account, orders)
        return [(o.symbol, o.kind, o.size) for o in orders.items]

    assert decide(0) == [("A", 0, 1000.), ("B", 0, -1000.)]
    qty[:2] = [10., -10.]
    assert decide(1) == []
    assert decide(2) == [("A", 2, 0.), ("B", 2, 0.)]
