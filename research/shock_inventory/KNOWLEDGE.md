# Shock inventory research journal

The dashboards expose live queue status and durable results. Hypothesis numbers are local to their instance. [Original six-month dashboard](http://127.0.0.1:8791/) | [Patient and diagnostic dashboard](http://127.0.0.1:8765/)

## Round 1 diagnostic

The fixed two-day check used the full Liquid 50 on [2026-08-14, 2026-08-16) UTC. It carried actual filled quantities, 10 USDT opening orders, a 30-second decision cadence and default V2 next-open costs. The six-month campaign C-6f39f3aed61e0c37 is separate at port 8791, H-0007 through H-0010. Queued receipt took 434 ms.

| Diagnostic policy | Job | Gross P&L USDT | Net P&L USDT | Fills/day | Lifecycles/day | Fills/lifecycle |
| --- | --- | ---: | ---: | ---: | ---: | ---: |
| Accelerating decline | 571a94c92d134c6e8e0780cf83a7d084 | -2.30 | -5.48 | 213.5 | 87 | 2.45 |
| Coin-specific decline | 42ef06642ce34b5ba2d1ea08435a5f70 | -2.65 | -6.02 | 222.5 | 87 | 2.56 |
| Aggressive sell pressure | 51fda8445108430292b9cd8cb3ca5dae | -1.62 | -3.67 | 138 | 57 | 2.42 |
| Coin-specific short | 485af4ce80c04732887aba2d0a703a50 | -2.90 | -7.16 | 259 | 101 | 2.56 |

All four median holds were two minutes. The diagnostics recorded 10–38 empty-open rejections per candidate and no buying-power rejections. Fees and modeled friction deepened losses that were already present in gross P&L. The short-side policy lost more in this window. These are two-day observations with no inference about the sign of the six-month results.

Exact replays H-0007 and H-0008 **on port 8765** attach to the two-day price and flow jobs above. Across both audits, median entries and exits were one each, the largest entry count was four, the largest exit count was three, and median booked fill size was 10.002 USDT. The longest holding time was 39 minutes and the largest fill-sampled inventory for one lifecycle was 39.67 USDT. Average hourly sampled gross utilization was 0.037% for the price policy and 0.023% for flow. This small implementation did not generate the intended inventory depth.

The fill-level losses were concentrated in adverse stops. The price audit lost 9.03 USDT and the flow audit lost 5.94 USDT from their losing stop-out lifecycles before offsetting gains from other positions. The price audit's worst lifecycle, INJUSDT-1, accumulated three entries and lost 0.819 USDT at the stop after 90 seconds. The median profitable price lifecycle, ONEUSDT-1, earned just 0.0293 USDT from one entry and one recovery exit. The flow policy's worst lifecycle, BTWUSDT-6, made four entries and two exits before closing down 0.715 USDT. This evidence implicates persistent moves and repeated small losses rather than a buying-power limit. Neither the flow condition nor relative return alone separated those moves reliably in the two-day check. The four representative production-browser timelines are saved under data/research/shock-inventory/diagnostic-audit. [Profitable ONEUSDT lifecycle](http://127.0.0.1:8765/#explorer?hypothesis=H-0003&candidate=571a94c92d134c6e8e0780cf83a7d084&symbol=ONEUSDT&position=ONEUSDT-1&from=1786667165000&to=1786667885000) | [Three-entry INJUSDT stop-out](http://127.0.0.1:8765/#explorer?hypothesis=H-0003&candidate=571a94c92d134c6e8e0780cf83a7d084&symbol=INJUSDT&position=INJUSDT-1&from=1786718975000&to=1786719665000)

## Round 2 design

The patient version freezes the fair-value anchor for exit, asks for a wider initial gap and a lagged range reference, and requires at least eight minutes before ordinary distribution. Fair-value and adverse-risk exits can still occur sooner. It tapers adds after the distance from the initial anchor reaches 1.9 times the initial shock hurdle, stops adding after 45 minutes, and bases subsequent spacing on booked additions and reductions. It uses a one-minute decision cadence and a six-hour maximum hold. One variant uses dynamic 10–20 USDT entry orders only when current shock severity supports them. The flow variant requires an isolated decline, abnormal aggressive selling and deceleration while price is still moving down. A separate short variant uses isolated upside shocks. Round two is a new version, preserving the original trial's exact replay.

Results and exact output paths are tracked under data/research/shock-inventory. The [protocol](PROTOCOL.md) records data and execution limitations. The [V2 knowledge base](../v2/KNOWLEDGE.md) describes earlier independent investigations and model caveats.

### Evidence from the second diagnostic

The patient six-month wave is H-0012 through H-0014 on port 8765 and its durable submission took 749 ms. The same two-day diagnostic independently finished as follows.

| Diagnostic policy | Job | Gross P&L USDT | Net P&L USDT | Fills/day | Lifecycles/day | Median hold |
| --- | --- | ---: | ---: | ---: | ---: | ---: |
| Patient absolute repricing | 433c35f7d17d45a4a1c364b9c47b4c68 | +1.94 | +0.80 | 102.5 | 39.0 | 11.5 min |
| Patient idiosyncratic flow | 4c51a11151bf4dc4af4cde3bd4ce7054 | +0.96 | +0.77 | 12.5 | 4.0 | 12 min |
| Patient isolated short | 6fa2198f7a5f4053af1d92d79861eb76 | +1.17 | -0.13 | 97.0 | 40.5 | 9 min |

The direction of gross improvement is encouraging across the three policies. The short still lost money after costs. The flow variant made eight positions in total, used at most 26.44 USDT gross at once, and averaged 3.13 fills per lifecycle. The price variant averaged 2.63 fills per lifecycle. The evidence remains weak on capital utilization and on the intended large number of entries and exits in one position. The short-window Sharpe is too unstable to interpret. These paired windows reuse the same dates, and the strategy changes were informed by the first window.

Two exact patient diagnostic replays were accepted as H-0015 and H-0016. The separate five-second check H-0017 and H-0018 varies only decision cadence from the two patient long policies. It tests whether faster observation actually increases economically justified inventory steps while preserving the same shock, pricing and risk rules. The answers are diagnostic evidence, independent of full-history promotion decisions. H-0017 and H-0018 were accepted in 317 ms.

## Six-month outcomes

All seven candidates used [2026-05-25, 2026-11-25) UTC and the full Liquid 50. Positions were sized by the strategy under the 10,000 USDT V2 wallet. None earned positive net P&L, so neither round advanced to two years.

| Policy | Registry / hypothesis | Gross USDT | Net USDT | Daily Sharpe | Fills/day | Lifecycles/day |
| --- | --- | ---: | ---: | ---: | ---: | ---: |
| Accelerating decline | 8791 / H-0007 | +47.99 | -411.19 | -10.66 | 336.5 | 135.2 |
| Coin-specific decline | 8791 / H-0008 | +48.88 | -263.92 | -15.99 | 227.3 | 90.3 |
| Aggressive sell burst | 8791 / H-0009 | +25.83 | -257.76 | -9.08 | 209.4 | 87.4 |
| Coin-specific short | 8791 / H-0010 | +69.63 | -356.23 | -18.79 | 291.8 | 117.5 |
| Patient absolute repricing | 8765 / H-0012 | +53.75 | -79.51 | -3.11 | 129.1 | 49.7 |
| Patient idiosyncratic flow | 8765 / H-0013 | -11.02 | -24.05 | -4.30 | 8.3 | 3.0 |
| Patient isolated short | 8765 / H-0014 | -55.64 | -200.55 | -11.50 | 115.3 | 51.1 |

All four first-round gross gains were overwhelmed by trading friction. The price patient variant reduced churn and still spent more on execution than it earned gross. The patient flow policy reduced the fill count sharply, but lost even before costs. The patient short's deterioration suggests the recovery filter needs independent treatment for each direction. The only recorded rejection category was empty next opens. The position count stayed much closer to the fill count than the intended inventory behavior requires. Raw summaries and experiment identifiers are in the initial and patient evidence JSON files.

The separate every-five-second diagnostic produced -0.90 USDT net for the patient price policy and +2.57 USDT net for patient flow on the two-day window. Price fills increased from 102.5 to 217 per day and flow fills from 12.5 to 48. Flow fills per lifecycle were essentially unchanged at about 3.1, so faster observation mainly increased the number of positions. The positive two-day flow result remains weak evidence. The relevant follow-up is a native six-month test at five seconds using the same flow parameters and the optimized execution path.

Confidence is strong in the measured failures of the tested policies and low that these results generalize to all shock mean reversion. The two-day improvements did not carry over to six months at the slower clock. Observed adverse shocks can be driven by persistent price news, macro risk or temporary liquidity demand. The current data cannot separate those sources cleanly.

The unchanged five-second flow follow-up was accepted as H-0035 on port 8765. Its source diagnostic is H-0018, job acdf5487bdc54731a328853318180618. It runs on the optimized V2 runtime with the same strategy version and parameters.

The five-second flow follow-up completed as job 26b36884b1394e0b8154bb96c8d101eb. It returned -0.62% net (-62.00 USDT), with daily Sharpe -5.84. Gross P&L was 6.87 USDT. There were 8,142 filled strategy orders across 2,719 lifecycles (44.2 fills and 14.8 lifecycles per day). Mean booked order value was 12.04 USDT. Faster observation improved gross P&L relative to the one-minute flow candidate, while extra trading friction made net P&L worse. No two-year promotion. This result supports testing a more selective inventory policy with a wider expected recovery before adding, while retaining adverse-price and flow conditions. It does not establish a general result about shock mean reversion.

## Sharpe 2 search

The owner set net daily Sharpe 2.0 as the target. The campaign requires positive net ROI at screening before an unchanged two-year run. Results must satisfy both conditions again over the full archive. Screening uses all Liquid 50 over [2026-05-25, 2026-11-25) UTC, the 10,000 USDT wallet, 10 USDT opening orders and base V2 costs. Both windows are research evidence. Late universe selection, small booked exposure, and winner concentration remain material concerns.

The first wave tested 16 prespecified variants of `shock_inventory_recovery` 2.0.0 across pressure, isolated price declines, decelerating flow, and upside shocks. Two produced positive net P&L. None qualified for promotion.

| Screening leader | Registry | Job | Net USDT | Net daily Sharpe | Lifecycles |
| --- | --- | --- | ---: | ---: | ---: |
| Extreme decelerating flow | H-0057 | c6984bab66d640ca9acd2fdbdc3f5702 | +0.89 | 0.787 | 11 |
| Extreme isolated displacement | H-0053 | 16239a3a16054a73844b3a153cac3e2c | +2.94 | 0.278 | 770 |

The isolated policy earned 13.81 USDT before execution costs, which consumed about 79% of gross gains. AKEUSDT and AINUSDT contributed 12.84 USDT together, exceeding total net P&L. Three calendar months lost money and November lost 5.87 USDT. Its median hold was 10 minutes and it averaged 2.27 fills per lifecycle. It recorded seven empty-open rejections. The flow leader's peak gross exposure was 10.89 USDT and two calendar months had zero P&L. Confidence in either edge is low. The four isolated upside tests all lost after costs. Full evidence is in `data/research/shock-inventory/sharpe-wide/evidence.json`.

Two follow-up waves test different explanations. H-0064 through H-0072 use `shock_liquidity_recovery` 1.0.0, separating isolated and market-wide pressure with a pre-shock reference frozen when the seed is submitted. They vary the size and horizon of the dislocation separately from inventory adds and recovery reductions. H-0073 through H-0078 test wider recovery targets, quiet markets, restricted adding, longer holds, extreme tails and relaxed flow selectivity around the isolated candidate. All specifications and receipts are saved under `data/research/shock-inventory/sharpe-frozen` and `data/research/shock-inventory/sharpe-isolated`.

The market-wide downside / half recovery candidate H-0067 screened at **2.033 net daily Sharpe**, +0.137% net ROI (+13.72 USDT), 0.085% maximum drawdown and 289 completed positions. It averaged 4.99 fills and 1.57 position lifecycles per day, with a 64-minute median hold, a 222 USDT peak gross exposure, and three rejections on empty opens. Decision cadence was four minutes. It opens with 10 USDT on a ten-minute fall of at least 2.6%, a pre-shock mean discount of at least 3%, a market-median fall of at least 0.4%, and historical turnover and prior-trend filters. It adds during further adverse price moves, reduces during recovery, and closes at the frozen reference, invalidation or expiry. See job `bee7dcaf345b4081b53d804840ea15ab`.

Results are sensitive to the policy. The two other market-wide variants lost money, and the best isolated variant H-0065 earned more (+18.18 USDT) with lower Sharpe (0.939). October and November supplied 12.55 of the winner's 13.72 USDT. This candidate clears the initial hurdle at low exposure with substantial temporal concentration. Its unchanged full-archive run is H-0079 (C-3edada2ee0f421c1).

All six focused isolated follow-ups earned positive net P&L and fell short of 2.0. The 7% downside tail H-0077 (job `e67d8c3d89354477b85e13c9ec14babb`) attained 1.696 and +15.34 USDT across 246 lifecycles. H-0075 capped inventory at 10 USDT, reached 1.632 and +22.23 USDT across 886 lifecycles, and contradicted the premise that additional adverse exposure is necessary for this particular signal. Both drew most of their gains from a few months, and both lost money in May and August. Three variants H-0081 through H-0083 preregister one-add, slow recovery, and seed-only policies around the 5–6% dislocation range. They use the same cost, universe and six-month settings.

H-0080 repeats H-0067's parameters on [2026-11-03, 2026-11-12) to inspect opposite outcomes within one short audit window. The source six-month daily series lost 2.03 USDT on November 4 and gained 2.28 USDT on November 10. The diagnostic starts with empty inventory and can differ from a slice of the longer experiment due to preexisting holdings.

A directional-context experiment tests whether already observable 24- or 48-hour gains distinguish temporary markdowns from genuine repricing. `shock_liquidity_recovery` 2.0.0 calculates its additional filter from completed hourly data lagged one further hour. It gates new positions while held positions retain the existing explicit-order policy. Four six-month configurations were accepted by the V2 registry on port 8791 as H-0013 through H-0016 in C-8bbf6e55d0cdb1e3. This registry has a separate hypothesis namespace. The four CPU slots for this wave leave 12 configured slots for the concurrent full-archive run on port 8765.
