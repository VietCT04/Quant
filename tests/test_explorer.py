from types import SimpleNamespace

import numpy as np
import polars as pl
import pytest

from quant.explorer import Explorer, _extrema, _window, verify_provenance
from quant.explorer_ledger import accounting_at, execution, reconstruct, sample_marks
from quant.validation import HOLDOUT_START
from simulator.feed import EMPTY, TRADED


def fill(ms, delta, after, price, entry, *, closed=0, realized=0, reason=0):
    return execution({
        "ms": ms, "bar_start": ms - 5100, "decision": ms - 10100, "symbol": 0,
        "filled": delta, "after": after, "price": price, "entry": entry,
        "closed": closed, "realized": realized, "reason": reason,
        "fee": abs(delta), "slippage": abs(delta) / 2, "requested": delta * (2 if reason == 1 else 1),
        "reference": price - 0.5,
    }, 5000, 100)


def events():
    return [
        fill(5100, 2, 2, 100, 100),
        fill(10100, 1, 3, 130, 110, reason=1),
        fill(15100, -1, 2, 120, 110, closed=1, realized=10),
        fill(20100, -3, -1, 90, 90, closed=2, realized=-40),
    ]


TERMINAL = {"qty": -1, "entry": 90, "realized": -30,
            "fees": 7, "slippage": 3.5, "funding": -1.5, "unrealized": 3}


def test_native_lifecycle_allocates_reversals_and_funding_before_same_clock_fill():
    fills = events()
    funding = [{"ms": 20100, "qty": 2, "payment": -1.5}]
    positions, ledger, totals = reconstruct("BTCUSDT", fills, funding, TERMINAL, 25100)
    long, short = positions
    assert [r["type"] for r in ledger] == ["long_entry", "scale_in", "scale_out", "reversal"]
    assert fills[1]["partial"] and fills[1]["filled_notional"] == 130
    assert ledger[-1]["positions"] == [long["id"], short["id"]]
    assert (long["state"], long["opened_qty"], long["closed_qty"], long["max_qty"]) == ("closed", 3, 3, 3)
    assert (long["average_entry"], long["average_exit"]) == (110, 100)
    assert (long["gross_realized"], long["fees"], long["funding"], long["net_pnl"]) == (-30, 6, -1.5, -37.5)
    assert [r["type"] for r in long["timeline"]][-2:] == ["funding", "reversal"]
    assert (short["state"], short["exit_ms"], short["fees"], short["unrealized"]) == ("open", None, 1, 3)
    assert short["return_on_opening_notional"] == pytest.approx(2 / 90)
    assert totals == {k: TERMINAL[k] for k in ("qty", "realized", "fees", "funding", "slippage")}
    assert list(accounting_at(ledger, funding, [15100, 20100])) == [
        (2, 110, 10, 4, 0), (-1, 90, -30, 7, -1.5),
    ]
    with pytest.raises(ValueError, match="fees"):
        reconstruct("BTCUSDT", fills, funding, TERMINAL | {"fees": 7.01}, 25100)


def test_mark_excursions_are_sampled_and_skip_unverified_intervals():
    fills = events()
    positions, ledger, _ = reconstruct("BTCUSDT", fills, [{"ms": 20100, "qty": 2, "payment": -1.5}], TERMINAL, 25100)
    market = np.zeros((5, 1, 4))
    market[:, 0, 3] = [105, np.nan, 107, 93, 87]
    market[:, 0, 0] = [104, 200, 106, 92, 88]
    flags = np.array([[TRADED], [EMPTY], [TRADED], [TRADED], [TRADED]])
    feed = SimpleNamespace(step=5000, lag=100, blocks=lambda **_: iter([(np.arange(0, 25000, 5000), market, flags)]))
    sample_marks(feed, ledger, positions, fallback=True)
    long, short = positions
    assert (long["mfe"], long["mae"], long["held_ticks"], long["sampled_ticks"]) == (10, -6, 3, 2)
    assert long["max_notional"] == 214
    assert (short["mfe"], short["mae"], short["max_notional"]) == (3, -3, 93)


def test_candle_endpoint_preserves_missing_status_and_utc_bucket_values():
    from quant.explorer import EMPTY, TRADED

    # The audited five-second fixture contains one known gap and one confirmed empty trade interval.
    # Supply the same source shape with OHLCV columns through the shared data projection.
    from quant.data import Block, UNITS
    from unittest.mock import patch

    fields = ("open", "high", "low", "close", "volume")
    vals = {
        "open": [10, 11, 12, np.nan, 13, 13],
        "high": [12, 14, 15, np.nan, 13, 15],
        "low": [9, 10, 11, np.nan, 13, 12],
        "close": [11, 12, 14, np.nan, 13, 14],
        "volume": [2, 3, 4, np.nan, 0, 1],
    }
    status = np.array([TRADED, TRADED, TRADED, 3, EMPTY, TRADED], dtype="u1")
    times = np.arange(6, dtype="i8") * 5000
    block = Block(times, ("BTCUSDT",), times + 5100, status[:, None],
                  {key: np.array(value)[:, None] for key, value in vals.items()},
                  5000, {key: UNITS[key] for key in fields}, ("fixture",))
    from quant import explorer
    with patch.object(explorer.data, "select"), patch.object(explorer.data, "iter_blocks", return_value=iter([block])):
        selection = SimpleNamespace(dataset=SimpleNamespace(interval_ms=5000))
        # Call the undecorated reader so a mutable test selection is never a shared cache key.
        chart = Explorer._candles.__wrapped__(None, selection, "BTCUSDT", 0, 30000, 15000, "fixture")
    assert chart["rows"] == [
        {"t": 0, "status": TRADED, "available_ms": 15100, "open": 10, "high": 15, "low": 9, "close": 14, "volume": 9},
        {"t": 15000, "status": 3, "available_ms": 30100},
    ]
    with pytest.raises(ValueError, match="inside"):
        _window({"start_ms": 0, "end_ms": HOLDOUT_START}, {"start": 0, "end": HOLDOUT_START + 5}, 5000)


def test_portfolio_buckets_keep_extrema_and_clock_order():
    scan = pl.LazyFrame({"ms": [1, 2, 3, 4, 5, 6], "equity": [100, 50, 200, 90, 95, 99],
                         "risk_drawdown": [0.0, .5, 0.0, .55, .525, .505]})
    output = _extrema(scan, ["equity", "risk_drawdown"], 1, 7, 1)
    assert output["equity"] == [[1, 100], [2, 50], [3, 200], [6, 99]]
    assert output["risk_drawdown"] == [[1, 0], [4, .55], [6, .505]]


def test_unverifiable_origin_fails_closed():
    with pytest.raises(ValueError, match="clean, verifiable"):
        verify_provenance({"specification": {}, "outcome": {"execution": {"git": {"dirty": True}}}})
