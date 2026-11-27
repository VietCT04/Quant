from types import SimpleNamespace

import numpy as np

from quant.strategy import Portfolio
from quant.trading import Orders
from research_strategies import pair_dislocation as strategy


def test_estimate_uses_prior_center_and_rejects_weak_links():
    rng = np.random.default_rng(29)
    steps = rng.normal(0, .015, 120).cumsum()
    history = np.column_stack([5 + steps, 4 + steps])
    z, beta, center, scale = strategy.estimate(history, history[-1] + [.03, 0.], True, .8)
    np.testing.assert_allclose((z, beta, center, scale), (6., 1., 1., .005))
    history[:, 1] *= -1
    assert strategy.estimate(history, history[-1], True, .8) is None


def test_pair_closes_an_unmatched_leg_and_keeps_cooldown():
    p = {name: rule.default for name, rule in strategy.STRATEGY.parameters.items()}
    symbols = ("BTCUSDT", "ETHUSDT")
    state = strategy.initialize(p, symbols, None)
    steps = np.random.default_rng(29).normal(0, .015, p["history"]).cumsum()
    path = np.column_stack([5 + steps, 4 + steps])
    state[2].extend(path)
    qty = np.zeros(2)
    row = SimpleNamespace(symbols=symbols, status=np.zeros((1, 2), dtype="u1"), values={
        "close": np.exp(path[-1] + [.016, 0.])[None, :], "turnover": np.full((1, 2), 2_000_000.)})

    def decide(hour):
        orders = Orders(symbols)
        account = Portfolio(hour * strategy.HOUR, {"qty": qty}, 10_000.,
                            available_margin=10_000., ready=symbols)
        strategy.trade(state, row, account, orders)
        return [(o.symbol, o.kind) for o in orders.items]

    assert decide(0) == [("ETHUSDT", 0), ("BTCUSDT", 0)]
    qty[1] = 10.
    assert decide(2) == [("ETHUSDT", 2)]
    qty[1] = 0.
    assert not decide(4)
    assert not decide(6)
