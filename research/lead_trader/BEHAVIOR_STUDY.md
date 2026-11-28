# What the public record says about this trader

Portfolio 5140488422016171264. Frozen November 28, 2026. All times are UTC. This study investigates selection, exposure and market behavior. It contains no strategy submissions or simulated trading results.

## Evidence and scope

| Source | Coverage | What it supports |
| --- | --- | --- |
| Public position summaries | 14,157 records from Jul 31 to Nov 28, 2026, including 13,848 closed lifecycles on 368 symbols | Reported boundaries, direction, cumulative reported P&L, peak-quantity proxy, and the relative distribution of winners and losers. |
| Public executed orders | 29,935 unique orders over Nov 20 20:17 to Nov 28 14:14 and 256 symbols | Observed order side, fill price, quantity, size and timestamp. Market orders may contain multiple underlying exchange fills. The verified order intervals exclude expired and unverified windows. |
| Reconciled lifecycles | 569 complete closed lifecycles and another 510 with linked but incomplete execution paths | Complete inventory trajectories in the first group and some bounded inventory states in the second. Verified HOLD observations come from the first group only. |
| Hourly market collection | 750 contracts with some data in May–Nov 2026 | Reported-opening context, point-in-time cross-sectional ranks, comparison markets, and broadly matched forward returns. The active risk set is smaller at any instant. |
| Audited market archive | Frozen Liquid 50 from Nov 25, 2024 through Nov 24, 2026 | Causal five-second inputs for the minute opportunity sample and a longer check of market-only relationships. The Liquid 50 membership was chosen in November 2026. |

The position endpoint was paginated while the advertised count changed, so history completeness remains unverified. The opening record can predate archived individual orders by months. The order archive has a retention gap and smaller failed-verification intervals. The stopped collector was left in its existing state. SHA-256 for the frozen SQLite archive is recorded in the generated manifest. Neither fee or funding decomposition nor account-level equity, concurrent margin, deposits, position intent, or private decision logs are available. Public closing P&L is reported as labeled by Binance.

## The watchlist favors established periods of activity in volatile altcoins

The selection test uses a reported symbol activation after at least seven days without another reported exposure interval. The last fully elapsed hour precedes the event. Available control symbols were inactive according to the public records at the same hour. The narrower peers were within ten percentile points in 24-hour dollar volume. Seven-day left censoring protects against counting the start of the archive as a real start. The median risk set was about 464 markets, with 58 liquidity peers. This process gives 134 starts over 65 UTC days and 132 symbols. It measures selection among observed markets. Private watchlists and omissions in position history can affect the risk set.

| At the first reported opening in an episode | Trader median | Same-hour liquidity peers, median |
| --- | ---: | ---: |
| Market quote turnover in the most recent 24 hours | $24.77m | $22.57m |
| 24-hour turnover rank across available contracts | 87th percentile | Matched within ten percentile points |
| 24-hour realized volatility rank | 94th percentile | 57th percentile |
| 24-hour signed return rank | 91st percentile | 53rd percentile |
| 24-hour absolute return rank | 90th percentile | 58th percentile |
| Last hour quote volume / preceding 24-hour hourly average | 0.89x | 0.72x |
| Last hour trade count / preceding 24-hour hourly average | 0.93x | 0.79x |
| Last hour quote volume / previous hour | 1.10x | 0.91x |
| Seven-day BTC correlation where available | 0.31 | 0.32 |

Fifty activations fell in the top turnover decile, 68 in the top return decile, and 19 in the bottom return decile. Across days, the mean within-peer volatility percentile was 84th and the mean within-peer signed return percentile was 66th. A chronological week-block bootstrap puts the differences from the peer midpoint at +30 to +37 and +10 to +20 percentage points, respectively, in exploratory 90% intervals. Relative turnover was modestly higher than matched peers. Its median below 1x offers little support for a mandatory immediate volume spike. Longer price and volatility context has the stronger relationship.

Of 134 activations, three were within 30 days of the known listing date and four within 90 days. Median indicated listing age was 674 days. This uses a retained exchange metadata snapshot checked against observed bars and may fail on contract relistings. The closed history contains zero BTCUSDT, ETHUSDT, SOLUSDT, or BNBUSDT positions, and 13 XRPUSDT positions. Many selected contracts had materially high dollar turnover. The record supports a preference for active altcoins. Evidence for a recent-listing rule is weak. Historical market cap, token themes and contemporaneous social popularity are unavailable, so a meme-coin or popularity preference is untested.

