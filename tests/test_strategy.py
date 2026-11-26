"""Contracts for registered strategies, causal feedback and durable execution."""

from contextlib import closing
from dataclasses import replace

import numpy as np
import polars as pl
import pytest

import quant as q
from quant import control_handlers as handlers, control_store as store, experiment
from quant.explorer import verify_provenance
from quant.strategy import Decision, Plan, Plot, Runner, Strategy, prepare, resolve_frozen
from simulator.native import Settings
from simulator.reporting import Logs
from test_research import market  # shared audited synthetic market fixture


def selector(name="ema_crossover"):
    return {"id": name, "version": "1.0.0"}


def job(name="ema_crossover", **params):
    return {"kind": "native", "strategy": selector(name), "parameters": params}


def hypothesis(market, candidate):
    return {"name": "Registered candidate", "family": "test", "mechanism": "Fixture",
            "definition": "Causal SDK feature and native callback", "prediction": "Repeatable state",
            "stage": "simulation", "candidates": [candidate],
            "protocol": {"start": market.start, "end": market.end, "universe": list(market.symbols),
                         "dataset_fingerprint": q.data.fingerprint(market), "seed": 12,
                         "evaluation": {}, "costs": {"rebalance_ticks": 1}}}


def test_registration_freezes_implementation_graph_and_parameters(market, tmp_path):
    db = tmp_path / "registry.sqlite"
    store.initialize(db)
    payload = hypothesis(market, job())
    submitted = store.submit(db, payload, handlers.validate)
    assert store.submit(db, payload, handlers.validate) == submitted
    record, = store.detail(db, submitted["submission_ids"][0], include_results=True)["experiments"]
    spec = record["specification"]
    frozen = spec["strategy"]
    assert frozen["api_version"] == 1 and len(frozen["implementation_hash"]) == 64
    assert spec["parameters"]["fast"] == 10 and frozen["graph"]["slow"]["parameters"]["span"] == 30
    assert spec["capabilities"] == [{"name": "quant.ts.ema"}]
    settings = Settings(rebalance_ticks=1)
    assert resolve_frozen(spec, settings)[3] == frozen
    with pytest.raises(ValueError, match="Incompatible"):
        resolve_frozen(spec | {"strategy": frozen | {"implementation_hash": "0" * 64}}, settings)
    with pytest.raises(ValueError, match="Unknown trusted strategy"):
        store.submit(db, hypothesis(market, job() | {"strategy": {"id": "os", "version": "1.0.0"}}), handlers.validate)
    with pytest.raises(ValueError, match="Unknown strategy parameter"):
        store.submit(db, hypothesis(market, job(code="raise SystemExit")), handlers.validate)
    with pytest.raises(ValueError, match="changed. Register a new version"):
        def changed(candidate, common):
            result = handlers.validate(candidate, common)
            return result | {"strategy": result["strategy"] | {"implementation_hash": "0" * 64}}
        store.submit(db, payload, changed)


