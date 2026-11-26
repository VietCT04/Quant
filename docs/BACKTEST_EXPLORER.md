# Backtest Explorer

Open a completed native candidate from **Alpha Results**, a hypothesis or candidate drawer, or **Research Inbox**. The Explorer also appears in the sidebar. The URL records the hypothesis, candidate, symbol, visible range, timeframe, overlays, selected position, and table filters.

## Inspect a position

1. Open **Positions**, select **Closed only**, and sort **Net realized** ascending to find the largest closed loss.
2. Select the position ID. The chart selects its symbol, fits the holding window, and highlights its fills. The position detail opens with the execution timeline.
3. Click an execution marker, a timeline event, or a row in the **Fills**, **Requested orders**, **Target changes**, **Funding**, or **Rejected orders** tabs. Each tab counts a different event type. **Previous trade** and **Next trade** navigate booked fills.
4. Download the filtered position history as CSV. **Chart window** constrains the table to the visible range.

Price and volume come from the audited five-second Parquet reader. Complete UTC aggregation buckets use first traded open, high, low, last published close, and summed verified volume. A bucket containing unavailable source data stays blank and retains its coverage status. Confirmed empty buckets have zero volume and no candle. Partial boundary buckets are omitted. A modeled fill can lie outside the trading candle range. Its timestamp and price stay fixed when the display timeframe changes.

## Execution clocks and accounting

| Field | Meaning |
|---|---|
| Decision input bar start | Decision clock minus the native interval and availability lag. The order artifact does not distinguish a strategy signal from a risk override. |
| Decision UTC | Recorded native decision timestamp |
| Execution interval | Half-open aggregate-trade bar used for execution |
| Booked fill UTC | Native booking timestamp after the execution interval and availability lag |
| Modeled fill price | Actual modeled price. Rejected attempts use the recorded reference for their marker and show the fill price as unavailable. |
| Requested versus actual | Requested signed USDT target, requested signed base quantity, filled quantity, and resulting signed quantity are reported separately. |
| Reason | Code 0 means filled, 1 partial, 2 risk, and 3 lot, participation, or minimum notional. |

A lifecycle begins at a fill from flat and ends at flat or reversal. A reversal appears in both timelines. Fees and modeled slippage are allocated by closed and opened quantities. Native realized P&L belongs to the closed side. Funding on the same booking clock belongs to the position held before the fill, following native settlement order. The Explorer reconciles quantity, terminal average entry, gross realized P&L, fees, slippage, and funding with the native symbol report.

Table entry and exit prices are volume-weighted across the lifecycle's opening and closing fills. The execution detail displays the native running average entry. **Net realized** equals gross realized minus allocated fees plus funding. The native fill price already includes modeled spread, slippage, and impact. Return divides net realized plus terminal open P&L by total opening fill notional.

MFE and MAE are the highest and lowest **sampled gross unrealized P&L** at five-second post-fill snapshots. Official native mark inputs take precedence. A traded close serves as fallback when enabled in the recorded settings. Excursions and peak marked notional exclude stale carried marks. Coverage divides ticks with valid sampled marks by all scheduled post-fill held ticks. Terminal open P&L comes from the native report.

## Feature and performance panes

The Explorer evaluates the original registered feature graph using the native feed projection, original start, full symbol order, and SDK streaming feature evaluator. Each descriptor identifies its function, parameters, units, inputs, plot location, label, color, and provenance. Coarse buckets display their last native observations and retain the original bar and availability clocks. Missing features or incompatible original code are reported explicitly.

Candles, features, symbol exposure, symbol P&L, and portfolio performance share a synchronized timeline. Requested target and native quantity and entry are displayed as booked steps when the window fits the exact-step budget. Symbol marked notional and P&L use the last native sample in each complete bucket and require a fresh mark when exposure is open. Portfolio equity, exposure, and recorded maximum drawdown come from the native equity artifact. Downsampling retains first, last, minimum, and maximum observations per metric and bucket.

Registered strategies expose [audit-time observations](STRATEGY_AUTHORING.md) directly from their strategy trace. The Explorer uses their generic plot descriptors and shows a matched strategy rationale alongside the simulator reason. Exact booked targets and executions remain in the separate native ledgers.

