# Symbol replay workstation

Open a completed native candidate from Alpha Results, a hypothesis, or the research inbox. The [native BTCUSDT walkthrough](http://127.0.0.1:8765/#explorer?hypothesis=H-0001&candidate=48b299ec64e74adc9f24d3315ea04bc3&symbol=BTCUSDT) and the [Binance public archive](http://127.0.0.1:8765/#explorer?source=binance&symbol=AKEUSDT) are live when the Control Center is running. The source selector switches between the two. Binance refresh reads the collector's SQLite archive through a read-only connection.

## Review a symbol

1. Pick an instrument in the watchlist. Market volume is audited USDT quote turnover. Own volume is the sum of executed quantity times booked price. Counts and P&L use the selected historical reporting period. Dates are UTC. The coverage dot measures available five-second bars in that requested period.
2. Select a booked fill on the chart or ledger. Arrows always point left and are green for BUY and red for SELL. The arrow tip uses the bucket coordinate of the actual booking clock and the price scale coordinate of the executed fill. This includes fills outside the visible candle's range. Dense labels yield to the selected label. Click overlapping heads to select a specific fill.
3. Use the persistent transport to step execution, lifecycle or candle. Paste a UTC timestamp, an execution ID, or `position:<id>` in Jump. Next and Previous use a deterministic sort within the current symbol and never switch instruments. Use the keyboard reference for shortcuts.
4. Change candle resolution manually. Execution navigation preserves the viewport when a marker is comfortably visible. **Center**, **Fit current position**, **Fit fills**, and **Reset viewport** are explicit. Fitting a long lifecycle may require selecting a coarser resolution. The three default panes are signed inventory, unrealized P&L, and cumulative realized P&L. Use **Panels** to toggle panes, switch marked/base inventory, and enable recorded overlays. Drag pane separators to resize.
5. Use the positions tab for lifecycle results. **Mark excursions** samples native marks when requested. Available legacy position exports and raw order, funding, target, and rejection resources remain in the original Explorer API.

Candle resolution and display preferences persist locally. The URL retains the source, candidate, symbol, viewport and selected execution. Each symbol remembers its last inspected fill on this browser.

## Clocks and provenance

**Analysis** displays surrounding observations on both sides. **True replay** caps feature, candle, and execution output at the replay clock, including deterministic ordering within the same millisecond. An in-progress aggregate only uses audited five-second observations published by that clock. Later chart slots remain blank. A missing or quarantined input prevents a candle from representing the affected bucket.

Native fill prices include modeled execution costs. The native ledger distinguishes gross realized P&L from realized P&L net of booked fees and funding. The break-even overlay reflects allocated costs already booked in the current lifecycle and excludes hypothetical future closing costs. Virtual terminal exits remain explicitly labeled. MAE and MFE are sampled gross unrealized P&L using native marks and the fallback in the original settings.

External orders retain the exact reported timestamp, price, quantity, and P&L. An unmatched nonzero reported P&L indicates realized exposure but leaves the exact action unknown. Inventory is reconstructed only inside order intervals certified by the collector and reconciled to the published lifecycle quantities. A confirmed reversal appears as one booked order across the two lifecycle allocations. Where inventory is reconstructed and an audited candle is available, symbol valuation is an estimate at the available close. External fees, account equity, and funding remain unknown. External True Replay hides state inferred later from a published position summary. Reported cumulative P&L sums captured orders and has incomplete coverage wherever orders are missing.

## Replay artifacts and performance

**Generate detailed replay** uses the existing asynchronous audit workflow for summary-only native candidates. Completed replay artifacts attach through SSE and are reused. The workstation never reruns a simulation on open. Recorded registered-strategy features and reasons come from the frozen trace. Legacy indicators requiring incompatible sources are flagged, while existing fills and accounting remain available.

The common read-only API is `GET /api/replay/{source}/{resource}`, with a native job ID or `binance` as the source. Resources include `meta`, `watch`, `window`, `navigate`, `executions`, and `lifecycles`. The window is limited to 2,000 candles. Execution pages are limited to 200 records. Most chart calls send only the buffered range around the viewport, and per-symbol tapes and market windows use bounded caches. A window with over 30,000 fills displays a density warning and exposes the remaining fills in the virtual ledger or at a closer zoom. Native artifact reconstruction retains the 200,000-row per-symbol bound.

## Verification

```powershell
.venv/Scripts/python.exe -m pytest -q
npm.cmd --prefix control-ui run build
node control-ui/tests/replay-workstation.mjs http://127.0.0.1:8765 data/research/replay-workstation
```

The browser suite uses H-0001 and the actual Binance archive. It checks rendered transaction-color pixels and round-trips the arrow tip through the chart coordinate converters. It also exercises overlapping candle executions, chronological navigation, position stepping, true replay, ledger virtualization, symbol switching, persisted timeframe and live archive refresh. The fixture tests perturb unobserved intrabar fields independently and check deterministic same-millisecond navigation.

| Windows loopback check | Measured result |
| --- | ---: |
| All 84 BTCUSDT fills traversed both ways | 166 navigation requests, 93 ms median, 169 ms p95 |
| Native true-replay market window | 230 ms, 2.9 KB |
| Binance watchlist, 372 symbols | 797 ms, 42 KB |
| Last-fill seek, ZEREBROUSDT with 2,162 public executions | 102 ms, 14 ledger rows mounted after seeking |
| Synthetic warm index, 200,000 rows | 626 ms to build with 97 MiB RSS increase, 17 ms for 1,000 indexed steps |

The synthetic measurement covers the in-memory index without Parquet decoding, mark reads or browser transfer. Loopback latency includes the HTTP body and the record lookups, with OS cache state uncontrolled.

[Native replay screenshot](backtest-explorer/replay-native.png) · [Binance replay screenshot](backtest-explorer/replay-binance.png)

TradingView Lightweight Charts is a product of TradingView, Inc. [TradingView](https://www.tradingview.com/)
