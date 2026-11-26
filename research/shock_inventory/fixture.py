"""Deterministic inventory decisions with an explicitly supplied booked position."""

from types import SimpleNamespace

import numpy as np

from quant.trading import Orders
from research_strategies import shock_inventory, shock_inventory_patient


def fixture(side, patient=False):
    module = shock_inventory_patient if patient else shock_inventory
    config = {key: rule.default for key, rule in module.STRATEGY.parameters.items()} | {"side": side}
    symbols = ("BTCUSDT",)
    state = module.initialize(config, symbols, None)

    def decide(price, qty, fast, move, sec=0):
        values = {"close": 100 + side * (price - 100), "fast": side * fast,
                  "move": side * move, "old_fast": -side * .001,
                  "prior_vol": .0003, "trend": -side * .01, "fair": 100, "prior_range_edge": 100}
        row = SimpleNamespace(symbols=symbols, status=np.array([[0]]),
                              values={key: np.array([[value]]) for key, value in values.items()})
        account = SimpleNamespace(positions={"qty": np.array([side * qty]),
                                              "entry": np.array([100 - side * .5])},
                                  clock_ms=sec * 1000, ready=symbols, available_margin=500.)
        orders = Orders(symbols)
        phase = module.trade(state, row, account, orders)["phase_code"][0, 0]
        return [intent.size for intent in orders.items], phase

    assert decide(98.85, 0, -.003, -.012) == ([side * 10], 1)
    assert decide(98.60, .102, -.0035, -.014, 30) == ([side * 10], 2)
    assert not decide(98.60, .204, -.0035, -.014, 60)[0]
    assert decide(98.35, .204, -.004, -.017, 90) == ([side * 10], 2)
    if patient:
        assert not decide(98.92, .305, .005, -.01, 120)[0]
        sizes, phase = decide(98.92, .305, .005, -.01, 540)
        assert len(sizes) == 1 and 0 < sizes[0] < .5 and phase == 3
        assert not decide(98.92, .204, .004, -.01, 600)[0]
        assert decide(98.10, .204, -.003, -.015, 660) == ([0], 7)
        return
    assert decide(98.92, .305, .005, -.01, 120) == ([-side * 10], 3)
    assert not decide(98.92, .204, .004, -.01, 150)[0]
    assert decide(98.10, .204, -.003, -.015, 180) == ([0], 7)
    assert not decide(98.01, 0, -.005, -.018, 210)[0]


if __name__ == "__main__":
    fixture(1)
    fixture(-1)
    fixture(1, patient=True)
    fixture(-1, patient=True)
    print("Long and short inventory decision fixtures passed")
