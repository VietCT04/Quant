import numpy as np
import polars as pl

from research.lead_trader.burst_study import inventory_action, matched_pairs, same_run


def test_signed_execution_geometry():
    assert inventory_action(10, 5) == "add"
    assert inventory_action(-10, -5) == "add"
    assert inventory_action(-10, 5) == "reduce"
    assert inventory_action(10, -10) == "close"
    assert inventory_action(10, -11) == "reverse"
    assert inventory_action(-10, 11) == "reverse"


def test_burst_boundary_uses_raw_side_and_elapsed_clock():
    assert same_run(1, 1, 30_000, 30)
    assert not same_run(1, 1, 30_001, 30)
    assert not same_run(1, -1, 500, 30)
    assert not same_run(1, 1, -1, 30)


def test_matched_comparison_keeps_symbol_side_day_and_inventory_close():
    data = pl.DataFrame({
        "symbol": ["A", "A", "A", "A", "B", "A", "A"],
        "ms": [1, 2, 3, 4, 5, 6, 86_400_001],
        "held_side": [1, 1, 1, 1, 1, 1, 1],
        "prev_order_side": [1, 1, 1, -1, 1, 1, 1],
        "inventory_log": np.log([50, 52, 48, 50, 50, 120, 50]),
        "gap_log": np.log([30, 30, 31, 30, 30, 30, 30]),
        "y": [True, True, False, False, False, False, False],
    })
    pairs = matched_pairs(data)
    assert len(pairs) == 1
    assert pairs[0, 0] in (0, 1)
    assert pairs[0, 1] == 2
