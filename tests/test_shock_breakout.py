from types import SimpleNamespace

import numpy as np
import pytest

from quant.strategy import Portfolio
from quant.trading import Orders
from research_strategies import shock_breakout as breakout


@pytest.mark.parametrize("side", [1, -1])
def test_breakout_ranks_shocks_and_trails_booked_position(side):
    symbols = tuple(f"s{i}" for i in range(20))
    params = {k: v.default for k, v in breakout.STRATEGY.parameters.items()} | {"side": side, "slots": 1}
    state = breakout.initialize(params, symbols, None)
    qty = np.zeros(20)

    def decide(hour, prices, impulse):
        row = SimpleNamespace(symbols=symbols, status=np.zeros((1, 20), dtype="u1"), values={
            "close": [prices], "impulse": [impulse], "prior_trend": [np.zeros(20)], "prior_vol": [np.full(20, .01)],
        })
        orders = Orders(symbols)
        breakout.trade(state, row, Portfolio(hour * breakout.HOUR, {"qty": qty}, 10_000.,
                                            available_margin=10_000., ready=symbols), orders)
        return [(item.symbol, item.kind, item.size) for item in orders.items]

    spot, impulse = np.full(20, 100.), np.zeros(20)
    assert not decide(0, spot, impulse)
    impulse[:2] = side * np.array([.06, .04])
    assert decide(1, spot, impulse) == [("s0", 0, side * 2400.)]
    qty[0] = side * 24
    impulse[:] = 0
    spot[0] = 100 + side * 10
    assert not decide(2, spot, impulse)
    spot[0] = 100
    assert decide(3, spot, impulse) == [("s0", 2, 0.)]
    qty[0] = 0
    impulse[0] = side * .06
    assert not decide(4, spot, impulse)
