from types import SimpleNamespace

import numpy as np

from quant.strategy import Portfolio, prepare
from quant.trading import Orders

from research_strategies import defensive_rotation as strategy


def test_defensive_rotation_requires_preceding_drawdown_and_relative_strength():
    _, plan, p, _ = prepare({"id": strategy.STRATEGY.id, "version": "1.0.0"}, {}, rebalance_ticks=1)
    assert plan.features["week"].name == "quant.ts.lag"
    symbols = ("BTCUSDT", *(f"S{i}" for i in range(21)))
    state = strategy.initialize(p, symbols, None)
    qty, entry = np.zeros(22), np.zeros(22)
    row = SimpleNamespace(symbols=symbols, status=np.zeros((1, 22), dtype="u1"), values={
        "close": np.full((1, 22), 100.), "week": np.zeros((1, 22)),
        "relative_day": np.full((1, 22), -.025), "continuation": np.full((1, 22), .01),
        "upper": np.full((1, 22), 99.), "volume_2h": np.full((1, 22), 3e6),
        "volume_baseline": np.full((1, 22), 1.5e6),
    })
    row.values["relative_day"][0, 1] = .01

    def decide(hour):
        orders = Orders(symbols)
        account = Portfolio(hour * strategy.HOUR, {"qty": qty, "entry": entry}, 10_000.,
                            available_margin=10_000., ready=symbols)
        strategy.trade(state, row, account, orders)
        return [(o.symbol, o.kind) for o in orders.items]

    assert not decide(0)
    row.values["week"][0, 0] = -.065
    assert decide(2) == [("S0", 0)]
    qty[1], entry[1] = 5., 100.
    row.values["week"][0, 0] = 0.
    row.values["close"][0, 1] = 94.
    assert decide(4) == [("S0", 2)]
