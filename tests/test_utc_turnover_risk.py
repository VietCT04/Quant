from types import SimpleNamespace

import numpy as np

from marketdata.model import DAY
from quant.strategy import Portfolio, prepare
from quant.trading import Orders
from research_strategies import utc_turnover_risk as strategy


def test_clocked_risk_scaling_excludes_unknown_and_leaves_exits_intact():
    _, plan, p, _ = prepare({"id": "fixed_clock_turnover_handoff", "version": "1.3.0"}, {}, rebalance_ticks=1)
    assert plan.features["prior_vol"].name == "quant.ts.lag"
    symbols = tuple(f"S{i}" for i in range(24))
    state = strategy.STRATEGY.initialize(p, symbols, None)
    qty, entry = np.zeros(24), np.zeros(24)
    qty[2], entry[2] = 5., 110.
    t = 2 * DAY + 14 * 3_600_000  # Saturday, 14:00 UTC
    row = SimpleNamespace(symbols=symbols, timestamps_ms=np.array([t]), status=np.zeros((1, 24), dtype="u1"),
                          values={"close": np.full((1, 24), 100.), "prior_move": np.zeros((1, 24)),
                                  "turn": np.full((1, 24), .007), "quote_hour": np.full((1, 24), 3e6),
                                  "same_clock_quote": np.full((1, 24), 1e6), "prior_vol": np.full((1, 24), .024)})
    row.values["prior_move"][0, :2] = .027
    row.values["prior_vol"][0, 1] = np.nan
    account = Portfolio(t + 5000, {"qty": qty, "entry": entry}, 10_000.,
                        available_margin=10_000., ready=symbols)
    orders = Orders(symbols)
    strategy.trade(state, row, account, orders)
    assert [(o.symbol, o.kind) for o in orders.items] == [("S2", 2), ("S0", 0)]
    np.testing.assert_allclose(orders.items[-1].size, 325.)