## Detailed replay

Summary mode remains the default. **Generate detailed replay** queues one audit through the research service. The Explorer attaches committed detailed artifacts through existing SSE lifecycle updates. The same outcome appears in the Research Inbox. Repeated requests return the same receipt.

Replay eligibility requires a verifiable clean original execution revision, matching trusted execution sources, SDK and simulator versions, effective settings, dataset fingerprint, and symbol order. The replay retains the original range, seed, features, candidate, and evaluation protocol. Audit logging and snapshot cadence are the intentional changes. Accounting and daily marked equity must match the original report before the artifacts attach. Older detailed artifacts can attach when their original recorded revisions, frozen specifications, and accounting agree.

## API and limits

Candidate-scoped routes start at **GET /api/explorer/{job}/{resource}**. Resources include **meta**, **candles**, **features**, **series**, **positions**, **fills**, **orders**, **targets**, **funding**, and **rejected**. Chart and series calls accept **symbol**, **start**, **end**, and **tf**. Use UTC epoch milliseconds. Ledger routes support offset and limit. The positions route also supports CSV.

Candles use column projection and partition pruning. Responses are capped at 2,048 candle buckets, 500 rows per ledger page, and 500 visible markers per event type. Larger marker sets remain accessible through the paged ledger. Recent candle windows, feature windows, and symbol summaries are cached in-process. Lifecycle reconstruction has a 200,000-record budget per symbol and ledger stream. The feature evaluator caps on-demand native warm-up at 12 million cells. Very wide windows retain the portfolio pane and request zooming for symbol marks. Exposure is derived from native artifacts and the market feed on demand.

## Verification

```sh
uv run pytest -q
npm --prefix control-ui run build
uv run python -m examples.explorer_check --out bench-data/explorer/verification.json
node control-ui/tests/explorer.mjs http://127.0.0.1:8765 bench-data/explorer
```

With the original H-0001 still recorded, use a fresh isolated registry to test a candidate with summary artifacts only.

```sh
uv run python -m examples.explorer_registry bench-data/explorer-replay/research.sqlite
uv run quant service --home bench-data/explorer-replay --port 8772 start
node control-ui/tests/explorer.mjs http://127.0.0.1:8772 bench-data/explorer-replay/browser
uv run quant service --home bench-data/explorer-replay --port 8772 stop
```

The isolated browser test pauses dispatch, requests a replay, checks its queued receipt and duplicate identity, resumes dispatch, and observes completion via SSE. The production test uses H-0001 and its matching detailed replay. The verifier compares candles to the audited source, EMA samples to the SDK, fills to native Parquet, and lifecycle totals to the simulator report.

### H-0001 measurements

The June 10–17, 2026 UTC BTCUSDT development run contained 120,960 five-second slots. It reconciled 84 fills, 82 reversals, 82 closed positions, one open position, and 20 funding records. The original H-0002 audit was reused in the owner registry. A replay in the isolated registry returned in 386 ms while dispatch was paused.

The table reports loopback HTTP request and JSON transfer time on Windows with 16 logical CPUs, using three equal consecutive requests. The first request used an empty service cache for its response. OS file-cache state was uncontrolled. The service's sampled RSS rose from 212 MiB to a peak of 551 MiB during this verification run.

| Seven-day BTCUSDT endpoint | First request | Repeated requests | Response |
|---|---:|---:|---:|
| 15m candles, 672 buckets | 284 ms | 29–40 ms | 95,117 bytes |
| 15m feature series | 458 ms | 155–158 ms | 127,341 bytes |
| Fills | 48 ms | 38–53 ms | 53,089 bytes |
| Position lifecycles | 270 ms | 27 ms | 57,396 bytes |
| Synchronized series | 406 ms | 411–425 ms | 499,683 bytes |

- [Market chart and synchronized panes](backtest-explorer/market.png)
- [Selected position and execution timeline](backtest-explorer/position.png)

TradingView Lightweight Charts™ is a product of TradingView, Inc. [TradingView](https://www.tradingview.com/)
