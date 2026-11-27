from types import SimpleNamespace

import numpy as np
import pytest

from quant.strategy import Portfolio
from quant.trading import Orders
from research_strategies import shock_breakout as breakout
from research_strategies import shock_breakout_managed as managed


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


def test_managed_budget_caps_entry_and_preserves_close(monkeypatch):
    params = {k: v.default for k, v in managed.STRATEGY.parameters.items()} | {
        "drawdown_sensitivity": 2., "vol_target": .01, "volume_budget": .25,
    }
    state = managed.initialize(params, ("A", "B"), None)
    state[1]["peak"] = 10_000.
    row = SimpleNamespace(symbols=("A", "B"), values={
        "prior_vol": [[.02, .02]], "prior_turnover": [[720_000., 720_000.]],
    })
    account = Portfolio(5000, {"qty": np.zeros(2)}, 8_000.)
    orders = Orders(row.symbols)

    def plan(inner, row, account, orders):
        assert inner[0]["fraction"] == pytest.approx(params["fraction"] * .6)
        orders.buy("A", notional=1000.)
        orders.close("B")
        return {}

    monkeypatch.setattr(breakout, "trade", plan)
    managed.trade(state, row, account, orders)
    assert [(intent.symbol, intent.kind, intent.size) for intent in orders.items] == [("A", 0, 250.), ("B", 2, 0.)]
