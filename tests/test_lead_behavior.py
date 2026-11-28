"""Deterministic checks for observable clocks and certified inventory opportunities."""

import numpy as np

from research.lead_trader.behavior_market import FEATURES, HOUR, ORIGIN, openings, trailing, utc_hour
from research.lead_trader.behavior_study import MINUTE, opportunity


def test_completed_hour_at_boundary_and_markout_starts_later():
    when = ORIGIN + 3*HOUR
    assert utc_hour(when) == 1
    assert utc_hour(when + 1) == 2
    features = {key:np.arange(9,dtype=float)[None,:] for key in FEATURES}
    forward = {1:np.arange(120,129,dtype=float)[None,:]}
    entry = dict(symbol="BTCUSDT",opened=when+2345,status="Partially Closed",direction=1)
    row, = openings([entry], ["BTCUSDT"], features, forward)
    assert row["relq"] == 2
    assert row["future1"] == 123  # The next full hour opens after the trader's timestamp.


def test_trailing_market_window_keeps_holes():
    x = np.array([10., np.nan, 30., 40.])
    np.testing.assert_equal(trailing(x, 2), [np.nan, np.nan, np.nan, 70.])
    np.testing.assert_equal(trailing(x, 168), [np.nan]*4)


def test_inventory_uses_known_state_and_terminal_reversal():
    life = dict(id="p", symbol="BTCUSDT", side="short")
    marks = [
        dict(ms=1, after=-2, basis=100, action="open", reversal=False),
        dict(ms=MINUTE+10, after=-3, basis=100, action="add", reversal=False),
        dict(ms=3*MINUTE, after=0, basis=None, action="close", reversal=True),
    ]
    fills = [dict(inventory_until=MINUTE+10), dict(inventory_until=3*MINUTE), dict(inventory_until=3*MINUTE+1)]
    rows = []
    opportunity(rows, life, marks, fills)
    assert [(r["ms"],r["action"],r["qty"]) for r in rows] == [
        (MINUTE, "add", -2),
        (2*MINUTE, "hold", -3),
        (3*MINUTE, "reverse", -3),
    ]
    fills[1]["inventory_until"] = 2*MINUTE - 1
    rows.clear()
    opportunity(rows, life, marks, fills)
    assert [r["action"] for r in rows] == ["add"]
