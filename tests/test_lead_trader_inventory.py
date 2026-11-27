from types import SimpleNamespace

import numpy as np
import polars as pl

from quant import data
from quant.strategy import Portfolio, prepare
from quant.trading import Orders
from research.lead_trader.align import BAR, FIELDS, completed_features
from research.lead_trader.summarize import lifecycles
from research_strategies import active_pullback_inventory as strategy
from research_strategies import active_pullback_handoff as handoff


def test_event_features_exclude_the_event_bar_and_honor_availability():
    n = 3000
    values = {field: np.full(n, 100.) for field in FIELDS}
    i = 2900
    values["close"][i - 1] = 105.
    values["close"][i] = 110.
    status = np.full(n, data.TRADED, dtype="u1")
    event = i * BAR + 100
    now = completed_features([event], 0, status, values, lag=100)
    np.testing.assert_allclose(now["ret_1m"], [.05])
    values["close"][i:] = 200.
    later_changed = completed_features([event], 0, status, values, lag=100)
    np.testing.assert_equal(now["ret_1m"], later_changed["ret_1m"])
    too_early = completed_features([event - 1], 0, status, values, lag=100)
    np.testing.assert_allclose(too_early["ret_1m"], [0.])
    status[i - 3] = data.QUARANTINED
    gap = completed_features([event], 0, status, values, lag=100)
    assert np.isnan(gap["ret_1m"][0])


def test_reversal_order_allocates_only_each_lifecycles_own_quantity():
    base = dict(symbol="A", positionId="1", status="All Closed", closingPnl=0.)
    positions = pl.from_dicts([
        dict(base, side="Long", index="1", opened=1000, closed=2000, closedVolume=2., maxOpenInterest=2.),
        dict(base, side="Short", index="2", opened=2000, closed=3000, closedVolume=3., maxOpenInterest=3.),
    ])
    orders = pl.from_dicts([dict(symbol="A", orderUpdateTime=t, side=side, executedQty=q, avgPrice=100.)
                           for t, side, q in [(1000, "BUY", 2.), (2000, "SELL", 5.), (3000, "BUY", 3.)]])
    long, short = list(lifecycles(positions, orders))
    assert long["boundary_split"] and short["boundary_split"]
    assert long["actions"][-1]["size"] == 200.
    assert short["actions"][0]["size"] == 300.


def test_strategy_waits_for_actual_inventory_and_keeps_candidate_state_private():
    _, _, p, _ = prepare({"id": "active_pullback_inventory", "version": "1.0.0"}, {}, rebalance_ticks=1)
    symbols = ("A",)
    one = strategy.initialize(p, symbols, None)
    other = strategy.initialize(p, symbols, None)

    def decide(minute, price, quantity=0., fast=-.006, swing=0.):
        facts = {"close": price, "fast": fast, "swing": swing, "trend": .04,
                 "minute_quote": 12_000., "minute_buys": 6000., "reference_volume": 300_000.}
        bar = SimpleNamespace(symbols=symbols, status=np.zeros((1, 1)),
                              values={name: np.array([[value]]) for name, value in facts.items()})
        account = Portfolio(minute * strategy.MINUTE, {"qty": np.array([quantity]), "entry": np.array([100.])},
                            10_000., available_margin=10_000., ready=symbols)
        orders = Orders(symbols)
        strategy.trade(one, bar, account, orders)
        return orders.items

    assert decide(100, 100.)[0].size == 10.
    assert decide(101, 99., quantity=0.) == []
    assert decide(105, 99., quantity=.1)[0].size == 10.
    assert decide(106, 100.4, quantity=.2, fast=0.) == []
    assert decide(110, 101., quantity=.2, fast=0., swing=.01)[0].kind == 3
    assert all(x[0] == 0 for x in (other[1], other[2], other[5]))


def test_handoff_reverses_only_on_confirmed_opposite_pressure():
    _, _, p, _ = prepare({"id": "active_pullback_inventory", "version": "2.0.0"}, {}, rebalance_ticks=1)
    symbols = ("A",)
    states = handoff.STRATEGY.initialize(p, symbols, None)
    states[1][0], states[2][0], states[3][0] = 50 * strategy.MINUTE, .1, 100.
    v = {"close": 96., "fast": -.006, "swing": -.015, "trend": -.015,
         "minute_quote": 40_000., "minute_buys": 12_000., "reference_volume": 300_000.}
    bar = SimpleNamespace(symbols=symbols, status=np.zeros((1, 1)),
                          values={name: np.array([[value]]) for name, value in v.items()})
    account = Portfolio(100 * strategy.MINUTE, {"qty": np.array([.1]), "entry": np.array([100.])},
                        10_000., available_margin=10_000., ready=symbols)
    book = Orders(symbols)
    handoff.trade(states, bar, account, book)
    only, = book.items
    assert only.kind == 1
    np.testing.assert_allclose(only.size, -(.1 + 11.5 / 96))
    bar.values["minute_buys"][0, 0] = 24_000.
    close = Orders(symbols)
    handoff.trade(states, bar, account, close)
    assert close.items[0].kind == 2
