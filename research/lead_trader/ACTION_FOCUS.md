# How the trader adjusts open inventory

This study covers Binance lead portfolio **5140488422016171264**. The three decision reports are [ADD](ACTION_ADD.md), [REDUCE](ACTION_REDUCE.md) and [REVERSE](ACTION_REVERSE.md). The earlier [inverse decision study](INVERSE_DECISIONS.md) supplies the frozen upstream data and longer-history context.

## Main findings

- **Prior actions carry the strongest repeatable information.** If the preceding action was ADD, the next eligible execution was also ADD 83.6% of the time for longs and 76.3% for shorts. Prior reductions repeated at 83.4% and 81.4%. The individual reports distinguish these execution-conditioned probabilities from the much smaller probabilities of an order arriving in the next minute.
- **Inventory changes what an adverse move is associated with.** After a move at least 25 bp against the position since its last fill, 72 of 123 eligible orders reversed when marked inventory was at most $25. Only 1 of 300 reversed when inventory was at least $250, and 232 reduced. The association reappeared on later dates. Sparse overlap within the same symbol and execution-gap band limits causal or universal interpretation.
- **Two reversal paths coexist.** 213 of 564 complete-position reversals followed at least three consecutive reductions. Another 177 had no previous reduction and retained at least 75% of their prior quantity peak. Only 3 of those 177 closed at least $250, a distinction between abrupt reversal and abrupt liquidation of a large book.
- **The observed prices do not recover the private trigger.** Execution-history and inventory models transfer substantially better than unconditional frequencies. Adding a wide price and market feature block did not improve later-date validation. Price still helps describe how specific fills differ, particularly in combination with inventory.

## Cohorts and baselines

The eligible executions span November 20 at 21:50 UTC through November 28 at 14:03 UTC, 2026. The longer published position history supports reported boundary matching. Execution-sequence estimates use the shorter order archive.

| Exposure before action | Executions | ADD | REDUCE | REVERSE | Symbols | Lifecycles | Five-second matches |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| Long | 3,106 | 1,134 (36.5%) | 1,697 (54.6%) | 275 (8.9%) | 56 | 275 | 1,830 |
| Short | 2,929 | 926 (31.6%) | 1,714 (58.5%) | 289 (9.9%) | 66 | 293 | 1,572 |

There are five additional confirmed standalone closes, excluded from the three-action estimand. The archive audit labels 6,159 order actions confirmed, 23,318 inferred and 458 unknown across 29,935 rows. The predictive panel requires a complete independently reconciled outgoing path and a usable pre-order clock. The reversal audit also keeps 114 exact splits with incomplete outgoing paths and 129 inferred candidates in separate tiers.

Order-time features precede the earlier of reported decision and booking times. Prior inventory comes from strictly earlier booked orders. Orders with an intervening booking between the decision and booking times are excluded. Market features use completed five-second bars plus a 100 ms availability convention. Gaps prevent rolling features and excursion extrema when continuity is needed. Signed returns are favorable to the held position when positive. Unrealized P&L uses the latest observable mark against the weighted reconstructed cost, before fees and funding. Running booked and observable market extrema are recorded separately.

Official public aggregate trades supplied 15 extra symbol-days and 244,558 five-second rows. Aggregates were compared against official checksummed one-minute files and 1,182 suspect minutes were quarantined. Five selected daily files returned 404. Targeted extensions improve overlap in active symbols and do not make the five-second cohort representative of all orders. Prior-only hourly observations provide longer horizons and altcoin-relative measures. The [Binance archive documentation](https://github.com/binance/binance-public-data/blob/master/README.md) defines the underlying file formats.

A second risk set uses one-minute forecasts from minute boundaries inside quantity-reconciled holding paths. There were 120,316 verified HOLD minutes. A terminal action takes precedence in a minute with other fills. Minutes with both ADD and REDUCE and no terminal action (91), and standalone closes (5), stay distinct and are omitted from the four-way timing baseline below. No gaps or unknown starts are interpreted as holds.

| Held side | Eligible minutes | ADD | REDUCE | REVERSE | HOLD | Five-second marks |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Long | 63,169 | 216 | 655 | 261 | 62,037 | 28,909 |
| Short | 59,740 | 322 | 870 | 269 | 58,279 | 21,089 |

## Validation

Values are multiclass log loss, with lower values better. Chronological fits exclude crossing lifecycles from training. Other splits hold out entire symbols or lifecycles. The exports also report equal-cluster scoring and exploratory symbol-cluster 90% intervals. All folds share a short archive, so these tests measure transfer within this record.

| Validation | Long frequency → history/state → + price | Short frequency → history/state → + price |
| --- | --- | --- |
| Later dates | .896 → .416 → .432 | .933 → .581 → .634 |
| Unseen symbols | .932 → .461 → .502 | .909 → .592 → .597 |
| Unseen lifecycles | .917 → .457 → .466 | .908 → .548 → .579 |
| Later dates, five-second matched cohort | history .411 → .442 | history .559 → .584 |

Price-feature comparisons cover momentum and acceleration, displacement from previous executions and cost, volatility, taker flow, BTC-relative and available altcoin-relative returns, and limited derivative observations. The fine-only unseen-symbol short model is a counterexample at the pooled level (.576 → .573), but worsens with equal symbol weight (.582 → .594). Individual cutoffs, grids and comparisons are exploratory. They should be evaluated on a future published order archive before treating them as stable decision rules.

## Evidence and reproduction

Open the [interactive probability atlas](/G:/Quant/data/research/lead-trader/action-focus-2026-11-29/action-atlas.html) for conditional distributions, two-dimensional heatmaps and complete-position Explorer links. Use the [conditional tables](/G:/Quant/data/research/lead-trader/action-focus-2026-11-29/conditional-actions.csv), [fixed price × exposure comparisons](/G:/Quant/data/research/lead-trader/action-focus-2026-11-29/fixed-interactions.csv), [conditional sizing](/G:/Quant/data/research/lead-trader/action-focus-2026-11-29/sizing-conditions.csv), or [full findings](/G:/Quant/data/research/lead-trader/action-focus-2026-11-29/findings.json) for exact denominators, validation, intervals, size quantiles and transitions.

```powershell
$out = 'data/research/lead-trader/action-focus-2026-11-29'
$decisions = 'data/research/lead-trader/decision-study-v2-expanded'
.venv/Scripts/python.exe -m research.lead_trader.action_market $decisions "$out/five-second"
.venv/Scripts/python.exe -m research.lead_trader.action_evidence $out
.venv/Scripts/python.exe -m research.lead_trader.action_evidence $out --audit
.venv/Scripts/python.exe -m research.lead_trader.action_analysis $out
.venv/Scripts/python.exe -m research.lead_trader.action_atlas $out
```

The market step acquires public daily files and can be skipped with the frozen supplement. Use the same archived input directory to reproduce the counts. The [execution features](/G:/Quant/data/research/lead-trader/action-focus-2026-11-29/executions.parquet) and [holding opportunities](/G:/Quant/data/research/lead-trader/action-focus-2026-11-29/minutes.parquet) retain clocks, feature distributions and trajectory identifiers. Study-only supplemental bars remain in the research folder, so some Explorer charts can have limited market coverage even when their execution sequence is complete.
