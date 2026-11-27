from types import SimpleNamespace

import numpy as np

from quant.strategy import Portfolio, prepare
from quant.trading import Orders
from research_strategies import signed_variance as strategy


def test_signed_variance_signal_uses_past_returns_and_separate_confirmation():
    _, _, p, _ = prepare({"id": strategy.STRATEGY.id, "version": "1.0.0"}, {"history": 6}, rebalance_ticks=1)
    symbols = tuple(f"S{i}" for i in range(24))
    state = strategy.initialize(p, symbols, None)
    qty, entry = np.zeros(24), np.zeros(24)
    row = SimpleNamespace(symbols=symbols, status=np.zeros((1, 24), dtype="u1"), values={
        "close": np.full((1, 24), 100.), "move": np.zeros((1, 24)),
        "turnover": np.full((1, 24), 6e6),
    })

    def decide(hour, first, other):
        row.values["move"][0, :] = other
        row.values["move"][0, 0] = first
        orders = Orders(symbols)
        account = Portfolio(hour * strategy.HOUR, {"qty": qty, "entry": entry}, 10_000.,
                            available_margin=10_000., ready=symbols)
        observed = strategy.trade(state, row, account, orders)
        return observed["variance_balance"][0, 0], [(o.symbol, o.kind) for o in orders.items]

    for tick in range(6):
        result, orders = decide(tick * 2, -.017, .01 * (-1)**tick)
        assert np.isnan(result) and not orders
    result, orders = decide(12, .01, .01)
    assert result == -1 and orders == [("S0", 0)]
    qty[0], entry[0] = 6., 100.
    assert decide(32, 0., .01)[1] == [("S0", 2)]
