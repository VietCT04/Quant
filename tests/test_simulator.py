from dataclasses import replace

import numpy as np
import pytest

from simulator.native import Engine, Settings, SETTLEMENT
from simulator.reference import ledger, simulate
from simulator.strategies import Precomputed, Signals


def tape(prices, targets, volume=100.0):
    ticks, n = np.atleast_2d(prices).T.shape
    values = np.full((ticks, n, 5), np.nan)
    prices = np.asarray(prices).reshape(ticks, n)
    values[:, :, 0] = values[:, :, 1] = values[:, :, 3] = prices
    values[:, :, 2] = np.asarray(volume).reshape(-1, 1)
    values[:, :, 4] = np.asarray(targets, dtype=float).reshape(ticks, n)
    return np.arange(ticks, dtype="i8") * 5000, values, np.zeros((ticks, n), dtype="u1")


def settings(**kwargs):
    return replace(Settings(initial_wallet=1000, fee_bps=0, spread_bps=0, slippage_bps=0, latency=0,
                            rebalance_ticks=1, snapshot_ticks=1, min_adjustment=0,
                            participation=1, drawdown_limit=0), **kwargs)


def test_causality_and_empty_gap_never_execute():
    starts, values, states = tape([100, 500, 120, 130, 110, 100], [110, np.nan, np.nan, np.nan, np.nan, np.nan])
    states[2, 0] = 1
    states[3, 0] = 3
    with Engine(settings(), [[0, 0]]) as e:
        e.run(starts[:2], values[:2], states[:2])
        assert not len(e.orders())
        e.run(starts[2:], values[2:], states[2:])
        fill, = e.orders()
        assert fill["decision"] == 5100
        assert fill["bar_start"] == 20000
        assert fill["ms"] == 25100
        assert fill["price"] == 110
        assert fill["filled"] == 1
        expected = simulate(starts.tolist(), list(zip(values[:, 0, 1], values[:, 0, 2], states[:, 0] == 0)),
                            [110, None, None, None, None, None], interval=5000, lag=100, latency=0,
                            participation=1)
        assert [(fill["decision"], fill["bar_start"], fill["ms"], fill["filled"])] == expected


def test_zero_lag_makes_subsequent_open_eligible_and_latency_blocks_it():
    starts, values, states = tape([100, 105, 110], [110, np.nan, np.nan])
    for lag, latency, expected in ((0, 0, 5000), (0, 1, 10000), (100, 5000, None)):
        with Engine(settings(lag=lag, latency=latency), [[0, 0]]) as e:
            e.run(starts, values, states)
            assert (e.orders()[0]["bar_start"] if len(e.orders()) else None) == expected


def test_reference_partial_reversal_fees_funding_and_mark_equity():
    starts, values, states = tape([100, 100, 100, 100, 110, 110, 110, 105, 105],
                                  [1000, np.nan, np.nan, np.nan, -440, np.nan, np.nan, np.nan, np.nan],
                                  [100, 100, 2, 100, 100, 100, 100, 100, 100])
    native_settings = settings(fee_bps=10, max_gross_leverage=4, leverage=4)
    funding = np.array([(20500, 0, .01, 110), (40500, 0, .02, 105)], dtype=SETTLEMENT)
    expected = ledger(1000, [("fill", 0, 2, 100, .2), ("fill", 0, 8, 100, .8),
                             ("funding", 0, 110, .01, 0), ("fill", 0, -14, 110, 1.54),
                             ("funding", 0, 105, .02, 0)], {0: 105}, 4, .005)
    with Engine(native_settings, [[0, 0]]) as e:
        e.run(starts, values, states, funding)
        fills = e.orders()
        assert fills["filled"].tolist() == pytest.approx([2, 8, -14])
        assert fills["entry"].tolist() == pytest.approx([100, 100, 110])
        assert fills["closed"].tolist() == [0, 0, 10]
        assert fills[-1]["closed_net"] == pytest.approx(97.9)
        for name in expected.keys() - {"positions"}:
            assert e.equity()[name] == pytest.approx(float(expected[name]))
        assert e.positions()[0]["qty"] == -4
        assert e.equity()["wallet"] + e.equity()["unrealized"] == pytest.approx(e.equity()["equity"])


def test_averaging_down_and_scaling_out_keep_basis():
    starts, values, states = tape([100, 100, 100, 80, 80, 80, 120, 120, 120],
                                  [200, np.nan, np.nan, 320, np.nan, np.nan, np.nan, np.nan, np.nan])
    values[6, 0, 4] = 240
    with Engine(settings(), [[1, 1]]) as e:
        e.run(starts, values, states)
        assert e.orders()["filled"].tolist() == [2, 2, -2]
        assert e.positions()["entry"].tolist() == [90]
        assert e.equity()["realized"] == 60


