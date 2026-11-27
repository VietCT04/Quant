import json
from types import SimpleNamespace

import numpy as np

from quant.strategy import Portfolio
from quant.trading import Orders
from research_strategies import range_reclaim as strategy


def test_requires_reclaim_then_closes_at_frozen_range_center():
    p = {name: rule.default for name, rule in strategy.STRATEGY.parameters.items()}
    plan = strategy.prepare(p)
    assert json.loads(plan.features["lower"].parameters_json)["periods"] == 2
    assert json.loads(plan.features["probe_low"].parameters_json)["periods"] == 1
    symbols = ("A",)
    state = strategy.initialize(p, symbols, None)
    qty = np.zeros(1)

    def decide(hour, price, upper):
        fields = {"close": price, "lower": 100., "upper": upper, "probe_low": 98.5, "probe_high": 104., "drift": 0.}
        row = SimpleNamespace(symbols=symbols, status=np.zeros((1, 1), dtype="u1"),
                              values={k: np.array([[v]]) for k, v in fields.items()})
        account = Portfolio(hour * strategy.HOUR, {"qty": qty}, 10_000., available_margin=10_000., ready=symbols)
        orders = Orders(symbols)
        strategy.trade(state, row, account, orders)
        return [(o.kind, o.size) for o in orders.items]

    assert decide(0, 99.5, 120.) == []
    assert decide(1, 101., 120.) == [(0, 900.)]
    qty[0] = 9.
    assert decide(2, 108., 130.) == []
    assert decide(3, 111., 130.) == [(2, 0.)]
    qty[0] = 0.
    assert decide(4, 101., 120.) == []
