from types import SimpleNamespace

import numpy as np

from quant.strategy import Portfolio, prepare
from quant.trading import Orders
from research_strategies import flow_size_divergence as strategy


def test_quote_count_disagreement_drives_entry_and_flow_flip_drives_exit():
    _, plan, p, _ = prepare({"id": strategy.STRATEGY.id, "version": "1.0.0"}, {}, rebalance_ticks=1)
    assert plan.features["count_flow"].name == "quant.microstructure.trade_count_imbalance"
    symbols = tuple(f"S{i}" for i in range(24))
    state = strategy.initialize(p, symbols, None)
    qty, entry = np.zeros(24), np.zeros(24)
    price_series = {"close": np.full((1, 24), 100.), "dollar_flow": np.zeros((1, 24)),
                    "count_flow": np.zeros((1, 24)), "recent": np.zeros((1, 24)),
                    "prior_day": np.zeros((1, 24)), "turnover": np.full((1, 24), 3e6)}
    row = SimpleNamespace(symbols=symbols, status=np.zeros((1, 24), dtype="u1"), values=price_series)
    row.values["dollar_flow"][0, :3] = [.12, .12, -.16]
    row.values["count_flow"][0, :3] = [-.04, .08, .02]

    def decide(hour):
        orders = Orders(symbols)
        account = Portfolio(hour * strategy.HOUR, {"qty": qty, "entry": entry}, 10_000.,
                            available_margin=10_000., ready=symbols)
        strategy.trade(state, row, account, orders)
        return [(o.symbol, o.kind) for o in orders.items]

    assert decide(0) == [("S0", 0)]
    qty[0], entry[0] = 5., 100.
    row.values["dollar_flow"][0, 0] = -.12
    assert not decide(4)
    assert decide(12) == [("S0", 2)]
