from types import SimpleNamespace

import numpy as np

from quant.strategy import Portfolio, prepare
from quant.trading import Orders
from research_strategies import breadth_rebound as strategy


def test_washout_requires_broad_confirmation_and_exits_actual_book():
    _, plan, params, _ = prepare({"id": strategy.STRATEGY.id, "version": "1.0.0"}, {}, rebalance_ticks=1)
    assert plan.features["washout"].name == "quant.ts.lag"
    symbols = tuple(f"S{i}" for i in range(24))
    state = strategy.initialize(params, symbols, None)
    qty, entry = np.zeros(24), np.zeros(24)
    row = SimpleNamespace(symbols=symbols, status=np.zeros((1, 24), dtype="u1"), values={
        "close": np.full((1, 24), 100.), "washout": np.full((1, 24), -.022),
        "bounce": np.zeros((1, 24)), "prior_trend": np.zeros((1, 24)),
        "turnover": np.full((1, 24), 5_000_000.),
    })

    def decide(hour):
        orders = Orders(symbols)
        account = Portfolio(hour * strategy.HOUR, {"qty": qty, "entry": entry}, 10_000.,
                            available_margin=10_000., ready=symbols)
        strategy.trade(state, row, account, orders)
        return [(order.symbol, order.kind) for order in orders.items]

    row.values["bounce"][0, :10] = .004
    assert not decide(1)
    row.values["bounce"][0, 10:17] = .003
    assert decide(2) == [(f"S{i}", 0) for i in range(10)]
    qty[0], entry[0] = 5.5, 100.
    assert not decide(3)
    row.values["bounce"][:] = 0.
    assert decide(18) == [("S0", 2)]
