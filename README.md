# Binance USD-M historical market data

## Quant research SDK

Start the [local Research Control Center](docs/CONTROL_CENTER.md) with `uv run quant service start --build-ui`. The service accepts development hypotheses asynchronously, executes independently of the submitting session, and records results in a durable inbox.

Use the [research execution guide](docs/RESEARCH_ENGINE.md) to run deterministic Phase 4 campaigns, share immutable market data, and replay registered experiments with full audit logs. See the [Phase 4 benchmarks](docs/RESEARCH_BENCHMARKS.md) for measured throughput.

`quant` wraps the audited Liquid 50 dataset and the existing portfolio simulator with aligned causal features, forward-label diagnostics, experiment tracking and capability discovery.

```sh
uv sync --locked
uv run python -m quant functions
uv run python -m quant describe quant.ts.ema
uv run python -m examples.research_sdk all
```

See the [SDK quickstart](docs/SDK.md), [research protocol](docs/RESEARCH_PROTOCOL.md), and [Phase 3 benchmarks](docs/BENCHMARKS.md). Research is authorized throughout the two-year archive. New research uses the [V2 next-open order model](docs/SIMULATOR_V2.md).

## Legacy portfolio simulator V1

The simulator streams the audited Liquid 50 Parquet through Arrow and bounded, time-major NumPy blocks. A C++20 engine handles native strategy decisions, market execution and linear futures accounting. Python calls the engine once per block through a C ABI that releases the GIL. The engine uses integer symbol IDs and keeps positions, pending targets, executions and valuation state independent.

```sh
uv sync --locked
# Use a C++20 compiler, or install the pinned Windows-compatible toolchain.
uv pip install ziglang==0.15.2
uv run python -m simulator.auxiliary
uv run python -m simulator.auxiliary --repair-marks
uv run python -m simulator --strategy scheduled --start 2026-06-10 --end 2026-06-17 --out data/simulator/example
uv run python -m json.tool data/simulator/example/report.json
uv run python -m simulator.benchmark --out data/simulator/benchmark.json
uv run pytest -q
```

