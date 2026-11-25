"""Market, cache, timing and authorized-release contract tests using isolated fixtures."""

from dataclasses import asdict, replace

import numpy as np
from numpy.testing import assert_allclose, assert_array_equal
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

import quant as q
from marketdata.model import write_json
from simulator.native import Settings


def source(tmp_path, *, start=0):
    root = tmp_path / "data"
    location = root / "binance" / "um" / "bars_5s"
    universe = root / "metadata" / "universes" / "liquid50.json"
    universe.parent.mkdir(parents=True)
    write_json(
        universe,
        {"symbols": [{"symbol": "BTCUSDT", "listing_timestamp_ms": start + 5000}]},
    )
    t = np.arange(start, start + 30000, 5000, dtype="i8")
    coverage = pa.table(
        {
            "symbol": ["BTCUSDT"],
            "status": ["official_aggtrade_gap"],
            "start_ms": [int(t[3])],
            "end_ms": [int(t[4])],
        }
    )
    pq.write_table(coverage, universe.parents[1] / "coverage.parquet")
    part = location / "symbol=BTCUSDT" / "year=1970" / "month=01" / "part-000.parquet"
    part.parent.mkdir(parents=True)
    table = pa.table(
        {
            "timestamp_ms": t,
            "close": [1.0, 2.0, 2.0, 3.0, 4.0, 5.0],
            "volume": [1.0, 1.0, 0.0, 1.0, 1.0, 1.0],
            "quote_volume": [1.0, 2.0, 0.0, 3.0, 4.0, 5.0],
            "is_empty": [False, False, True, False, False, False],
        }
    )
    pq.write_table(table, part)
    aux = location.parent / "simulator_aux"
    for directory, clock, values in [
        (aux / "funding", "timestamp_ms", [6000, 16000]),
        (aux / "marks" / "BTCUSDT", "available_ms", [6000, 18000]),
    ]:
        directory.mkdir(parents=True)
        dst = directory / (
            "BTCUSDT.parquet" if clock == "timestamp_ms" else "1970-01.parquet"
        )
        pq.write_table(
            pa.table({clock: values, "price": [12.0, 15.0], "rate": [0.01, -0.01]}), dst
        )
    return location


def test_market_projection_coverage_aux_and_decoded_reuse(tmp_path, monkeypatch):
    path = source(tmp_path)
    d = q.data.open(path)
    selection = q.data.select(d, start=0, end=30000, fields=("close",))
    blocks = list(q.data.iter_blocks(selection, block_steps=2))
    assert_array_equal(
        np.concatenate([b.status.ravel() for b in blocks]), [4, 0, 1, 3, 0, 0]
    )
    assert_allclose(
        np.concatenate([b.values["close"].ravel() for b in blocks]),
        [np.nan, 2, 2, np.nan, 4, 5],
    )
    assert np.array_equal(blocks[0].available_at_ms, [5100, 10100])
    cache = q.features.Cache(
        directory=tmp_path / "cache", memory_bytes=0, disk_bytes=16384
    )
    cached = list(q.data.iter_blocks(selection, block_steps=2, decoded_cache=cache))
    assert len(cached) == 3 and cache.hits == 0

    def no_decode(*a, **k):
        raise AssertionError("Warm decoded blocks must avoid Parquet")

    monkeypatch.setattr(d, "iter_symbol_slices", no_decode)
    warm = list(q.data.iter_blocks(selection, block_steps=2, decoded_cache=cache))
    assert cache.hits == 3
    for a, b in zip(blocks, warm):
        assert_array_equal(a.status, b.status)
        assert_allclose(a.values["close"], b.values["close"])
    assert len(q.data.fingerprint(selection)) == 64
    funding = list(q.data.load_funding(selection))
    assert funding[0][1]["rate"].to_pylist() == [0.01, -0.01]
    marks = list(q.data.load_marks(selection))
    aligned = q.data.asof_observations(
        blocks[0],
        marks,
        value="price",
        clock="available_ms",
        max_age_ms=5000,
        units="USDT/base",
    )
    assert_allclose(aligned.values["price"].ravel(), [np.nan, 12])
    late = q.data.asof_observations(
        blocks[-1],
        marks,
        value="price",
        clock="available_ms",
        max_age_ms=5000,
        units="USDT/base",
    )
    assert np.isnan(late.values["price"]).all()
    with pytest.raises(ValueError, match="market bars"):
        q.data.select(d, start=0, end=5000, fields=["bids"])