def test_registered_feedback_reads_the_preceding_actual_book_and_is_chunk_invariant(market, tmp_path):
    def initialize(params, symbols, rng):
        return {"clocks": [], "qty": [], "draws": []}

    def decision(state, block, portfolio):
        now = int(block.available_at_ms[0])
        state["clocks"].append((now, portfolio.clock_ms))
        state["qty"].append(portfolio.positions["qty"].copy())
        state["draws"].append(portfolio.equity)
        assert not portfolio.positions.flags.writeable
        # Ask for exposure only once. The callback must not see the same request as a fill.
        return Decision([[200., -200.]] if len(state["clocks"]) == 1 else [[np.nan, np.nan]])

    local = Strategy("test_feedback", "1.0.0", {}, ("close",), lambda _: Plan(), initialize, decide=decision)
    plan = Plan(plots=(Plot("target", "Requested USDT", "USDT"),))
    settings = Settings(rebalance_ticks=1, min_adjustment=0)
    resolved = (local, plan, {}, {"decision_ticks": 1})
    results = []
    for steps in (1, 7):
        root = tmp_path / f"run-{steps}"
        runners = []
        candidates = []
        for name in ("one", "two"):
            recorder = Logs(root / name, market.symbols)
            runner = Runner(resolved, market.symbols, 11, trace=recorder)
            runners.append(runner)
            candidates.append(q.simulator.Candidate(name, runner, config=settings, fields=("close",),
                                                    feedback=True, trace_width=1))
        runs = q.simulator.run_batch(market, candidates, out=root,
                                    logging="audit", block_steps=steps, workers=2)
        assert not runs.failures, runs.failures
        for item in runners:
            item.trace.close(True)
        one, two = runners
        assert one.state is not two.state and one.state["qty"] is not two.state["qty"]
        np.testing.assert_array_equal(one.state["qty"], two.state["qty"])
        fills = pl.read_parquet(root / "one" / "fills.parquet").sort("ms")
        first = fills["ms"][0]
        assert first > one.state["clocks"][0][0]
        assert one.state["clocks"][0][1] is None
        assert one.state["draws"][0] == settings.initial_wallet
        for i, (current, observed) in enumerate(one.state["clocks"]):
            if observed is not None:
                assert observed < current
            if observed is None or observed < first:
                assert not one.state["qty"][i].any()
        active = [qty for qty in one.state["qty"] if np.any(qty)]
        assert active
        np.testing.assert_allclose(active[0], [fills.filter(pl.col("symbol") == i)["after"][0] for i in range(2)])
        trace = pl.read_parquet(root / "one" / "strategy_trace.parquet")
        assert trace["target"].drop_nulls().to_list() == [200., -200.]
        results.append((runs.results["one"].report, fills, trace))
    left, right = results[0][0], results[1][0]
    assert {k: v for k, v in left.items() if k != "closed_reductions"} == {
        k: v for k, v in right.items() if k != "closed_reductions"
    }
    for key, value in left["closed_reductions"].items():
        expected = right["closed_reductions"][key]
        if isinstance(value, float):
            assert value == pytest.approx(expected, abs=1e-12)
        else:
            assert value == expected
    assert results[0][1].equals(results[1][1])
    assert results[0][2].equals(results[1][2])


def test_future_input_perturbation_leaves_earlier_registered_targets_equal(market):
    selected = prepare(selector(), {"fast": 2, "slow": 4}, rebalance_ticks=1)
    _, plan, _, _ = selected
    blocks = list(q.data.iter_blocks(market, block_steps=6))

    def execute(raw):
        runner = Runner(selected, market.symbols, 12)
        calculated = q.features.evaluate(raw, plan.features)
        from quant._batch import _DecisionFrame
        return np.concatenate([runner(_DecisionFrame(block, {**block.values, **feature.values},
                                                     {**block.units, **feature.units}))
                               for block, feature in zip(raw, calculated)])

    changed = blocks.copy()
    last = changed[-1]
    adjusted = np.where(last.status <= 1, last.values["close"] * 3.0, np.nan)
    changed[-1] = replace(last, values={"close": adjusted})
    np.testing.assert_equal(execute(blocks)[:-6], execute(changed)[:-6])


def test_registered_job_recovers_with_the_same_frozen_receipt(market, tmp_path):
    db = tmp_path / "registry.sqlite"
    store.initialize(db)
    payload = hypothesis(market, job(fast=2, slow=5))
    key, = store.submit(db, payload, handlers.validate)["submission_ids"]
    trial, = store.detail(db, key, include_results=True)["experiments"]
    with closing(q.research._db(db)) as connection, connection:
        connection.execute("UPDATE experiments SET status='running' WHERE id=?", (trial["id"],))
        connection.execute("UPDATE research_jobs SET attempts=1 WHERE id=?", (trial["id"],))
        connection.execute("INSERT INTO research_attempts VALUES (?,?,?,NULL,NULL)", (trial["id"], 1, experiment._now()))

    class Control:
        batch_id = None

        def start(self, jobs, selection):
            assert jobs[0]["specification"]["strategy"] == trial["specification"]["strategy"]

        def finish(self, profile):
            assert profile["observed_bars"]

        def is_set(self):
            return False

    q.research.run(db, {"native": lambda jobs: handlers.execute(
        "native", jobs, source=market.dataset.path, root=tmp_path,
        budgets={"cpu_limit": 1, "memory_budget_gib": 1, "disk_budget_gib": 1}, control=Control(),
    )})
    completed, = store.detail(db, key, include_results=True)["experiments"]
    assert completed["status"] == "completed"
    assert completed["specification"] == trial["specification"]
    assert [item["failure"] for item in completed["attempts"]] == [{"type": "InterruptedProcess"}, None]
    verify_provenance(completed)
    with pytest.raises(ValueError, match="Incompatible"):
        verify_provenance(completed | {"specification": completed["specification"] | {
            "strategy": completed["specification"]["strategy"] | {"runtime_hash": "0" * 64},
        }})
