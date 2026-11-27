from types import SimpleNamespace

import numpy as np

from quant.strategy import Portfolio, prepare
from quant.trading import Orders
from research_strategies import flow_absorption as strategy


def test_flow_absorption_requires_resilient_price_and_exits_the_booked_position():
    _, graph, params, _ = prepare({"id": strategy.STRATEGY.id, "version": "1.0.0"}, {}, rebalance_ticks=1)
    assert graph.feature_periods["flow"] == 1440
    assert graph.features["flow"].inputs[0].inputs == ("buy_quote_volume",)
    symbols = tuple(f"S{i}" for i in range(22))
    state = strategy.initialize(params, symbols, None)
    qty, entry = np.zeros(22), np.zeros(22)
    row = SimpleNamespace(symbols=symbols, status=np.zeros((1, 22), dtype="u1"), values={
        "close": np.full((1, 22), 100.), "flow": np.zeros((1, 22)),
        "volume_4h": np.full((1, 22), 4_000_000.),
        "prior_volume": np.full((1, 22), 1_000_000.),
        "move": np.zeros((1, 22)), "trend": np.zeros((1, 22)), "recent": np.zeros((1, 22)),
    })
    row.values["flow"][0, :3] = [-.2, -.2, .2]
    row.values["move"][0, 1] = -.02

    def decide(hour):
        orders = Orders(symbols)
        account = Portfolio(hour * strategy.HOUR, {"qty": qty, "entry": entry}, 10_000.,
                            available_margin=10_000., ready=symbols)
        strategy.trade(state, row, account, orders)
        return [(order.symbol, order.kind, order.size) for order in orders.items]

    (symbol, kind, size), = decide(0)
    assert (symbol, kind) == ("S0", 0)
    np.testing.assert_allclose(size, 850.)
    qty[0], entry[0] = 8.5, 100.
    assert not decide(2)
    assert decide(18) == [("S0", 2, 0.)]