def test_inner_outer_alignment_and_availability():
    left = q.data.Block(
        np.array([0, 5000]),
        ("BTC", "ETH"),
        np.array([5100, 10100]),
        np.zeros((2, 2), dtype="u1"),
        {"x": np.ones((2, 2))},
        5000,
        {"x": "a"},
    )
    right = replace(
        left,
        timestamps_ms=np.array([5000, 10000]),
        symbols=("ETH", "SOL"),
        available_at_ms=np.array([10110, 15110]),
    )
    a, b = q.data.align(left, right, mode="inner")
    assert a.symbols == b.symbols == ("ETH",)
    assert_array_equal(a.timestamps_ms, [5000])
    assert b.available_at_ms[0] == 10110
    a, b = q.data.align(left, right, mode="outer")
    assert a.symbols == b.symbols == ("BTC", "ETH", "SOL")
    assert_array_equal(a.timestamps_ms, [0, 5000, 10000])
    assert np.isnan(a.values["x"][:, 2]).all()
    assert np.isnan(b.values["x"][0]).all()


def test_target_stream_and_boundary(tmp_path, monkeypatch):
    from quant import simulator as sim

    path = source(tmp_path)
    selection = q.data.select(q.data.open(path), start=0, end=30000)

    def replay(feed, settings, strategy, out):
        blocks = []
        for starts, values, status in feed:
            strategy.apply_at(starts, values)
            blocks.append(values[:, :, 4].copy())
        return {"targets": np.concatenate(blocks)}

    monkeypatch.setattr(sim, "backtest", replay)

    def stream():
        for block in q.data.iter_blocks(selection, block_steps=3):
            yield sim.targets(block, np.full(block.status.shape, 100.0))

    result = sim.run(
        selection, stream(), Settings(), out=tmp_path / "simulation", block_steps=2
    )
    assert_allclose(
        result.report["targets"].ravel(), [np.nan, 100, 100, np.nan, 100, 100]
    )
    block = next(q.data.iter_blocks(selection, block_steps=2))
    targets = sim.targets(block, np.ones(block.status.shape))
    delayed = replace(targets, available_at_ms=targets.available_at_ms + 1)
    with pytest.raises(ValueError, match="available by"):
        sim.run(selection, delayed, out=tmp_path / "invalid")
    assert sim.target_digest(targets) != sim.target_digest(delayed)
    reserved = replace(
        selection,
        start=q.validation.HOLDOUT_START,
        end=q.validation.HOLDOUT_END,
        infrastructure=True,
    )
    with pytest.raises(PermissionError):
        sim.run(reserved, targets, out=tmp_path / "forbidden")


def test_release_receipt_and_sealed_native_entry(tmp_path, monkeypatch):
    from quant import simulator as sim
    from quant import experiment

    path = source(tmp_path)
    lo, hi = q.validation.HOLDOUT_START, q.validation.HOLDOUT_END
    sel = q.data.select(q.data.open(path), start=lo, end=hi, infrastructure=True)
    settings = Settings()
    targets = sim.Targets(
        np.array([lo]), sel.symbols, np.array([[100.0]]), np.array([lo + 5100])
    )
    spec = dict(
        strategy="fixtures only",
        parameters={},
        universe=list(sel.symbols),
        dataset_fingerprint=q.data.fingerprint(sel),
        git_commit="42",
        cost_model=asdict(settings),
        target_digest=sim.target_digest(targets),
    )
    release_path = tmp_path / "release.json"
    receipt = q.validation.record_final_release(
        release_path, owner_authorization="test fixture approval", frozen_spec=spec
    )
    monkeypatch.setattr(experiment, "_git", lambda: {"commit": "42", "dirty": False})

    class Feed:
        start, end = lo, hi
        step, lag = settings.interval, settings.lag
        symbols = sel.symbols

    monkeypatch.setattr(sim, "_feed", lambda data, block_steps, release: Feed())
    monkeypatch.setattr(
        sim,
        "_backtest",
        lambda feed, cfg, signals, out, release: {"id": release["release_id"]},
    )
    with pytest.raises(PermissionError):
        sim.final_run(
            sel,
            targets,
            replace(settings, fee_bps=2),
            out=tmp_path / "would-run",
            release_path=release_path,
        )
    result = sim.final_run(
        sel, targets, settings, out=tmp_path / "mock-only", release_path=release_path
    )
    assert result.report["id"] == receipt["release_id"]
    with pytest.raises(FileExistsError):
        sim.final_run(
            sel, targets, settings, out=tmp_path / "twice", release_path=release_path
        )