The 43 observed cessations with seven days of subsequent archive look-ahead had a 42nd percentile median return rank and an 82nd percentile volatility rank. Only 16 episodes have both matched starts and ends. In those, the median start-to-end changes were about -14 percentage points of return rank, -13 points of volatility rank, and -6 points of turnover rank. This is a weak cooling pattern. Cessation requires observing later inactivity, and the ending sample has different symbol composition.

## Alternation is real. It does not identify the signal

Among 13,848 closed summaries, 13,601 (98.2%) have an opposite-side opening within one millisecond. The narrower complete-lifecycle reconstruction confirms at least 678 distinct single-order reversals. The latter count uses reconciled booked quantities and avoids double-counting one reversal as two orders. The broader boundary statistic describes reported timing.

In the 10,762 position openings with hourly overlap, **long and short exposures both mostly began after the underlying contract had risen**. The 5,155 matched long handoffs followed a median +45 bp raw four-hour move and +267 bp over 24 hours. The 5,166 short handoffs followed +25 bp and +179 bp on those same raw horizons. BTC-adjusted 24-hour moves were still positive in raw price terms on both sides. Ordinary opens had the same raw four-hour sign: +28 bp on 262 long openings and +49 bp on 179 short openings. Near-zero signed hourly flow medians give little evidence of a universal flow trigger. These hourly findings can coexist with the preceding study's five-second and one-minute adverse moves. The two studies condition on different frequencies and coverage.

From the first full hourly open after the reported opening, median four-hour market moves in the booked direction were -20 bp for long handoffs and +28 bp for short handoffs. Removing each side's five most sampled symbols left -19 and +26 bp. Non-handoff openings had -10 and +60 bp signed medians, respectively. These are time-based underlying price marks, with a delay from the fill and no simulated exits, sizing, or costs. Correlated observations on the same symbol and reversal cycle prevent interpreting them as independent trades or causal return estimates. The behavior is consistent with both sides using a shared volatile-asset selection layer. The evidence leaves directional triggers and the mechanism causing the close/reopen sequence unresolved.

## Inventory is more responsive to activity than a universal underwater rule

Complete lifecycles have a median first allocated order of $41.38, add of $11.86, and partial reduction of $13.60. The entire order feed has a median $11.96, with a 10th–90th percentile range of $5.57–$186.17. Shared reversals contribute their separate closing and opening quantities in the lifecycle paths, and count only once in the public order distribution.

The inventory opportunity grid compares observable one-minute windows within a certified lifecycle. Market features use audited completed five-second bars and their 100 ms availability delay. Holdings at the sample clock are actual reconciled base quantity and weighted entry. A HOLD label requires continuous verified order coverage and no observed order during that minute. Terminal orders can stop the opportunity window and are labeled separately. Matching compares minutes within the same lifecycle and age band. A sensitivity additionally matches time since the prior fill and reports symbol-equal differences. The execution window overlaps only four UTC dates with audited bars, so the direct behavioral relationships remain tentative.

The final sample contains **41,357 opportunities** on four dates, spanning 126 lifecycles. It includes 40,596 HOLD, 139 ADD-only, 481 REDUCE-only, 124 reversals, one standalone close and 16 mixed windows. Minutes with a terminal order use that outcome even if they contain an earlier adjustment. Timed observations from positions too short to reach the next minute boundary are absent. EVAAUSDT contributed 279 of the 761 non-HOLD minutes. This establishes a denominator and makes symbol concentration visible.

After controlling for time since the prior fill, the equal-symbol changes on busy (above 1.5x activity) versus quiet (below 0.9x) minutes were +0.89 percentage points for long reductions and +2.50 for short reductions. Long and short additions rose by +0.47 and +0.16 points. Long reversal rates rose by +0.57 points and shorts were near zero. Only 5 of 13 long and 5 of 12 short symbols had a strictly positive reduction difference, with several ties. Loss relative to weighted cost and adverse one-minute movement had small, inconsistent cross-symbol relationships.

After the same recency control, reduction rates rose from 2.67% to 23.30% in long lifecycles and 5.12% to 24.80% in short lifecycles when comparing marked inventory under $75 with over $250. Only five long and eight short lifecycles qualify. Larger inventory may coincide with active bursts or allow more partial execution. These are clues about inventory distribution. The private rule and causal trigger are unknown.

## The rare long wins explain most of the reported dollar result

| Closed side | Lifecycles | Positive share | Median reported P&L | Sum reported P&L |
| --- | ---: | ---: | ---: | ---: |
| Long | 6,929 | 46.0% | -$0.08 | +$415,877 |
| Short | 6,919 | 48.2% | -$0.02 | -$4,075 |

