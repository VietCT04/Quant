from types import SimpleNamespace

import numpy as np
import pytest

from quant.strategy import Portfolio
from quant.trading import Orders
from research_strategies import shock_breakout as breakout
from research_strategies import shock_breakout_confirmed as confirmed
from research_strategies import shock_breakout_distributed as distributed
from research_strategies import shock_breakout_fast as fast
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


def test_half_hour_breakout_preserves_wall_clock_warmup():
    hourly = breakout.prepare({})
    half_hour = fast.prepare({})
    assert fast.STRATEGY.orders is breakout.STRATEGY.orders
    assert half_hour.decision_ticks == 360
    assert half_hour.feature_periods == dict.fromkeys(hourly.feature_periods, 360)
    assert half_hour.warmup_bars == hourly.warmup_bars
    assert half_hour.features["impulse"] == hourly.features["impulse"]
    assert half_hour.features["prior_trend"] != hourly.features["prior_trend"]
    assert half_hour.features["prior_vol"] != hourly.features["prior_vol"]


def test_distributed_entry_quota_resets_at_utc_midnight(monkeypatch):
    params = {k: v.default for k, v in distributed.STRATEGY.parameters.items()} | {"daily_entries": 1}
    symbols = ("A", "B")
    state = distributed.initialize(params, symbols, None)

    def planned(inner, row, account, orders):
        orders.buy("A", notional=50)
        orders.close("B")
        return {}

    monkeypatch.setattr(breakout, "trade", planned)
    for now, kinds in ((0, [0, 2]), (distributed.DAY - 5_000, [2]), (distributed.DAY, [0, 2])):
        row = SimpleNamespace(symbols=symbols)
        orders = Orders(symbols)
        distributed.trade(state, row, Portfolio(now, {"qty": np.zeros(2)}, 10_000.), orders)
        assert [order.kind for order in orders.items] == kinds


def test_flow_filter_only_affects_entry_readiness_and_stop_uses_booked_price(monkeypatch):
    params = {key: rule.default for key, rule in confirmed.STRATEGY.parameters.items()} | {
        "flow_floor": .04, "drift_floor": .01, "entry_stop": .025,
    }
    symbols = ("A", "B")
    state = confirmed.STRATEGY.initialize(params, symbols, None)
    row = SimpleNamespace(symbols=symbols, status=np.zeros((1, 2), dtype="u1"), values={
        "close": np.array([[97., 100.]]), "shock_flow": np.array([[-.1, .2]]),
        "prior_drift": np.array([[.10, .03]]),
    })
    account = Portfolio(5000, {"qty": np.array([1., 0.]), "entry": np.array([100., 0.])},
                        10_000., ready=symbols)

    def entries(state, row, observed, orders):
        assert observed.ready == ("B",)
        assert observed.positions is account.positions
        orders.buy("B", notional=100.)
        return {}

    monkeypatch.setattr(distributed, "trade", entries)
    orders = Orders(symbols)
    confirmed.trade(state, row, account, orders)
    assert [(intent.symbol, intent.kind) for intent in orders.items] == [("B", 0), ("A", 2)]
