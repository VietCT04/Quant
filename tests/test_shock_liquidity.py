from types import SimpleNamespace

import numpy as np
import pytest

from quant.trading import Orders
from quant.strategy import Portfolio
from research_strategies import shock_liquidity as strategy
from research_strategies import shock_liquidity_context as context


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