def test_robustness_validation():
    v = q.validation
    assert v.holdout_symbols(["B", "C", "A"], ["C"]) == (("B", "A"), ("C",))
    assert (
        v.grouped_robustness([1, 2, np.nan], ["low", "low", "high"])["low"]["mean"]
        == 1.5
    )
    assert v.cost_sensitivity(Settings(), [0, 2], lambda s: s.fee_bps) == {0: 0, 2: 10}
    x = np.arange(32, dtype=float).reshape(16, 2)
    samples = list(v.placebo_signals(x, block_size=4, repetitions=3, seed=12))
    assert_array_equal(
        samples,
        np.stack(list(v.placebo_signals(x, block_size=4, repetitions=3, seed=12))),
    )
    for sample in samples:
        assert_array_equal(np.sort(sample[:, 0]), x[:, 0])
        assert_array_equal(sample[:, 1] - sample[:, 0], 1)
    rng = np.random.default_rng(17)
    result = v.deflated_sharpe(
        rng.normal(0.01, 0.1, 90),
        trial_sharpes=[-0.4, 0, 0.1, 0.4],
        independent_trials=4,
    )
    assert 0 < result["probability"] < 1
    with pytest.raises(ValueError):
        v.deflated_sharpe(np.ones(9), trial_sharpes=[0, 1], independent_trials=2)


def test_forward_labels_at_development_end_are_purged():
    hi = q.validation.HOLDOUT_START
    clock = np.arange(hi - 20000, hi, 5000, dtype="i8")
    values = np.array([[1.0], [2.0], [3.0], [4.0]])
    source_block = q.data.Block(
        clock,
        ("A",),
        clock + 5100,
        np.zeros((4, 1), dtype="u1"),
        {"close": values},
        5000,
        {"close": "USDT/base"},
    )
    labels = q.evaluate.forward_returns(source_block, 1)
    assert_allclose(
        q.evaluate.research_values(labels).ravel(), [1, 0.5, np.nan, np.nan]
    )
    aligned = replace(source_block, available_at_ms=clock + 5101)
    assert np.isnan(q.evaluate.per_symbol_ic(aligned, labels)).all()


def test_wilder_and_streaming_forward_label_equivalence():
    from quant.data import _slice

    n = 60
    rng = np.random.default_rng(17)
    x = 100 + rng.normal(size=(n, 3))
    x[17, 0] = np.nan
    x[25:30, 1] = np.nan
    start = np.arange(n, dtype="i8") * 5000
    market = q.data.Block(
        start,
        ("A", "B", "C"),
        start + 5100,
        np.where(np.isfinite(x), 0, 2),
        {"close": x},
        5000,
        {"close": "USDT/base"},
    )
    cuts = [0, 4, 7, 17, 18, 29, 44, n]
    blocks = [_slice(market, a, b) for a, b in zip(cuts, cuts[1:])]
    f = q.features.expression("quant.ts.wilder", "close", window=8, units="USDT/base")
    streamed = q.features.evaluate(blocks, {"wilder": f})
    assert_allclose(
        np.concatenate([b.values["wilder"] for b in streamed]),
        q.ts.wilder(x, 8),
        atol=1e-12,
    )
    expected = q.evaluate.research_values(q.evaluate.forward_returns(market, 7))
    realized = list(q.evaluate.iter_forward_returns(blocks, 7))
    assert_array_equal(np.concatenate([b.timestamps_ms for b, y in realized]), start)
    assert_allclose(
        np.concatenate([q.evaluate.research_values(y) for b, y in realized]), expected
    )


def test_output_readers_and_cache_isolation(tmp_path):
    pq.write_table(
        pa.table({"equity": [100.0, 101.0, 103.0], "unused": [7, 8, 9]}),
        tmp_path / "equity.parquet",
    )
    result = q.simulator.Result(tmp_path, {})
    assert result.table("equity", columns=["equity"])["equity"].to_pylist() == [
        100,
        101,
        103,
    ]
    assert [len(batch) for batch in result.iter_batches("equity", batch_size=2)] == [
        2,
        1,
    ]
    assert result.table("fills") is None and list(result.iter_batches("fills")) == []
    with pytest.raises(ValueError, match="output table"):
        result.table("../bad")
    cache = q.features.Cache(directory=tmp_path / "cache", memory_bytes=16)
    key = "a" * 64
    arr = np.array([1.0, 2.0])
    cache.put(key, arr)
    arr[0] = 5
    assert_allclose(cache.get(key), [1, 2])
    assert not cache.get(key).flags.writeable
    cache.put("b" * 64, arr)
    assert cache.get(key) is None
    with pytest.raises(ValueError, match="cache key"):
        cache.get("../bad")
