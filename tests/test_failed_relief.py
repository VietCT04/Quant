from types import SimpleNamespace

import numpy as np

from quant.strategy import Portfolio, prepare
from quant.trading import Orders

from research_strategies import failed_relief as strategy


def test_relief_short_waits_for_reversal_and_uses_completed_prior_trend():
    _, plan, p, _ = prepare({"id": strategy.STRATEGY.id, "version": "1.0.0"}, {}, rebalance_ticks=1)
    assert {plan.features[key].name for key in ("drift", "relief")} == {"quant.ts.lag"}
    symbols = tuple(f"S{i}" for i in range(24))
    state = strategy.initialize(p, symbols, None)
    qty, entry = np.zeros(24), np.zeros(24)
    values = {"close": np.full((1, 24), 100.), "drift": np.full((1, 24), -.06),
              "relief": np.zeros((1, 24)), "turn": np.zeros((1, 24)),
              "turnover": np.full((1, 24), 5e6)}
    row = SimpleNamespace(values=values, symbols=symbols, status=np.zeros((1, 24), dtype="u1"))

    def decide(hour):
        orders = Orders(symbols)
        account = Portfolio(hour * strategy.HOUR, {"qty": qty, "entry": entry}, 10_000.,
                            available_margin=10_000., ready=symbols)
        strategy.trade(state, row, account, orders)
        return [(o.symbol, o.kind, o.size) for o in orders.items]

    values["relief"][0, 0] = .027
    assert not decide(2)
    values["turn"][0, 0] = -.008
    assert decide(4) == [("S0", 0, -600.)]
    qty[0], entry[0] = -6., 100.
    values["drift"][:] = .01
    assert decide(24) == [("S0", 2, 0.)]
