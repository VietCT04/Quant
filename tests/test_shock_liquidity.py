from types import SimpleNamespace

import numpy as np
import pytest

from quant.trading import Orders
from quant.strategy import Portfolio
from research_strategies import shock_liquidity as strategy
from research_strategies import shock_liquidity_allocated as allocated
from research_strategies import shock_liquidity_context as context
from research_strategies import shock_liquidity_extension as extension


@pytest.mark.parametrize("side", [1, -1])
def test_shock_liquidity_booked_inventory(side):
    symbols = tuple(f"S{i}" for i in range(20))
    parameters = {k: rule.default for k, rule in strategy.STRATEGY.parameters.items()} | {"side": side}
    state = strategy.initialize(parameters, symbols, None)

    def decide(price, move, recent, qty, minute):
        def field(value, other=0.):
            return np.asarray([[value, *([other] * 19)]])

        row = SimpleNamespace(symbols=symbols, status=np.zeros((1, 20), dtype="u1"), values={
            "close": field(100 + side * (price - 100), 100.),
            "move": field(side * move), "recent": field(side * recent),
            "prior_trend": field(0.), "reference": field(100., 100.),
            "prior_turnover": field(1000., 1000.),
        })
        account = SimpleNamespace(positions={"qty": field(side * qty)[0]}, ready=symbols,
                                  clock_ms=minute * 60_000, available_margin=500.)
        orders = Orders(symbols)
        phase = strategy.trade(state, row, account, orders)["phase_code"][0, 0]
        return orders.items, phase

    assert not decide(100, 0, 0, 0, 0)[0]
    orders, phase = decide(96, -.04, -.015, 0, 4)
    assert phase == 1 and [order.size for order in orders] == [side * 10]
    orders, phase = decide(95, -.045, -.013, .1, 8)
    assert phase == 2 and [order.size for order in orders] == [side * 10]
    # The additional order did not fill. Spacing still uses the prior booked seed.
    orders, phase = decide(94.9, -.05, -.014, .1, 12)
    assert phase == 2 and [order.size for order in orders] == [side * 10]
    orders, phase = decide(98, .01, .01, .2, 16)
    assert phase == 3 and 0 < orders[0].size < 1
    orders, phase = decide(100, .02, .01, .1, 20)
    assert phase == 4 and [order.size for order in orders] == [0]
    assert not decide(96, -.04, -.015, 0, 24)[0]
    assert not decide(96, -.04, -.015, 0, 28)[0]


def test_context_filters_only_entry_readiness(monkeypatch):
    params = {key: rule.default for key, rule in context.STRATEGY.parameters.items()}
    plan = context.prepare(params)
    assert plan.feature_periods["context_return"] == 720
    assert plan.warmup_bars == (params["context_hours"] + 2) * 720
    row = SimpleNamespace(symbols=("A", "B", "C", "D"),
                          values={"context_return": np.array([[.05, .02, np.nan, .06]])})
    book = np.array([0., 1., 1., 0.])
    account = Portfolio(5_000, book, 10_000., ready=("A", "B", "C"))

    def decisions(state, observed_row, portfolio, orders):
        assert portfolio.ready == ("A",)
        assert portfolio.positions is book and observed_row is row

    monkeypatch.setattr(strategy, "trade", decisions)
    context.trade((params,), row, account, None)


def test_allocated_budget_compounds_using_booked_equity(monkeypatch):
    params = {key: rule.default for key, rule in allocated.STRATEGY.parameters.items()}
    state = allocated.initialize(params, ("A",), None)
    other = allocated.initialize(params, ("A",), None)
    row = SimpleNamespace(symbols=("A",))

    def decisions(local, observed, portfolio, orders):
        assert local[0]["order_usdt"] == .04 * portfolio.equity
        assert local[0]["inventory"] == .12 * portfolio.equity
        return {}

    monkeypatch.setattr(strategy, "trade", decisions)
    account = Portfolio(5_000, np.zeros(1), 10_000.)
    assert allocated.trade(state, row, account, None)["leg_budget"].item() == 400.
    account = Portfolio(10_000, np.zeros(1), 12_500.)
    assert allocated.trade(state, row, account, None)["leg_budget"].item() == 500.
    assert "order_usdt" not in other[0] and "order_usdt" not in params


def test_extension_reduces_once_then_closes_on_trailing_reversal(monkeypatch):
    params = {key: rule.default for key, rule in extension.STRATEGY.parameters.items()}
    state = extension.initialize(params, ("A",), None)
    state[0][1][0] = 100.
    positions = {"qty": np.array([3.]), "entry": np.array([96.])}
    monkeypatch.setattr(allocated, "trade", lambda state, row, account, orders: orders.close("A") or {})

    def decision(price, clock):
        orders = Orders(("A",))
        row = SimpleNamespace(symbols=("A",), status=np.array([[0]]), values={"close": np.array([[price]])})
        extension.trade(state, row, Portfolio(clock, positions, 10_000.), orders)
        return [(item.kind, item.size) for item in orders.items]

    assert decision(100., 5_000) == [(3, .35)]
    positions["qty"][0] = 1.95
    assert decision(104., 10_000) == []
    assert decision(100., 15_000) == [(2, 0.)]