The 20 best longs contributed $411,413 of the $411,802 combined total. Removing those winners leaves about +$389 across both sides. This is a sensitivity to profit concentration, using outcome-based exclusions. It does not show that the large winners were predictable or disposable. Closed longs as reported were positive in each full month from August through October and in the observed portion of November. AKEUSDT, IRYSUSDT, BTRUSDT and BEATUSDT together generated $295,770 across both sides in the captured account. A few different markets contributed large losses. Full-sample timing marks and typical exits cannot explain rare long gains in dollar terms without observing time-varying size and mark trajectories.

A matched outcome analysis pairs winners with losers on the same symbol, direction, seven-day UTC block, reported handoff status and sign of prior four-hour return. It requires close prior return, similar hourly activity and a similar volatility rank. Relative to matched losers, median winner peak-value proxies were 0.87x across 521 long pairs (116 symbols) and 0.77x across 517 short pairs (133 symbols). Median winner holding times were 0.81x and 0.84x. Peak value is approximated by published peak base quantity times reported average entry. The outcome pairing is retrospective. Its result challenges the claim that larger exposure or longer holding explained the success of the typical winner.

Among 44 fully reconstructed paths with at least $1,000 peak marked exposure, median initial allocation was $280, median additions were four, median largest addition was $632, and 24 had an addition of at least $500. Eight started with $20 or less. Large exposure has multiple observed pathways. The strongest fully reconstructed winner in this window, an EVAAUSDT short, grew from $43 to about $7,651 with 49 adds and reported +$1,396. Its largest add was $579. A TAGUSDT short grew from $26 to about $5,790 with 25 adds and reported -$1,675. Its largest add was $1,090. These are illustrative complete paths selected to compare both tails. Among the 25 largest winners and 25 largest losses in the long position archive, zero had complete order trajectories and only three had partial links. The archived evidence cannot explain how the biggest historical bets were actually allocated.

## Transfer to markets without using the trader's orders

This section tests observable market moves across all available symbols. One study compares hour-end shocks of at least 2% over four hours, elevated last-hour activity (at least 2x its prior-day rate), and contemporaneous markets with comparable signed and absolute returns, volatility and dollar-volume ranks but ordinary activity (0.8–1.25x). Matching and event timestamps use only completed predictor information. Peers may be reused. Returns run from the following hourly open to the close at the stated horizon. A separate one-hour entry delay checks boundary effects. Sampling clocks move in non-overlapping steps at each horizon. We report medians, equal-UTC-day paired means and week-block 90% exploratory intervals. These are price markouts, with no funding, position state, actual quotes or venue capacity. The V2 default 14 bp round-trip friction is an illustrative hurdle, with no assertion of executability at the hourly open.

### Conditional downside recovery is the clearest transferable finding

Typical four-hour continuation with high activity was negative after both up and down impulses. In the broad sample, the median continuation moves were -37 bp after up impulses (10,152 pairs) and -27 bp after down impulses (7,598 pairs). Their ordinary-activity control medians were -27 and -24 bp. After one hour of delay, the four-hour continuation medians were still negative. The paired mean advantage of fading these moves unconditionally was unstable. Direction and market environment mattered more than the pooled median.

For down impulses when BTC's preceding 24-hour return was negative, the broad sample had **5,859 pairs across 585 contracts and 156 days**. The high-activity contracts rebounded by **37.8 bp more than matched ordinary-activity contracts** on an equal-day mean basis, with a 90% week-block interval of +9.6 to +69.0 bp. The high-activity fade itself averaged **+44.1 bp after a fixed 14 bp round-trip deduction**, with a +21.6 to +67.9 bp interval. A one-hour entry delay left +38.1 bp. The paired advantage helps distinguish a coin-specific activity effect from a contemporaneous common market bounce. Relative BTC beta and liquidity differences remain possible explanations. During BTC-up conditions, the broad fade averaged -6.0 bp after the same deduction.

| Downside sensitivity during BTC-down conditions | Matched pairs | Days | Mean after 14 bp | After 28 bp | Paired gross advantage |
| --- | ---: | ---: | ---: | ---: | ---: |
| Broad universe | 5,859 | 156 | +44.1 bp | +30.1 bp | +37.8 bp |
| Broad excluding the best five days | 5,784 | 151 | +21.8 bp | +7.8 bp | +13.6 bp |
| Broad before September 2026 | 2,665 | 87 | +45.5 bp | +31.5 bp | +47.4 bp |
| Broad from September 2026 | 3,194 | 69 | +42.4 bp | +28.4 bp | +25.7 bp |
| Both symbols outside Liquid 50 | 5,089 | 153 | +27.7 bp | +13.7 bp | +35.1 bp |
| Neither symbol in the trader's history | 870 | 114 | +21.5 bp | +7.5 bp | +7.7 bp |
| Two-year audited Liquid 50 | 71 | 53 | +42.8 bp | +28.8 bp | +58.1 bp |
| Audited Liquid 50 before May 25, 2026 | 38 | 31 | +24.1 bp | +10.1 bp | +47.2 bp |
| Audited Liquid 50 excluding the best five days | 63 | 48 | +5.5 bp | -8.5 bp | +45.6 bp |