Set `CXX` to a C++ compiler command or `QUANT_SIM_LIBRARY` to a prebuilt library. The default compiler path prefers the installed Zig package and caches a source-keyed native binary under `.sim-build`. Acquisition uses the frozen universe and writes funding and mark data beside the trade bars. The downloader resumes from its receipts and uses public data only. [Binance USD-M API](https://developers.binance.com/en/docs/catalog/core-trading-derivatives-trading-usd-s-m-futures/api/rest-api/market-data) · [Binance public archive](https://data.binance.vision/)

```python
from marketdata import MarketDataset
from marketdata.reader import as_millis
from simulator.__main__ import backtest
from simulator.feed import MarketDataFeed
from simulator.native import Settings
from simulator.strategies import Precomputed

data = MarketDataset("data/binance/um/bars_5s", universe="liquid50")
feed = MarketDataFeed(data, ["BTCUSDT", "SOLUSDT", "EVAAUSDT"], "2026-06-10", "2026-06-12")
targets = {"BTCUSDT": 0, "SOLUSDT": 100, "EVAAUSDT": -250}
signal = Precomputed([as_millis("2026-06-10")], [[targets[s] for s in feed.symbols]])
backtest(feed, Settings(rebalance_ticks=1, min_adjustment=0), signal, "data/simulator/precomputed")
```

The target values are signed USDT notionals. A signal row is indexed by the bar's start, becomes usable at that bar's availability timestamp, and is sampled only on configured rebalance ticks. NaN leaves the current target unchanged. Custom signal generation is responsible for using only data available at that timestamp. The supplied `buy_hold` strategy runs in C++. The other built-ins produce bounded arrays in Python. Their names are `scheduled`, `ema`, `zscore`, and `random`. Finite changes in targets can be scaled, sliced using `max_adjustment`, or retried across eligible bars.

### Timing, execution and accounting

- A completed bar becomes visible at its start plus its duration and the reader's lag. The first eligible execution interval starts at or after decision time plus latency. With five-second bars and the 100 ms lag, a decision based on the bar beginning at 00:00:00 is visible at 00:00:05.100. At the default latency the first eligible interval starts at 00:00:10.
- Market orders use the eligible interval's aggregate-trade VWAP as the modeled execution reference. Participation and impact use that interval's realized base volume. Fills are booked at interval completion plus lag, and funding at or before that booking timestamp applies to the prior position. Spread, slippage and impact are assumptions. Historical bid/ask prices, order book depth and exact fills are unavailable from these bars.
- Missing, quarantined, pre-listing and synthetic empty intervals have separate data states. Orders wait for a genuine tradable bar. Official mark candle closes become available after the minute closes plus 100 ms. Their default maximum age is one minute. Where a mark is unavailable, the default uses a genuine observed trade close as a marked fallback. During unobservable periods the last price remains only as an explicitly flagged indicative valuation. The engine blocks exposure increases while a held position is unmarked and allows reductions on eligible real bars. Funding events without reliable settlement marks are reported as omissions. An observed event sequence cannot establish that no unobserved funding changes occurred.
- Wallet balance includes realized PnL, fees and funding. Equity also includes unrealized PnL. Initial margin uses configured leverage and maintenance uses a configurable flat rate. Margin breaches are indicative and do not trigger a modeled Binance liquidation. Current frozen lot and minimum-notional filters are approximate for historical dates. Simultaneous orders compete for capacity in stable feed symbol order. A configured drawdown stop queues flattening instructions at the time the breach is observable.

### Legacy reports and archived research policy

Each run produces `report.json`, `experiment.json` and Parquet tables for targets, orders, fills, positions, funding and equity. Symbol IDs in logs follow the `symbols` array in the report or row order for the position table. Sharpe and Sortino use UTC daily equity returns, with 365 days per year and zero risk-free rate. Holding-time and win/loss statistics describe closed reductions using average entry age and allocated fees. They differ from marked-to-market portfolio returns, which incorporate open positions and funding. Cost attribution uses modeled execution and exchange fee assumptions.

The historical holdout policy was superseded by the V2 owner specification. The November 2026 liquidity snapshot still introduces survivorship and historical selection bias. The supplied strategies and cost sensitivity runs verify infrastructure and mechanics.

See [the simulator benchmark](BENCHMARK.md#portfolio-simulator-v1) for measured reader, engine and parallel-run results.

## Liquid 50 crypto universe

Freeze and download the Liquid 50 from the existing November 25, 2026 snapshot. This adds only the missing crypto contracts to the existing manifest for the original two-year date range. The original universe and TradFi partitions stay available. Binance's asset tags determine eligibility, including crypto-tagged tokens such as PAXG. The frozen ranking records the active-contract selection and uses the October 26 to November 24, 2026 quote volumes. It carries survivorship and historical selection bias for earlier dates.

```sh
uv run python -m marketdata.liquid50 --workers 16 --processors 7 --memory-gib 8 --temp-budget-gib 20 --retry-missing
uv run python -m marketdata.liquid50 --report-only
```

The configuration, acquisition metrics, rank-by-rank dataset inventory, and uncovered intervals are written under `data/metadata/universes`. Coverage percentages count five-second calendar slots from the later of the UTC listing date and the requested start date, through the requested end date.

```python
from marketdata import MarketDataset

dataset = MarketDataset("data/binance/um/bars_5s", universe="liquid50")
for batch in dataset.iter_batches(None, "2026-11-23", "2026-11-24", ["symbol", "timestamp_ms", "close"]):
    ...
```

The pipeline turns checksum-verified Binance Futures aggregate-trade ZIPs into UTC-aligned OHLCV and aggressive trade-flow bars. It runs on Windows and Linux with Python 3.12–3.14. The `uv.lock` file pins the tested dependency graph.

## Run and resume

```sh
uv sync --locked
uv run pytest -q
uv run python -m marketdata universe --top 50 --volume-window 30d
uv run python -m marketdata plan --years 2 --interval 5s
uv run python -m marketdata download --workers 16 --dry-run
uv run python -m marketdata download --workers 16
uv run python -m marketdata status
uv run python -m marketdata validate
```

The default range starts two calendar years before the current UTC date and ends at that date, exclusive. On November 25, 2026, the range is `[2024-11-25, 2026-11-25)`. UTC date inputs use the same half-open convention. Resume is automatic. The `--retry-missing` flag rechecks incomplete partitions. `update` extends a frozen plan to the latest completed UTC day by default, with optional `--end YYYY-MM-DD`. It reuses committed daily fragments for the extending month and skips unchanged validated months. Each data directory supports one universe, interval, and start date.

```sh
uv run python -m marketdata update --workers 16 --retry-missing
uv run python -m marketdata --data-dir data-1s plan --start 2026-10-01 --end 2026-11-01 --interval 1s --symbols BTCUSDT ETHUSDT
uv run python -m marketdata --data-dir data-1s download --processors 2 --memory-gib 4 --temp-budget-gib 12 --disk-budget-gib 64 --temp-dir /path/to/ssd
uv run python -m marketdata.benchmark --day 2026-11-20 --month 2026-10-01 --launch
```

Supported resolutions are integral seconds or minutes that divide one minute. The benchmark command uses BTC, ETH, and LINK for its daily sample, then BTC, LINK, and XAU for a completed month. It audits both samples, estimates dataset size from official archive sizes, and uses the storage gate before launching the frozen plan. Use an independent data directory for benchmark samples. A download exits nonzero for failed or incomplete partitions and still retains covered data and the reason for every gap. `--keep-raw` retains verified ZIPs subject to the temporary storage budget.

## Universe and reproducibility

The ranking sums the `quote asset volume` field in official UTC daily futures klines as decimal USDT. Eligible contracts are active `PERPETUAL` or `TRADIFI_PERPETUAL` products with USDT margin and quote. Volume alone determines rank, with symbol name breaking ties. A data directory freezes the ranked set and selection time in `universe.json`. `universe_rankings.json`, `symbols.parquet`, and the original exchange-info snapshot preserve the observed universe and listing metadata. An archive update is detectable from the original checksum recorded in the manifest. To reproduce a committed partition later, retain its Parquet or original checked ZIPs because Binance can revise archives.

**Survivorship and look-ahead bias.** The historical data uses contracts active at selection time and ranks them on recent volume. Contracts delisted before selection are excluded, and this universe uses information unavailable at earlier strategy dates. The saved exchange observations include inactive contracts still returned by Binance and can seed a future point-in-time universe. Past exchange states and historical trading halts require additional sources. [Binance USD-M market data documentation](https://developers.binance.com/en/docs/catalog/core-trading-derivatives-trading-usd-s-m-futures/api/rest-api/market-data)

## Aggregation and validation

- The downloader streams ZIPs with pooled HTTP connections, shared 429 cooldown, jittered retries, resumable Range requests, checksum verification, and atomic publication. Separate bounded queues overlap acquisition and processing. CPU workers stream Arrow CSV blocks into Polars aggregations. Unsorted archives take a bounded DuckDB spill path. Conflicting duplicate IDs, invalid rows and overlapping constituent ID spans fail validation.
- Completed months use published monthly archives where available. Short edge ranges and the open calendar month use daily ZIPs. Full daily fallback applies if the monthly source is unavailable or cannot be reconciled. When a localized intramonth gap is detected, the pipeline checks a daily archive for the affected interval and requires disjoint trade IDs before accepting a repair. A partition publishes a single chronological copy of each bar.
- The pipeline reconstructs 1-minute OHLCV and taker flow from the 5-second bars for every usable reference minute. Minute differences are measured and recorded. A partition is `qualified` if structural and daily totals pass and its source has minute-level differences or quarantined gaps. Daily tolerances are 0.05% for volume and quote totals and 0.2% for constituent counts. A failed reconciliation remains visible in the manifest and is excluded from covered output. No 5-second bars are synthesized from klines.
- Binance assigns one timestamp to an aggregate that can contain individual fills on either side of a bar boundary. `trade_count` and its buy/sell subdivisions sum inclusive reported ID spans, `last_trade_id - first_trade_id + 1`. ID spans are checked for validity and overlap. This is a derived count and may differ from numbers in official klines and the individual-trade archive. `max_trade_qty` is the maximum aggregate quantity in the bar.
- Empty bars use only earlier prices. The pipeline fills covered 5-second intervals in official positive-volume 1-minute candles where aggregate trades are present for that minute. Unconfirmed fully zero-volume minutes, missing reference or trade archives, unverified suspensions, and pre-listing intervals do not receive fills. A confirmed empty bar has zero flow and counts, null VWAP, and `is_empty=true`. A known close carries across source and partition boundaries only when coverage is contiguous. The first interval with trades can straddle an intraminute listing instant.

Sources and samples use Binance’s public archive and developer documentation. [Archive format and checksums](https://github.com/binance/binance-public-data) · [Binance USD-M market data](https://developers.binance.com/en/docs/catalog/core-trading-derivatives-trading-usd-s-m-futures/api/rest-api/market-data)

## Dataset and simulator contract

```text
data/
  binance/um/bars_5s/symbol=BTCUSDT/year=2026/month=10/part-000.parquet
  metadata/universe.json
  metadata/universe_rankings.json
  metadata/symbols.parquet
  metadata/manifest.sqlite
  metadata/coverage.parquet
  metadata/validation_report.json
  metadata/validation_summary.txt
  metadata/benchmark_report.json
```

Each symbol-month contains a single ZSTD Parquet file with 65,536-row groups and column statistics. The schema version and interval are stored in the footer. `timestamp_ms` is an int64 UTC epoch timestamp for the beginning of the half-open interval. OHLC, base and quote flows, VWAP, and maximum aggregate quantity are float64. Aggregate count is uint32, fill counts are uint64, and `is_empty` is boolean. VWAP is null for empty bars. Symbol comes from the partition and is available in the reader.

```python
from marketdata import MarketDataset

dataset = MarketDataset("data/binance/um/bars_5s")
symbols = ["BTCUSDT", "ETHUSDT"]
columns = ["symbol", "timestamp_ms", "available_at_ms", "close", "volume", "is_empty"]

for batch in dataset.iter_batches(symbols, "2025-01-01", "2025-02-01", columns):
    # Each completed bar can enter the simulator at available_at_ms.
    use(batch)

for timestamps, frames in dataset.iter_aligned(
    symbols, "2025-01-01", "2025-01-02", ["close", "volume", "is_empty"]
):
    # frames[symbol]["present"] is false when the input has no verified bar.
    use_aligned(timestamps, frames)
```

`load` materializes a short time slice as an Arrow table. `iter_batches` interleaves rows chronologically across symbols, and `iter_aligned` returns a shared time axis with nulls for missing values. The reader prunes by month and applies an Arrow timestamp filter and column projection. `coverage()` distinguishes covered, quarantined, missing, and pending ranges. Strategy decisions may use a bar at or after `available_at_ms`, which defaults to `timestamp_ms + interval_ms + 100`. The extra 100 ms covers Binance's documented aggregate-trade grouping window for archive-time simulation. It does not model live network latency.

The SQLite manifest commits each validated fragment before its raw ZIP can be deleted. Finished partitions are published atomically and audited by hash, schema, chronology, bar invariants, and covered interval counts. Invalid input ZIPs are retained for inspection and count against the raw budget. Increase the budget or move the dataset to a larger SSD before retrying if retained failures exhaust the cap.

See [BENCHMARK.md](BENCHMARK.md) for measured throughput and archive findings.
