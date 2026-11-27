from types import SimpleNamespace

import numpy as np

from quant.strategy import Portfolio
from quant.trading import Orders
from research_strategies import peer_rotation as strategy


def test_peer_scores_ignore_common_moves_and_unknown_symbols():
    shape = 24
    values = np.linspace(-.03, .03, shape)
    valid = np.ones(shape, dtype=bool)
    valid[-1] = False
    volatility = np.full(shape, .01)
    for mode in ("momentum", "reversal", "pullback"):
        recent = -values.copy()
        recent[shape // 2] = 0.
        values[shape // 2] = 0.
        score = strategy.peer_scores(mode, recent, values, values, volatility, valid, .12)
        shifted = strategy.peer_scores(mode, recent + .10, values - .2, values + .15, volatility, valid, .12)
        np.testing.assert_allclose(score, shifted, atol=1e-12)
        assert np.isnan(score[-1])


def test_rotation_holds_booked_quantity_until_rank_or_expiry_requires_exit():
    n = 24
    symbols = tuple(f"S{i}" for i in range(n))
    p = {name: parameter.default for name, parameter in strategy.STRATEGY.parameters.items()} | {"slots": 2}
    state = strategy.initialize(p, symbols, None)
    ticks = np.linspace(-.04, .04, n)
    prices = np.full(n, 100.)
    row = SimpleNamespace(symbols=symbols, status=np.zeros((1, n), dtype="u1"), values={
        "close": [prices], "recent": [ticks], "slow": [ticks * 2], "previous": [ticks],
        "vol": [np.full(n, .01)],
    })
    qty = np.zeros(n)
    position = {"qty": qty, "entry": prices.copy()}

    def orders(hour):
        requests = Orders(symbols)
        account = Portfolio(hour * strategy.HOUR, position, 10_000., available_margin=10_000., ready=symbols)
        strategy.trade(state, row, account, requests)
        return [(item.symbol, item.kind) for item in requests.items]

    assert orders(0) == [(symbols[-1], 0), (symbols[-2], 0)]
    qty[-2:] = 10.
    assert orders(2) == []
    assert orders(48) == [(symbols[-2], 2), (symbols[-1], 2)]
