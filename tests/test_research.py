"""Phase 4 causal, resource, native and durable execution regressions."""

from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from dataclasses import replace
from threading import Event
import warnings

import numpy as np
from numpy.testing import assert_allclose, assert_array_equal
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

import quant as q
from marketdata.model import write_json
from simulator.native import Engine, Settings, SETTLEMENT


@pytest.fixture
def market(tmp_path):
    root = tmp_path / "data"
    location = root / "binance" / "um" / "bars_5s"
    universe = root / "metadata" / "universes" / "liquid50.json"
    symbols = ("BTCUSDT", "ETHUSDT")
    write_json(
        universe,
        {
            "symbols": [{"symbol": s, "listing_timestamp_ms": 0} for s in symbols],
            "exchange_snapshot": "metadata/exchange.json",
        },
    )
    write_json(
        root / "metadata" / "exchange.json",
        {
            "symbols": [
                {
                    "symbol": s,
                    "filters": [
                        {"filterType": "LOT_SIZE", "stepSize": "0.01"},
                        {"filterType": "MIN_NOTIONAL", "notional": "5"},
                    ],
                }
                for s in symbols
            ]
        },
    )
    t = np.arange(0, 120000, 5000, dtype="i8")
    pq.write_table(
        pa.table(
            {
                "symbol": ["BTCUSDT"],
                "status": ["official_aggtrade_gap"],
                "start_ms": [20000],
                "end_ms": [25000],
            }
        ),
        universe.parents[1] / "coverage.parquet",
    )
    for i, s in enumerate(symbols):
        part = location / f"symbol={s}" / "year=1970" / "month=01" / "part-000.parquet"
        part.parent.mkdir(parents=True)
        keep = (t != 20000) if i == 0 else np.ones(len(t), bool)
        close = (100 + np.arange(len(t)) * (i + 1)).astype(float)
        volume = np.full(len(t), 10.0)
        volume[t == 25000] = 0.0
        pq.write_table(
            pa.table(
                {
                    "timestamp_ms": t[keep],
                    "close": close[keep],
                    "volume": volume[keep],
                    "quote_volume": (close * volume)[keep],
                    "is_empty": (volume == 0)[keep],
                }
            ),
            part,
        )
        aux = location.parent / "simulator_aux"
        funding = aux / "funding" / f"{s}.parquet"
        funding.parent.mkdir(parents=True, exist_ok=True)
        pq.write_table(
            pa.table({"timestamp_ms": [35000], "rate": [0.01], "price": [110.0]}),
            funding,
        )
        write_json(
            funding.with_suffix(".json"), {"start_ms": 0, "end_ms_exclusive": 120000}
        )
        marks = aux / "marks" / s / "1970-01.parquet"
        marks.parent.mkdir(parents=True)
        pq.write_table(
            pa.table({"available_ms": [100, 60100], "price": [100.0, 110.0]}), marks
        )
    return q.data.select(q.data.open(location), start=0, end=120000, fields=("close",))


