"""Causal equivalence of compiled policies, SDK features, and prepared inputs."""

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace

import numpy as np
import polars as pl
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

import quant as q
from quant._batch import Candidate
from quant._order_trial import observed_warmup
from simulator.reporting import SummaryLogs
from quant.data import Block, _slice
from quant.strategy import Runner, prepare
from simulator.native import OrderSettings, ShockFeatureStream
from test_research import market


def test_fused_feature_prefix_and_block_equivalence():
    rng = np.random.default_rng(718)
    samples = 931
    shape = (samples, 3)
    clock = np.arange(samples) * 5000
    status = np.zeros(shape, dtype="u1")
    status[406:430, 1] = 2
    status[575:590, 0] = 1
    price = np.cumprod(1 + rng.normal(0, .002, shape), axis=0) * 100
    buy, sell = rng.random((2, *shape)) * 10
    buy[status == 1] = sell[status == 1] = 0
    values = {"close": price, "quote_volume": buy + sell, "buy_quote_volume": buy, "sell_quote_volume": sell}
    for array in values.values():
        array[status > 1] = np.nan
    block = Block(clock, ("A", "B", "C"), clock + 5000, status, values, 5000,
                  {k: q.data.UNITS[k] for k in values})
    bounds = [0, 155, 411, 590, 605, len(clock)]
    split = [_slice(block, lo, hi) for lo, hi in zip(bounds, bounds[1:])]
    graph = prepare({"id": "shock_inventory_recovery", "version": "2.0.0"},
                    {"mechanism": "residual_flow"}, rebalance_ticks=1)[1].features
    ref = list(q.features.evaluate(split, graph))
    stream = ShockFeatureStream(len(block.symbols), flow=True, price_range=True)
    try:
        result = np.concatenate([stream.advance(b) for b in split], axis=1)
    finally:
        stream.close()
    mapping = ("fast", "move", "old_fast", "prior_vol", "trend", "fair", "aggression",
               "volume_current", "volume_prior", "prior_range_edge")
    for i, key in enumerate(mapping):
        expected = np.concatenate([part.values[key] for part in ref])
        np.testing.assert_allclose(result[i], expected, rtol=1e-8, atol=1e-10)
    with ThreadPoolExecutor(max_workers=2) as pool:
        stream = ShockFeatureStream(len(block.symbols), flow=True, price_range=True)
        try:
            parallel, ready = stream.advance_ready(block, warmup=361, pool=pool, workers=2)
        finally:
            stream.close()
    np.testing.assert_array_equal(parallel, result)
    expected_ready, _ = observed_warmup(status, price, np.zeros(3, dtype="i8"), 361)
    np.testing.assert_array_equal(ready, expected_ready)
    values["close"][640:, 0] *= 2
    stream = ShockFeatureStream(len(block.symbols), flow=True, price_range=True)
    try:
        changed = stream.advance(block)
    finally:
        stream.close()
    np.testing.assert_allclose(changed[:, :640], result[:, :640], rtol=1e-8, atol=1e-10)


@pytest.mark.parametrize("version", ["1.0.0", "2.0.0"])
def test_compiled_fills_equal_reference_and_warm_cache(version, market, tmp_path, monkeypatch):
    from quant._order_trial import OrderTrial

    starts = 5000 * np.arange(980)
    for ix, symbol in enumerate(market.symbols):
        base = 100 + ix * 10
        timeline = np.interp(np.arange(len(starts)), [0, 370, 393, 430, 480, 525, 600, 630, 810, 980],
                             [1, 1, .978, .963, .976, 1.004, 1, .957, .99, 1]) * base
        quote = 25 * timeline
        path = market.dataset.path / f"symbol={symbol}" / "year=1970/month=01/part-000.parquet"
        pq.write_table(pa.table({"timestamp_ms": starts, "open": timeline - .0005, "close": timeline,
                                "volume": np.full(len(starts), 25.), "quote_volume": quote,
                                "buy_quote_volume": 0.32 * quote, "sell_quote_volume": .68 * quote,
                                "is_empty": np.zeros(len(starts), dtype=bool)}), path)
    selection = replace(market, end=int(starts[-1] + 5000))

    # Keep staging ledgers in this fixture so row-for-row equality is tested as well as reports.
    monkeypatch.setattr(OrderTrial, "clean", lambda self: None)
    retained = []
    append = SummaryLogs.append

    def capture(self, name, rows):
        if name == "fills" and len(rows):
            retained.append(rows.copy())
        return append(self, name, rows)

    monkeypatch.setattr(SummaryLogs, "append", capture)

    def run(label):
        retained.clear()
        strategy = {"id": "shock_inventory_recovery", "version": version}
        resolved = prepare(strategy, {"cadence": 1}, rebalance_ticks=1)
        runner = Runner(resolved, selection.symbols, 37)
        config = Candidate("fixture", runner, resolved[1].features, config=OrderSettings(),
                           fields=runner.fields, feedback=True, warmup_bars=361)
        cache = tmp_path / "cache" if label != "reference" else None
        result = q.simulator.run_batch(selection, [config], out=tmp_path / label, logging="summary",
                                       workers=1, block_steps=151, cache_directory=cache)
        assert not result.failures, result.failures
        if label == "reference":
            fills = pl.read_parquet(tmp_path / label / "fixture/.summary-ledger/fills.parquet")
        else:
            rows = np.concatenate(retained)
            fills = pl.DataFrame({key: rows[key] for key in rows.dtype.names}).with_columns(
                pl.selectors.float().fill_nan(None)
            )
        return result.results["fixture"].report, fills, result.profile

    monkeypatch.setenv("QUANT_ORDER_ENGINE", "python")
    reference, fills, _ = run("reference")
    assert reference["fills"] > 0
    monkeypatch.delenv("QUANT_ORDER_ENGINE")
    cold, cold_fills, _ = run("cold")
    warm, warm_fills, profile = run("warm")
    assert reference == cold == warm
    assert fills.equals(cold_fills) and fills.equals(warm_fills)
    assert profile["cache_hits"] == 1 and profile["signals_worker_seconds"] == 0

    initialize = SummaryLogs.__init__

    def tiny_ledger(self, folder, symbols, limit):
        initialize(self, folder, symbols, 1)

    monkeypatch.setattr(SummaryLogs, "__init__", tiny_ledger)
    spilled, spilled_fills, _ = run("spilled")
    assert reference == spilled and fills.equals(spilled_fills)
    assert (tmp_path / "spilled/fixture/.summary-ledger/fills.parquet").is_file()