def test_exposure_limits_rounding_and_rejection():
    starts, values, states = tape([10, 10, 10, 10], [100, np.nan, np.nan, np.nan])
    with Engine(settings(max_symbol=37, max_adjustment=15), [[1, 5]]) as e:
        e.run(starts, values, states)
        assert e.orders()["filled"].tolist() == [1, 1]
        assert e.equity()["gross"] == 20
    with Engine(settings(max_positions=0), [[1, 5]]) as e:
        e.run(starts, values, states)
        assert e.positions()["qty"].tolist() == [0]
        assert e.orders()["reason"].tolist() == [2, 2]


def test_unmarked_position_and_leverage_changes_margin_only():
    starts, values, states = tape([50, 50, 50, 80, 90], [100, np.nan, np.nan, np.nan, np.nan])
    values[3:, 0, 3] = np.nan
    states[3:, 0] = 2
    result = []
    for leverage in (1, 4):
        with Engine(settings(leverage=leverage), [[0, 0]]) as e:
            e.run(starts, values, states)
            assert e.positions()["held_unmarked_ticks"].tolist() == [2]
            assert e.equity()["uncertain_positions"] == 1
            result.append(e.equity())
    assert result[0]["initial"] == 4 * result[1]["initial"]
    assert result[0]["equity"] == result[1]["equity"] == 1000


def test_seeded_signals_and_native_result_invariant_to_block_size():
    starts, values, states = tape(np.full(40, 10), np.full(40, np.nan))
    expected = None
    for chunk in (3, 40):
        strategy = Signals("random", ["S"], 20, 3, 15)
        with Engine(settings(rebalance_ticks=3), [[0, 0]]) as engine:
            fills = []
            for index in range(0, len(starts), chunk):
                i = slice(index, index + chunk)
                out = values[i].copy()
                strategy.apply(out, states[i])
                engine.run(starts[i], out, states[i])
                fills.extend(engine.orders()[["decision", "bar_start", "filled"]].tolist())
            if expected is None:
                expected = fills
            else:
                assert fills == expected


def test_precomputed_timestamps_and_holdout_guard(tmp_path):
    from simulator.__main__ import backtest
    from marketdata.reader import as_millis
    from types import SimpleNamespace

    starts, values, _ = tape([10, 10, 10], [np.nan, np.nan, np.nan])
    Precomputed([0, 10000], [[20], [-20]]).apply_at(starts, values)
    assert values[[0, 2], 0, 4].tolist() == [20, -20]
    with pytest.raises(ValueError, match="align"):
        Precomputed([5], [[20]]).apply_at(starts, values)
    with pytest.raises(ValueError, match="2026-08-25"):
        backtest(SimpleNamespace(end=as_millis("2026-08-26")), settings(), None, tmp_path)


def test_drawdown_flatten_is_a_new_causal_decision():
    starts, values, state = tape([100, 100, 100, 10, 10, 10, 10], [300, *[np.nan] * 6])
    with Engine(settings(drawdown_limit=.1), [[0, 0]]) as e:
        e.run(starts, values, state)
        orders = e.orders()
        assert orders["filled"].tolist() == [3, -3]
        assert orders["decision"].tolist() == [5100, 20100]
        assert orders["bar_start"].tolist() == [10000, 25000]
        assert e.equity()["risk_drawdown"] == pytest.approx(.27)


def test_simultaneous_cross_asset_signal_rows():
    starts = np.arange(4) * 5000
    tape = np.full((4, 2, 5), 100.)
    tape[:, :, 4] = np.nan
    tape[0, :, 4] = [200, -300]
    with Engine(settings(), [[0, 0], [0, 0]]) as e:
        e.run(starts, tape, np.zeros((4, 2), dtype="u1"))
        assert e.targets()["ms"].tolist() == [5100, 5100]
        assert e.orders()["bar_start"].tolist() == [10000, 10000]
        assert e.positions()["qty"].tolist() == [2, -3]


def test_mark_alignment_respects_close_availability_and_staleness(tmp_path):
    import polars as pl

    from marketdata.reader import as_millis
    from simulator.feed import MarketDataFeed

    t = as_millis("2025-06-01")
    path = tmp_path / "marks" / "BTCUSDT"
    path.mkdir(parents=True)
    pl.DataFrame({"available_ms": [t + 100, t + 60_100], "price": [105., 110.]}).write_parquet(path / "2025-06.parquet")
    feed = MarketDataFeed.__new__(MarketDataFeed)
    feed.aux_root, feed.step, feed.lag, feed.mark_staleness_ms = tmp_path, 5000, 100, 60000
    clock = t + np.array([99, 100, 60_099, 60_100, 120_100])
    minutes = (clock - 100) // 60000
    keys = np.arange(minutes[0], minutes[-1] + 1) * 60000 + 100
    result = np.full(len(clock), np.nan)
    feed._fill_marks("BTCUSDT", clock - 5100, clock, keys, minutes - minutes[0], result)
    assert np.isnan(result[[0, 4]]).all()
    assert result[1:4].tolist() == [105, 105, 110]