def dynamic(block):
    sign = np.where((block.timestamps_ms // 5000) % 8 < 4, 1.0, -1.0)
    return np.repeat(sign[:, None] * 100.0, len(block.symbols), axis=1)


def test_summary_audit_single_native_equivalence_and_isolation(market, tmp_path):
    config = Settings(rebalance_ticks=1, snapshot_ticks=2)
    a = q.simulator.Candidate("a", dynamic, config=config)
    b = q.simulator.Candidate("b", allocation=(0.0, 0.0), config=config)
    serial = q.simulator.run_batch(
        market, [a, b], out=tmp_path / "serial", workers=1, block_steps=3
    )
    parallel = q.simulator.run_batch(
        market, [a, b], out=tmp_path / "parallel", workers=2, block_steps=4
    )
    audited = q.simulator.run_batch(
        market, [a], out=tmp_path / "audit", workers=1, logging="audit", block_steps=7
    )
    for result in (serial, parallel, audited):
        assert not result.failures
    standard = q.simulator.run(
        market,
        (q.simulator.targets(x, dynamic(x)) for x in q.data.iter_blocks(market)),
        config,
        out=tmp_path / "single",
        block_steps=4,
    )
    summary = serial.results["a"].report
    audit = audited.results["a"]
    assert summary == parallel.results["a"].report
    for name in (
        "end",
        "total_return",
        "maximum_drawdown",
        "execution_costs",
        "orders",
        "fills",
        "per_symbol",
        "monthly_marked_pnl",
        "funding_warnings",
        "mark_warnings",
    ):
        assert summary[name] == audit.report[name] == standard.report[name]
    assert summary["end"]["funding"] != 0 and summary["fills"] > 0
    assert summary["risk"] == audit.report["risk"]
    assert parallel.results["b"].report["end"]["fees"] == 0
    assert serial.results["a"].table("fills") is None
    assert audit.table("fills").equals(standard.table("fills"))
    assert audit.table("funding").equals(standard.table("funding"))
    fills = audit.table("fills")
    assert all(
        bar >= decision + config.latency
        for bar, decision in zip(
            fills["bar_start"].to_pylist(), fills["decision"].to_pylist()
        )
    )
    assert (
        20000 not in fills["bar_start"].to_pylist()
        and 25000 not in fills["bar_start"].to_pylist()
    )


def test_native_four_column_abi_and_logging_invariance():
    starts = np.arange(12, dtype="i8") * 5000
    values = np.empty((len(starts), 1, 5))
    values[:, :, :4] = (10.0, 10.0, 100.0, 10.0)
    values[:, :, 4] = np.where(starts[:, None] < 30000, 100.0, -100.0)
    flags = np.zeros(values.shape[:2], dtype="u1")
    events = np.array([(35000, 0, 0.01, 10.0)], dtype=SETTLEMENT)
    cfg = Settings(rebalance_ticks=1)
    with (
        Engine(cfg, [(0.01, 1.0)]) as old,
        Engine(replace(cfg, logging=2), [(0.01, 1.0)]) as shared,
    ):
        market = np.ascontiguousarray(values[:, :, :4])
        market.flags.writeable = False
        old.run(starts, values, flags, events)
        shared.run_shared(starts, market, flags, events, targets=values[:, :, 4])
        assert old.equity().tolist() == shared.equity().tolist()
        assert_array_equal(old.positions(), shared.positions())
        assert old.risk().tolist() == shared.risk().tolist()
        assert len(old.orders()) > 0 and len(shared.orders()) == 0
        assert len(old.cashflows()) > 0 and len(shared.cashflows()) == 0


def test_shared_cache_publication_and_readers(market, tmp_path):
    from quant._prepared import _window, prepared
    from quant.simulator import _feed

    cache = tmp_path / "cache"
    baseline = list(_feed(market, 5).blocks(shared=True))

    def load():
        metadata = {}
        feed = _feed(market, 5)
        blocks = list(prepared(market, feed, cache, 1 << 20, metadata))
        return blocks, metadata, feed.status_counts.copy(), feed.settlements.copy()

    with ThreadPoolExecutor(2) as threads:
        a, b = list(threads.map(lambda _: load(), range(2)))
    assert sum(result[1].get("cache_builds", 0) for result in (a, b)) == 1
    for blocks, metadata, count, events in (a, b):
        assert count.sum() == 48 and len(events) == 2
        for source, cached in zip(baseline, blocks):
            for first, second in zip(source, cached):
                assert_allclose(first, second)
            assert not cached[1].flags.writeable and not cached[2].flags.writeable
    assert not list(cache.rglob(".pending-*"))
    file = next(cache.rglob("market.npy"))
    mapped = np.load(file, mmap_mode="r")
    window = _window((file, mapped.dtype, mapped.shape, mapped.offset), 5, 12)
    assert not window.flags.writeable
    assert_allclose(window, mapped[5:12])
    with pytest.raises(MemoryError, match="disk budget"):
        list(prepared(market, _feed(market, 5), tmp_path / "too-small", 1, {}))


def test_resource_limits_holdout_and_independent_failure(market, tmp_path):
    valid = q.simulator.Candidate("ok", allocation=(0.0, 0.0))
    broken = q.simulator.Candidate("bad", lambda block: np.array(2.0))
    ran = q.simulator.run_batch(
        market, [valid, broken], out=tmp_path / "run", workers=2
    )
    assert list(ran.results) == ["ok"] and "axes" in ran.failures["bad"]
    for kwargs in ({"memory_budget_gib": 1e-7}, {"disk_budget_gib": 1e-7}):
        with pytest.raises(MemoryError):
            q.simulator.run_batch(
                market, [valid], out=tmp_path / "budget", workers=1, **kwargs
            )
    event = Event()
    event.set()
    stopped = q.simulator.run_batch(
        market, [valid], out=tmp_path / "stop", workers=1, cancel=event
    )
    assert stopped.failures == {"ok": "cancelled"}
    with pytest.raises(PermissionError):
        q.simulator.run_batch(
            replace(
                market, start=q.validation.HOLDOUT_START, end=q.validation.HOLDOUT_END
            ),
            [valid],
            out=tmp_path / "holdout",
            workers=1,
        )
    with pytest.raises(ValueError, match="Worker"):
        list(q.optimize.parallel(abs, [-1], workers=2, cpu_limit=1))
    missing = q.features.expression("quant.ts.ema", "unprojected", span=3, units="USDT")
    dependency = q.features.expression("quant.ts.ema", missing, span=4, units="USDT")
    broken = q.simulator.Candidate("feature", dynamic, {"dependency": dependency})
    isolated = q.simulator.run_batch(
        market, [valid, broken], out=tmp_path / "isolated", workers=2
    )
    assert (
        list(isolated.results) == ["ok"]
        and "unprojected" in isolated.failures["feature"]
    )
    screened = q.evaluate.screen(
        market,
        [
            q.evaluate.ScreenCandidate("ok", {}, lambda value: value["close"]),
            q.evaluate.ScreenCandidate(
                "bad", {"dependency": dependency}, lambda value: value["dependency"]
            ),
        ],
        workers=1,
    )
    assert (
        list(screened["results"]) == ["ok"]
        and "unprojected" in screened["failures"]["bad"]
    )


def test_shared_features_screen_state_and_wave_invariance(market):
    fast = q.features.expression("quant.ts.ema", "close", units="USDT/base", span=3)
    slow = q.features.expression("quant.ts.ema", "close", units="USDT/base", span=8)
    candidates = [
        q.evaluate.ScreenCandidate(
            f"s{i}",
            {"fast": fast, "slow": slow},
            lambda values: values["fast"] - values["slow"],
        )
        for i in range(3)
    ]
    one = q.evaluate.screen(
        market,
        candidates,
        horizon=2,
        workers=1,
        block_steps=5,
        groups=2,
        event_threshold=0.1,
    )
    many = q.evaluate.screen(
        market,
        candidates,
        horizon=2,
        workers=2,
        block_steps=7,
        groups=2,
        event_threshold=0.1,
    )
    assert not one["failures"] and not many["failures"]
    assert (
        one["profile"]["feature_nodes"] == 2 and many["profile"]["feature_nodes"] == 2
    )
    for a, b in zip(one["results"].values(), many["results"].values()):
        for name in (
            "mean_ic",
            "normalized_score_turnover",
            "event_return",
            "quantile_returns",
        ):
            assert_allclose(a[name], b[name], atol=1e-12)
        for name in (
            "coverage",
            "ic_observations",
            "event_observations",
            "quantile_observations",
        ):
            assert a[name] == b[name]
    assert set(q.evaluate.screen_decay(market, candidates[:1], [1, 2], workers=1)) == {
        1,
        2,
    }
    split = q.validation.Split((0, 35000), (40000, 120000))
    assert (
        len(
            list(
                q.evaluate.screen_walk_forward(
                    market, [split], lambda d: candidates[:1], workers=1
                )
            )
        )
        == 1
    )


def test_consolidated_resume_and_feature_alias(market, tmp_path):
    fast = q.features.expression("quant.ts.ema", "close", units="USDT/base", span=3)
    alias = q.features.expression(
        "quant.signal.smooth", "close", units="USDT/base", span=3
    )
    assert alias == fast
    items = [q.simulator.Candidate("p", allocation=(0.0, 0.0))]
    out = tmp_path / "group"
    first = q.simulator.run_batch(
        market, items, out=out, workers=1, resume_token="fixture"
    )
    resumed = q.simulator.run_batch(
        market, items, out=out, workers=1, resume_token="fixture"
    )
    assert (
        resumed.profile["resumed_results"] == 1
        and resumed.profile["market_blocks"] == 0
    )
    assert first.results["p"].report == resumed.results["p"].report
    assert first.results["p"].report_path == resumed.results["p"].report_path
    with pytest.raises(FileExistsError):
        q.simulator.run_batch(
            market, items, out=out, workers=1, resume_token="different"
        )


def test_threaded_correlation_matches_independent_paired_reference():
    rng = np.random.default_rng(4)
    x = rng.normal(size=(91, 17))
    y = 2 * x + rng.normal(size=x.shape)
    x[::4, 5:] = np.nan
    y[::3, ::2] = np.nan
    x[:2] = np.nan
    x[1, 0] = y[1, 0] = 1.0
    reference = []
    for a, b in zip(x, y):
        valid = np.isfinite(a) & np.isfinite(b)
        reference.append(
            np.corrcoef(a[valid], b[valid])[0, 1] if valid.sum() > 1 else np.nan
        )
    with warnings.catch_warnings():
        warnings.simplefilter("error", RuntimeWarning)
        with ThreadPoolExecutor(4) as pool:
            results = list(
                pool.map(lambda _: q.math.correlation(x, y, axis=1), range(8))
            )
    for result in results:
        assert_allclose(result, reference, rtol=1e-13)


def test_scheduler_recovery_retry_dependencies_and_capability(tmp_path):
    from quant.research import _db

    db = tmp_path / "experiments.sqlite"
    common = dict(
        hypothesis="fixture",
        start=0,
        end=10,
        universe=["BTCUSDT"],
        dataset_fingerprint="fixture",
        seed=4,
        features=[],
        evaluation={},
        costs={},
    )
    jobs = [{"kind": "a", "parameters": {"x": i}} for i in range(3)]
    a, b, c = q.research.submit(db, jobs, **common)
    assert q.research.submit(db, jobs[:1], **common) == [a]
    d = q.research.submit(db, [{"kind": "b", "depends_on": [a]}], **common)[0]
    q.research.cancel(db, c)
    with closing(_db(db)) as con, con:
        con.execute("UPDATE experiments SET status='running' WHERE id=?", (a,))
        con.execute("UPDATE research_jobs SET attempts=1 WHERE id=?", (a,))
        con.execute(
            "INSERT INTO research_attempts VALUES (?,1,?,NULL,NULL)",
            (a, q.experiment._now()),
        )
    raw_request = dict(
        name="unimplemented_fixture",
        module="quant.ts",
        purpose="test request",
        required_inputs=[],
        parameters={},
        causal=True,
        streaming_required=True,
        acceptance_criteria=["verify"],
    )
    missing = q.research.submit(
        db,
        [
            {
                "kind": "a",
                "capabilities": [
                    {"name": "quant.ts.unimplemented_fixture", "request": raw_request}
                ],
            }
        ],
        **common,
    )[0]
    attempts = {}

    def run(jobs):
        output = {}
        for job in jobs:
            key = job["id"]
            attempts[key] = attempts.get(key, 0) + 1
            if key == b and attempts[key] == 1:
                output[key] = {"failure": {"type": "SimulatedError"}}
            else:
                output[key] = {"metrics": {"score": -2.0}}
        return output

    result = q.research.run(db, {"a": run, "b": run}, claim_size=2)
    assert result["counts"] == {
        "queued": 0,
        "running": 0,
        "completed": 3,
        "failed": 1,
        "cancelled": 1,
    }
    assert q.experiment.reproduce(db, a)["outcome"]["metrics"] == {"score": -2.0}
    assert q.experiment.reproduce(db, missing)["outcome"]["failure"]["request_id"]
    assert {"InterruptedProcess", "SimulatedError"} <= {
        row["outcome"]["failure"]["type"]
        for row in result["attempt_history"]
        if row["outcome"]["failure"]
    }
    assert q.experiment.reproduce(db, d)["status"] == "completed"
