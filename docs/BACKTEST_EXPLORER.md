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

External markers preserve the reported transaction side and booked price. Action confidence, signed quantity confidence, and average entry confidence are tracked independently. Unresolved markers use BUY or SELL with executed USDT notional. The inspector displays reported realized P&L separately. An order can realize P&L on the outgoing leg of a reversal, and a closing fill can report zero P&L.

### Public inventory reconstruction

The Explorer associates direction-consistent fills with published position intervals. A unique opposite-side opening and closing boundary can identify a reversal. A reconciled quantity for either leg can allocate the other leg of that same order. Both lifecycle allocations share one marker. Interior fills can be classified when the associated lifecycle is only partially covered.

The inventory line connects signed quantities within collector-certified order windows and stops at gaps, ambiguous fill ordering or the end of available position evidence. Quantity can be reconstructed forward from an opening, backward from a closing, or from an independently reconciled reversal. A rising historical peak between retained partial snapshots can establish a quantity checkpoint when the change in closed volume also reconciles. Public snapshots in this archive provide peak and closed volume, without a direct current quantity. When cost information is sufficiently consistent across independent additions, it can corroborate an inferred starting quantity at the observed precision. The solver rejects inconsistent checkpoints and never carries inferred inventory through an unverified gap.

**Confirmed** means the archived quantities reconcile with independent public evidence. **Inferred** identifies a supported action or state with unresolved assumptions. **Unknown** leaves the value blank. These labels describe evidence within the public archive. Binance does not expose the private account ledger here.

Unrealized P&L uses audited candle closes where available. Dots indicate the latest supported fill-price mark per chart bucket, with the exact selected fill mark in the inspector. The dots remain independent across missing candle intervals. External fees, funding and account equity remain unknown. Cumulative reported P&L sums captured orders, so gaps can make it incomplete. True Replay hides labels and state that depend on position summaries published later.

Click the coverage count beneath the chart for a symbol's confidence levels and failure reasons. Refresh reads the latest collector revision and keeps the active symbol and selected execution. The same counts are available at `GET /api/replay/binance/reconstruction?symbol=BTWUSDT`.

### Replay a complete Binance lifecycle

Use **Positions** and **Complete lifecycles only** to keep closed positions with matched opening and closing orders, complete verified order coverage, and reconciled quantities. **Jump to position opening** selects the original opening order and scopes execution navigation and markers to that lifecycle. A reversal can open one lifecycle and close another with a single order ID. The replay retains the selected lifecycle at that shared boundary. **Clear position replay** returns to the symbol scope.

A row can display **Opening missing**, **Order history gaps**, **Open through report**, or **Unreconciled**. The inspector gives the opening evidence, the first captured fill, and the inventory known at that boundary. When an incomplete position has a known opening, the jump action still works. A pre-existing position with no opening fill begins at its first captured execution and has no opening marker. Lifecycle summaries and completeness filters are available in Analysis mode.

#### BTWUSDT opening-history audit

The frozen archive contained 179 BTWUSDT lifecycle summaries and 805 unique orders. Of the 179, 175 ended before the first captured BTWUSDT order. The four lifecycles with linked fills are below.

| Lifecycle suffix | Side | Reported opening (UTC) | Reported close (UTC) | Earliest captured fill (UTC) | Verified order coverage of report | Starting inventory |
| --- | --- | --- | --- | --- | ---: | --- |
| :53 | Long | Nov 16 07:17:03.996 | Nov 22 21:55:12.896 | Nov 20 21:47:28.691 | 27.3% | Opening unavailable. Quantity before first sale unknown. |
| :54 | Short | Nov 22 21:55:12.896 | Nov 23 07:53:45.396 | Nov 22 21:55:12.896 | 100% | -289 BTW after opening reversal. |
| :55 | Long | Nov 23 07:53:45.396 | Nov 28 10:20:05.547 | Nov 23 07:53:45.396 | 80.2% | +55 BTW after opening reversal. |
| :56 | Short | Nov 28 10:20:05.547 | Open at Nov 28 10:33:22.436 report | Nov 28 10:20:05.546 | 100% through report | Opening reversal matched within one millisecond. Quantity split unknown. |

