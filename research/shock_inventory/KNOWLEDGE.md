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
