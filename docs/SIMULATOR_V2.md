# Native order simulator V2

Registered order callbacks use the next-open model and the native OrderSettings defaults. This path shares the C++ book, funding settlement and weighted-average fill accounting. Archived legacy_targets_v1 artifacts retain their original signed-target contract. A frozen candidate whose runtime changed reports replay incompatibility.

| Setting | Default |
| --- | --- |
| Initial wallet and leverage | 10,000 USDT and 1× |
| Order type and position mode | Market and one-way signed base quantity |
| Execution | Immediately following scheduled five-second open |
| Latency | Zero |
| Fee, full quoted spread, additional slippage | 5 bp, 2 bp and 1 bp |
| Impact and volume participation | Disabled |
| Quantity step and opening minimum | 0.000001 base units and 10 USDT requested opening notional |
| Order, symbol, portfolio and concurrent-position caps | Disabled |
| Partial fills, retries and rebalancing | Disabled |
| Historical funding | Enabled with verified timestamp and settlement price |
| Drawdown stop | Disabled |
| Sizing and compounding | Strategy-controlled |

Protocol costs may override supported settings. Delayed orders use latency in multiples of five seconds. Optional participation and impact explicitly use volume. Partial slicing belongs to the legacy execution model.

## Clock, orders and margin

For the bar starting at 12:00:00, the callback observes completed data at 12:00:05. New orders are eligible for the open at 12:00:05. The original archive's 100 ms convention applies to legacy consumers. V2 uses its idealized completed-bar clock. Records use modeled execution time even if processing a bounded block finishes later. Same-time funding settles against the position held before the open. Completed-bar valuations use observable marks. An order is rejected when its scheduled open is missing, quarantined, before listing, empty, or invalid.

BUY and SELL add signed quantity. CLOSE flattens actual quantity, and REDUCE decreases it by the requested fraction. Insertion order determines buying-power priority. Quote-notional requests convert at the open, and rounded quantities fill once. The opening part of a reversal must meet the requested-notional minimum and fit buying power. Reductions and exact closes can finish below the minimum. Buys and sells use open × 1.0002 and open × 0.9998 by default. Fees are 0.0005 times actual fill notional. Modeled spread and slippage are embedded in the prices.

New exposure that exceeds marked buying power rejects the order atomically. Risk-reducing orders remain possible. At a maintenance breach or configured drawdown stop, the simplified policy suspends new exposure and closes the book at usable scheduled opens, explicitly labeled as risk-driven. Exchange-specific lot filters, liquidation priority and liquidity guarantees are outside this model.

## Warm-up and terminal reporting

Warm-up loads available history before the trading range. It may update an optional strategy-state callback and leaves the wallet untouched. Higher-timeframe features use completed UTC buckets. The ready-symbol list reflects consecutive observable history and finite features. At the first day of the archive, readiness develops during the run. The warm-up data identity and effective configuration are pinned for queued runs.

At the end, END_OF_BACKTEST virtually closes remaining positions at the last observable native valuation, with spread, slippage and fees. The report preserves the prior positions and unrealized P&L. Stale valuations are reported. Orders scheduled outside the trading range are rejected. The virtual event uses the terminal clock without reading subsequent market data.

Lifecycle statistics group entries and reductions from flat to flat, split reversals, and allocate fees and funding. Summary and Explorer use the same reconstruction. Daily Sharpe uses marked net equity returns, including idle days, sample volatility, 365-day annualization and a zero risk-free rate. Insufficient or zero-variance samples have undefined Sharpe. The final equity and position statistics include virtual closures. Execution diagnostics distinguish strategy orders, risk fills and virtual closures.