The first captured :53 fill sold 27 BTW from an existing long. Its reported opening predates the oldest saved order capture and was outside the collector's seven-day public order retention window even at the first archived request. The November 21 order gap falls after that opening. All 176 unmatched openings predate the retention cutoff recorded for the frozen audit. None had an opening that was still within a pending backfill range, missing inside a verified pagination window, or matched to a raw order that the Explorer dropped.

The initial archive check compared 1,255 BTWUSDT records in retained raw order pages with the normalized archive and checked 11 historical exports plus the initial portfolio export. All 771 unique raw page orders and all 535 unique export orders were already in the archive. There were no additional opening orders to import. The six-fill :54 short reconciles as the opening reversal, four reductions, and the closing reversal. The :55 opening is available, and its later order history has gaps. The :56 opening is also available. Its position report was picked up while the collector continued to run.

Generate a CSV containing every reported lifecycle, its timestamps, linked execution counts, starting quantity evidence, and verified intervals:

```powershell
.venv/Scripts/python.exe -m research.lead_trader.reconstruction_audit --lifecycles BTWUSDT --out data/research/lead-trader/btw-lifecycles.csv
node control-ui/tests/public-lifecycles.mjs
```

### Reconstruction audit

A frozen archive snapshot contained 21,791 orders across 246 symbols and 9,993 position summaries. The orders span November 20 through November 28, 2026 UTC. The prior implementation at `c57dbab` required a fully closed lifecycle, unique boundary fills, verified coverage for the entire path, and exact reconciliation to peak and closed volume. This left 18,282 orders without a position action. It also discarded a reversal when the quantity of only one leg was independently available.

| Symbol | Orders | Prior classified | Now classified | Quantity at fill | Inventory time coverage |
| --- | ---: | ---: | ---: | ---: | ---: |
| BTWUSDT | 704 | 4 | 704 | 500 | 51.8% |
| ZEREBROUSDT | 3,396 | 0 | 3,396 | 0 | 0.0% |
| EVAAUSDT | 1,595 | 463 | 1,595 | 944 | 47.7% |
| CCUSDT | 1,327 | 0 | 1,327 | 1,190 | 52.8% |
| FLUXUSDT | 804 | 0 | 804 | 737 | 58.3% |
| ACEUSDT | 557 | 114 | 557 | 536 | 62.9% |
| FLOCKUSDT | 464 | 117 | 464 | 442 | 21.6% |
| All 246 symbols | 21,791 | 3,509 | 21,689 | 10,456 | |

Inventory time coverage divides the length of inventoried segments by the time from the first to the last archived order for the symbol. It may differ substantially from coverage measured per fill. Across the sample, 3,676 actions were confirmed, 18,013 inferred and 102 unknown. Of 11,335 fills with unknown quantity, 7,741 had inconsistent weighted-cost evidence, 2,506 fell outside verified order intervals, 812 lacked an independent checkpoint, 180 lacked a usable position link, 82 conflicted with reported closed volume and 14 had ambiguous same-millisecond ordering.

BTWUSDT had 620 reductions, 82 additions and two reversals. Its 204 unknown quantities came from 113 inconsistent cost observations, 87 fills outside verified order intervals, two missing checkpoints and two same-millisecond fills. On ZEREBROUSDT, 3,104 fills had inconsistent cost checks and 292 were outside verified intervals. On old reconciled paths, only 1,382 of 2,091 sampled reduction and closure records reproduced reported order P&L within the audit tolerance. This rules out treating every reported P&L value as a standalone weighted-entry checkpoint.

To regenerate per-symbol counts from an archive or snapshot, run:

```powershell
.venv/Scripts/python.exe -m research.lead_trader.reconstruction_audit --out data/research/lead-trader/reconstruction.json
node control-ui/tests/public-reconstruction.mjs
```

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