The audited Liquid 50 covers 25 calendar months, but simultaneous matches become sparse in 50 symbols. Its full BTC-down subset spans 29 symbols and 53 days. The earlier-date interval after 14 bp includes zero. The matched advantage is also weaker after removing the best broad days and when neither contract appears in the trader archive. Regime splits were examined during exploration. The intervals are unadjusted and the two datasets overlap on recent dates. Treat this as a research lead for distinguishing market-wide downside stress from an isolated sell-off. Spread, gap execution, funding, joint exposure and the cost of repeated inventory turns remain open.

### Strong watchlist selection did not transfer into a simple long signal

In the follow-up, an eligible leader had positive 24-hour return, top-quartile return rank, top-quintile volatility rank, and top-30% turnover. The test compared quiet leaders (at most 1x relative hourly activity) with similarly ranked busy leaders (at least 1.5x) at the same UTC midnight. In the broader archive, 5,093 pairs across 522 case symbols and 206 days had median next-day returns of -161 bp quiet and -247 bp busy. The equal-day mean quiet-minus-busy spread was -1.7 bp with a wide week-block interval spanning zero. In the audited Liquid 50, only 29 pairs qualified and the quiet group's median was lower. A high-volatility watchlist appears useful for finding actively traded markets. These comparisons offer little support for using that selection rule as a stand-alone long entry.

## What to retain and what might change the conclusion

- **High confidence in the observed record.** Frequent small adjustments and genuine reversals coexist with occasional large opening allocations and large additions. Most captured dollar profit comes from infrequent large long wins. The public record cannot identify the historical timing and risk behind the biggest positions.
- **Moderate confidence in the selection relationship.** High recent volatility and positive 24-hour ranks distinguish recorded activations even from same-time, similarly liquid peers. The last completed hour's volume and trade count are secondary. The underlying cohort and listing metadata constrain generalization.
- **Lower confidence in execution triggers.** Opportunity-level evidence favors adjusting in active markets. The few audited days and selection of complete, reversal-heavy lifecycles limit generalization. Size, age, drawdown from peak, order clustering and hidden portfolio constraints remain intertwined. Holding intervals outside verified histories remain unknown.
- **Preserve the previous negative evidence.** H-0410 through H-0416 tested trader-inspired pullbacks, pressure and handoff rules. None met the preregistered promotion criteria. Extra urgency, high turnover and low gross edge mattered under modeled costs. The full-history V2 persistence results also warn against assuming a trend effect transfers across dates or sides. See the [earlier trader study](KNOWLEDGE.md), [V2 findings](../v2/KNOWLEDGE.md) and [shock journal](../shock_inventory/KNOWLEDGE.md).
- **Most informative next measurements.** Separate watchlist selection from entry predictions. Check longer holdings without repeated crossing, paired directional effects under adverse and favorable market conditions, and whether opportunistic trimming correlates with shrinking demand or just higher traded volume. A longer verified order sample and independent order-book/funding information would change the sizing and economic inference most. Historical category labels and listings would enable a direct test of token themes and popularity. Retain signs and uncertainty if follow-up results disagree.

## Reproduce

Run from the repository root. The study uses only local files and leaves the collector and trading registry untouched.

```powershell
$out = 'data/research/lead-trader/behavior-2026-11-28'
.venv/Scripts/python.exe -m research.lead_trader.behavior_study $out
.venv/Scripts/python.exe -m research.lead_trader.behavior_audited $out
.venv/Scripts/python.exe -m research.lead_trader.behavior_report $out
.venv/Scripts/python.exe -m pytest -q tests/test_lead_behavior.py
```

Use a new output folder to study an updated archive, or pass a saved SQLite snapshot with `--archive`. `--inventory-only` and `--market-only` rebuild one half of an existing run. `--reuse-hours` avoids rescanning the audited five-second market archive. Scripts, fixed filters and method are versioned. Raw pages, frozen SQLite, typed Parquet evidence, CSVs, SVG plots and numerical summaries stay in the local output folder. Read `archive-summary.json` and `findings.json` for counts, uncertainty and per-side details. `matched-wins-losses.csv`, `pnl-tails.csv` and `position-pnl-by-month.csv` retain the outcome comparisons. The market CSVs retain monthly results. Inventory and market Parquet tables allow further checks without recapturing public records.
