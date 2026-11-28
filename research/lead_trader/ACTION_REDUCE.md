# REDUCE | Partially closing existing exposure

Scope, baselines and validation are in [How the trader adjusts open inventory](ACTION_FOCUS.md). A REDUCE leaves a nonzero position in the same direction. Full closures and flips are separate outcomes.

## When the trader reduces

| Held side | Condition | REDUCE / group | Rate | Matched baseline | Lift |
| --- | --- | ---: | ---: | ---: | ---: |
| Long | Previous order REDUCE | 1,415 / 1,697 | 83.4% | 54.6% | 1.53× |
| Short | Previous order REDUCE | 1,392 / 1,710 | 81.4% | 58.5% | 1.39× |
| Long | >100 bp adverse to weighted entry | 482 / 668 | 72.2% | 53.0% | 1.36× |
| Short | >100 bp adverse to weighted entry | 404 / 573 | 70.5% | 60.2% | 1.17× |
| Long | >100 bp favorable to weighted entry | 154 / 212 | 72.6% | 53.0% | 1.37× |
| Short | >100 bp favorable to weighted entry | 253 / 405 | 62.5% | 60.2% | 1.04× |

Repeat reductions persisted on later dates, at 695/806 long executions (86.2%) and 538/678 short executions (79.4%). Median pre-order signed mark relative to weighted cost was −97.8 bp long and −70.3 bp short. Reductions were common on either side of cost. A profit-only exit rule cannot explain the tape.

The repeat-reduction rate exceeded each date's baseline on all nine dates for both sides, but in only 29/38 long and 28/45 short symbols with at least three qualifying observations. The exploratory 90% symbol-cluster intervals were 79.4–86.0% long and 77.6–84.3% short. The pooled tendency is stronger than its instrument-level uniformity.

## Size and price interact

Each row conditions on a move at least 25 bp adverse to the position since its previous fill. Counts are ADD / REDUCE / REVERSE. Small is at most $25 marked inventory and large is at least $250.

| Side and inventory | All eligible executions | Later-date executions |
| --- | --- | --- |
| Long, small | 11 / 13 / 39 (63) | 6 / 5 / 11 (22) |
| Long, large | 21 / 69 / 0 (90) | 3 / 17 / 0 (20) |
| Short, small | 12 / 15 / 33 (60) | 6 / 6 / 6 (18) |
| Short, large | 46 / 163 / 1 (210) | 16 / 46 / 1 (63) |

Comparable favorable moves also show more reductions and fewer flips at high exposure. Within-symbol, same-gap overlap was sparse: the adverse comparison had no long cells with at least three eligible observations per inventory band and three short cells, two with more flips in the small band. The pooled interaction is well measured for this archive. Isolating inventory from instrument, age and cadence needs more overlap.

Using verified next-minute opportunities after an adverse prior-fill move, reductions were 3/3,124 (0.10%) small versus 40/1,066 (3.75%) large for longs, and 6/3,873 (0.15%) versus 105/842 (12.47%) for shorts. These cells mix markets and prior execution states. Weighted-cost displacement shows why executed-order and timing conditioning must remain separate. In the fine marked sample, adverse cost displacement elevated the conditional REDUCE share for shorts, yet their minute hazard fell to 183/12,310 (1.49%) versus 441/21,089 (2.09%) across the fine sample. The corresponding long figures rose from 312/28,909 (1.08%) to 150/11,274 (1.33%).

## Distribution policy and counterexamples

The median action closed 7.8% of pre-order long inventory or 8.4% of short inventory. The tenth to ninetieth percentile ranges were 1.4–51.5% and 0.8–57.6%. That spread weakens a single fixed proportional reduction rule. Runs of small cuts support deliberate staged distribution or repeated signals; short and long underwater examples also support risk reduction or a moving target. Some large inventory added after an adverse move, so even the clearest two-variable pattern admits counterexamples. Orders big enough to consume all prior inventory are classified as closes or flips, which mechanically limits the possible REDUCE sizes. Comparing to previous actual clips in the [REVERSE report](ACTION_REVERSE.md) helps quantify this selection effect.

For at-most-$25 inventory, median cuts of about $6 closed 47.8% long and 61.7% short (37 and 35 events). For at-least-$250 inventory, median cuts were larger in dollars, $14.95 long and $17.51 short, and much smaller as fractions, 3.5% and 1.7% (292 and 476 events). This is consistent with order clips scaling less than proportionally to inventory. Conditioning on REDUCE excludes any orders that consumed the position, so the contrast also reflects the geometry of the action category. [Conditional sizing table](/G:/Quant/data/research/lead-trader/action-focus-2026-11-29/sizing-conditions.csv)

[REDUCE heatmaps and full action distributions](/G:/Quant/data/research/lead-trader/action-focus-2026-11-29/action-atlas.html?action=reduce)

## Complete positions in Explorer

- **Long, trim profitable larger inventory.** $18.67 in the execution, $169.05 of inventory before the order, and -17 bp from the most recent fill. [BULLAUSDT in Explorer](http://127.0.0.1:8765/#explorer?source=binance&symbol=BULLAUSDT&lifecycle=1174525816309649408%3A39&execution=56b5a55034327e32fcdfd0de0df0dfee337d3a9043a8a7ebeb41f7642646899d)
- **Long, trim underwater inventory.** $11.76 in the execution, $314.09 of inventory before the order, and +7 bp from the most recent fill. [AINUSDT in Explorer](http://127.0.0.1:8765/#explorer?source=binance&symbol=AINUSDT&lifecycle=1168239248334753792%3A51&execution=ee38717fe531f462d080f99d52371af3ca76510fa91fd6210c84fe6e0789e3f8)
- **Short, trim profitable larger inventory.** $19.92 in the execution, $855.63 of inventory before the order, and -187 bp from the most recent fill. [EVAAUSDT in Explorer](http://127.0.0.1:8765/#explorer?source=binance&symbol=EVAAUSDT&lifecycle=1160034319677886464%3A79&execution=693cb9474a834ebfdc81ed8f6c337b367d8cf31ce025f34880db033eb99139cc)
- **Short, trim underwater inventory.** $10.66 in the execution, $1429.19 of inventory before the order, and -12 bp from the most recent fill. [ELSAUSDT in Explorer](http://127.0.0.1:8765/#explorer?source=binance&symbol=ELSAUSDT&lifecycle=1177567774078767104%3A10&execution=1cfde0b7bd9325855f11f794c226fed4b345185279a7e2cde6cbd4206a396a97)
