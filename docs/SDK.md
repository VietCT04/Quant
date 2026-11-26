# Quant research SDK

Install the locked environment with `uv sync --locked` and import `quant`. Native simulation also needs a C++20 compiler or the pinned Zig toolchain from the [README](../README.md). Market examples need the existing dataset locally.

```bash
uv run python -m quant functions
uv run python -m quant describe quant.ts.ema
uv run python -m quant search "beta neutral"
uv run python -m examples.research_sdk all
uv run pytest -q
```

Use the [API reference](API_REFERENCE.md), [function catalog](FUNCTION_CATALOG.json) or `q.registry` for the current inventory, including public types, aliases and streaming support. Regenerate both published inventories with `uv run python -m quant catalog`. Use the [Phase 4 execution guide](RESEARCH_ENGINE.md) for batch simulation, diagnostics and the durable scheduler.

For asynchronously queued, versioned implementations, see the [strategy authoring contract](STRATEGY_AUTHORING.md). Use the [order simulator](SIMULATOR_V2.md), shared causal feature graphs, order callbacks and native feedback for current research. The target example below documents the legacy compatibility path.

## Streaming a simulator experiment

```python
import quant as q

dataset = q.data.open()
selection = q.data.select(dataset, symbols=["BTCUSDT", "ETHUSDT"],
                          start="2026-06-10", end="2026-06-11",
                          fields=("close", "volume"))
fast = q.features.expression("quant.ts.ema", "close", span=12, units="USDT/base")
slow = q.features.expression("quant.ts.ema", "close", span=60, units="USDT/base")
blocks = q.features.evaluate(q.data.iter_blocks(selection), {"fast": fast, "slow": slow})
targets = (
    q.simulator.targets(block, 100 * q.math.sign(block.values["fast"] - block.values["slow"]))
    for block in blocks
)
result = q.simulator.run(selection, targets, out="bench-data/my-sdk-run")
equity = result.table("equity", columns=["ms", "equity"])
```

Use `q.experiment.register` and `q.experiment.run` to record provenance before evaluating a candidate and retain success or failure. `Result.report` retains the native execution assumptions, risk, fees, funding, and mark warnings. `Result.table` reads the output tables and `Result.iter_batches` streams large artifacts. Reusable marked-equity and closed-reduction metrics live in `q.performance`. Use `q.evaluate.iter_forward_returns` to construct diagnostic labels across read-block boundaries.

## Numerical conventions

- Integer windows count scheduled observations. Time windows such as `"5m"` require an exact multiple of `interval_ms`. All feature windows trail the current observation. Rolling functions default to complete finite samples and accept smaller `min_periods` where supported. Cumulative and expanding functions reset at nonfinite gaps. Forward fill requires an explicit positive limit.
- Variance and standard deviation default to `ddof=1`. Quantiles interpolate linearly. Cross-sectional rank uses average ties and midpoint percentiles. Top and bottom selections break ties by symbol order. Zero denominators and invalid real domains return NaN.
- EMA uses `alpha = 2 / (span + 1)` and the recursive, unadjusted update. The first finite input seeds the state and the default warm-up is one observation. `gap="reset"` restarts at a gap. `"skip"` retains state and emits NaN. `"carry"` retains state and emits the last warmed EMA. Batch EMA and `EMAState.update` use the same filter and agree across arbitrary chunks within float64 rounding tolerance.
- Wilder smoothing seeds with the mean of the first full finite window, then applies `alpha = 1/window`. A gap restarts the seed. RSI smooths positive and negative changes separately and returns 50 for a fully warmed flat series. ATR uses high minus low at the start of a run and includes distances to the prior close where observable.
- ADX applies Wilder smoothing to directional movement and DX. Aroon uses the most recent tied extreme over `window + 1` observations. MACD uses cascaded recursive EMAs. Supertrend starts down at the ATR seed. Parabolic SAR seeds direction from the first two midpoints.
- Realized volatility is RMS of the supplied returns. Annualization uses an explicit interval and a 365-day, 24/7 year. Yang–Zhang requires one OHLC observation per explicit fixed session.

## Bounded research

Use market blocks for streaming. EMA and Wilder retain constant state per symbol. Finite-lookback feature expressions retain only their declared history. Batch-only functions operate on caller-bounded sample arrays. Order statistics, some indicator variants, and per-date factor regressions can be more expensive than linear scans.

Supply `fingerprint=q.data.fingerprint(selection)` and a `q.features.Cache` to reuse stateless or finite-lookback output blocks. Recursive operators recompute state. Pass a separate cache as `decoded_cache` on `iter_blocks` to reuse projected market blocks. A complete warm cache avoids Parquet reads. A partially warm scan can still read overlapping blocks after the first miss. Budgets are per cache instance. Use each disk cache directory with one writer. Read and feature array budgets exclude library scratch allocations.

The [ten examples](../examples/research_sdk.py) cover EMA, shock events, flow imbalance, cross-sectional momentum, volatility-based sizing, forward quantiles, decay, native simulation, tracked sweeps, and capability requests. They use a fixed development hour. See [data contracts](DATA_CONTRACT.md), [causality](CAUSALITY.md), [research protocol](RESEARCH_PROTOCOL.md), [capability requests](FEATURE_REQUESTS.md) and [benchmarks](BENCHMARKS.md) for details.
