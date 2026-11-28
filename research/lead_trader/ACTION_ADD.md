# ADD | Increasing exposure

Scope, baselines and validation are in [How the trader adjusts open inventory](ACTION_FOCUS.md). This report classifies an order as ADD only when booked exposure increases in the already held direction.

## When the trader adds

| Held side | Previous execution | Eligible executions | ADD | Baseline ADD | Lift |
| --- | --- | ---: | ---: | ---: | ---: |
| Long | ADD | 1,134 | 948 (83.6%) | 36.5% | 2.29× |
| Long | REDUCE | 1,697 | 99 (5.8%) | 36.5% | 0.16× |
| Short | ADD | 926 | 707 (76.3%) | 31.6% | 2.41× |
| Short | REDUCE | 1,710 | 134 (7.8%) | 31.6% | 0.25× |

The repeat-ADD rate was 478/554 (86.3%) for later-date longs and 348/448 (77.7%) for later-date shorts. This also changes execution timing. In eligible one-minute holding opportunities after ADD, 122/6,187 long minutes (1.97%) and 184/4,982 short minutes (3.69%) produced another ADD. All-minute baselines are 0.342% and 0.539%, yielding 5.77× and 6.85× lift. Most minutes in the elevated group still had no ADD.

Repeat ADD exceeded the local baseline on all nine sampled dates for each side, in 22/24 long and 21/22 short symbols, and in 57/59 long and 54/62 short lifecycles with at least three qualifying observations. Exploratory 90% symbol-cluster intervals for the pooled probabilities were 72.8–87.6% long and 66.6–82.6% short.

Prices show both accumulation into strength and averaging into adverse moves. When the latest observable price was at least 25 bp adverse relative to the prior fill, ADD accounted for 93/403 longs (23.1%) and 108/465 shorts (23.2%). At least 25 bp favorable, it was 124/369 (33.6%) and 179/451 (39.7%). Five-second cohort baselines are 40.4% and 32.3%. The median signed movement since the preceding fill on an ADD was approximately 0 bp for longs and +9.1 bp for shorts. These comparisons leave a path to an adaptive or trend-dependent grid, and weaken unconditional averaging down as the main explanation.

## Clips, scaling and sequences

- Median add size was $9.94 long and $17.22 short. Adds above $500 occurred 20/1,134 times on the long side and 42/926 times on the short side.
- After a previous raw order below $20, ADD exceeded $100 for 21/883 longs (2.4%) and 45/498 shorts (9.0%). After a previous raw order of at least $100, the counts rose to 57/112 (50.9%) and 116/214 (54.2%). This is evidence of persistent clip-size regimes, which could reflect the instrument, allocation or parent decisions.
- In ADD→ADD pairs within five seconds, neighboring sizes matched within 10% on 323/628 longs and 127/361 shorts. Median actual fill spacing was 10.2 and 17.1 bp. With at least five minutes between ADDs the median spacing was 145.8 and 218.3 bp (36 and 50 observations). Fill spacing varies with elapsed time and cannot identify a fixed private price threshold.
- After a recorded reversal opened the held side, a next classified ADD arrived within 60 seconds in 56 incoming longs and 51 incoming shorts. That is one recurring path among many, with reductions and other reversals also observed after a flip.

Long median ADD sizes remained around $6 at both up-to-$25 and at-least-$250 starting inventory (78 and 156 orders). Short medians climbed from $6.02 to $72.33 (73 and 192 orders). The share of short ADDs of at least $100 rose from 4/73 to 76/192 across those bands. This suggests a strong exposure-dependent or symbol-dependent short clip regime, with a weaker effect on the typical long clip. Short upper/lower five-minute volatility bands, defined around each symbol and side's own median, gave 40/182 (22.0%) and 26/241 (10.8%) ADDs of at least $100. The analogous long rates were 13/265 (4.9%) and 12/370 (3.2%). The bands retain symbol and date dependence and cannot separate adaptive sizing from coincident order bursts. The sample, clip distribution and resulting inventory are in the [sizing table](/G:/Quant/data/research/lead-trader/action-focus-2026-11-29/sizing-conditions.csv).

**Interpretation.** Recent action and clip size are the strongest practical cues for accumulation. The alternating stretches of repeated ~10 USDT orders and occasional much larger clips are consistent with clustered execution or changing size targets. Missing parent order IDs and private signals prevent distinguishing those explanations. Fixed clips and adverse-only accumulation each have direct counterexamples. The shared validation found no stable incremental value from the full price, volatility and flow block, so the evidence does not support a universal ADD trigger.

[ADD heatmaps and full action distributions](/G:/Quant/data/research/lead-trader/action-focus-2026-11-29/action-atlas.html?action=add)

## Complete positions in Explorer

- **Long, scale into strength.** $11.00 in the execution, $19.42 of inventory before the order, and +37 bp from the most recent fill. [IDOLUSDT in Explorer](http://127.0.0.1:8765/#explorer?source=binance&symbol=IDOLUSDT&lifecycle=1136496983082110976%3A42&execution=3398a4291616c189004ef9e86b0c5e5427b19ba3611d44aa148b9583e83ebaa1)
- **Long, scale despite adverse movement.** $11.95 in the execution, $67.62 of inventory before the order, and -362 bp from the most recent fill. [LISTAUSDT in Explorer](http://127.0.0.1:8765/#explorer?source=binance&symbol=LISTAUSDT&lifecycle=1149723418575867904%3A3&execution=196edb69433822c28f2ff5adf39c542de6cf6c40b8be764e49fdc158f8a3d2e0)
- **Short, scale into strength.** $29.91 in the execution, $1801.77 of inventory before the order, and +33 bp from the most recent fill. [EVAAUSDT in Explorer](http://127.0.0.1:8765/#explorer?source=binance&symbol=EVAAUSDT&lifecycle=1160034319677886464%3A65&execution=2c33afe3abcda80d2a1d28ac2ca841a0177511183747a6044f86aa8339b010e8)
- **Short, scale despite adverse movement.** $18.23 in the execution, $3582.88 of inventory before the order, and -63 bp from the most recent fill. [EVAAUSDT in Explorer](http://127.0.0.1:8765/#explorer?source=binance&symbol=EVAAUSDT&lifecycle=1160034319677886464%3A79&execution=4831cb8c953912aacbb85ab2eccd54fcfe0a9bc066800207e23c9065dee59ae2)
