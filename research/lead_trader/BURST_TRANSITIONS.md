# What starts and changes an execution burst

This study follows [ACTION_FOCUS.md](ACTION_FOCUS.md), [ADD](ACTION_ADD.md), [REDUCE](ACTION_REDUCE.md) and [REVERSE](ACTION_REVERSE.md). It uses the same frozen Binance order and position records for portfolio 5140488422016171264 and the existing audited and supplemental five-second market bars. It covers November 20–28, 2026 UTC. The question is how current inventory and market conditions relate to the trader's next signed order.

## Main results

1. **A short adverse move is the clearest market clue for a first long ADD.** A fall of at least 10 bp over 15 seconds lifted the observed probability to 2.89 times baseline and 1.65 times the rate after an equivalent rise. The adverse association survives a same-symbol matched comparison. Short ADD timing rose after moves in either direction. Within the subset of executed short ADDs, adverse movement distinguished first clips from continuations. On the clock it did not identify which direction of move would lead to an ADD.
2. **The first REDUCE is associated with movement in either direction.** Both favorable and adverse 15-second moves increased the raw next-minute rate. After matching, the signed move had little consistent direction at reduction starts. Being further underwater had some support for reductions and for choosing a BUY over a renewed SELL after a pause in an existing short. The broader market model failed to transfer across symbols for that switch.
3. **Clip size often resets after a pause, and sometimes changes abruptly inside a run.** After a sub-$20 order of the same transaction side, jumps to $100 or more were more frequent after a pause. Market features showed no consistent improvement beyond existing exposure and timing on held-out markets.
4. **A reversal is largely a question of what remains when an opposing order arrives.** Held-out opposing clip distributions projected against current inventory improved reversal discrimination substantially over a frequency baseline. They still materially overpredicted reversals, so the results leave room for sizing that adapts to inventory and for distinct opposing-order regimes.

## How bursts and decision opportunities are defined

Same raw BUY or SELL orders form a candidate burst when consecutive bookings in the same confirmed lifecycle are at most the chosen gap apart. An opposing order or exceeding the gap starts a new burst. The primary gap is 30 seconds. The opening booking is an anchor, so the first ADD of a position can already be a continuation of the opening-side burst. A reversal is counted once on the outgoing side; it can anchor the incoming lifecycle. A lifecycle edge cuts the grouping, and no public order ID identifies any private parent order.

The study joins **6,040** pre-inventory orders to the raw signed tape, including five standalone closes. It uses three distinct risk sets.

- Executed adjustment. Compare first vs continuing ADD or REDUCE, or compare raw opposing vs renewed held-side orders after a common pause of **more than 30 seconds**. Market features are sampled before the earlier of recorded decision and booking. The signed return is favorable to the held position when positive.
- One-minute start hazard. At UTC minute boundaries inside verified holding intervals, label the first booked order in the next minute as a fresh burst, a continuation or no order. The specific ADD and REDUCE tables compare starts of that type with verified quiet minutes. Other action outcomes stay separate. A row is excluded if the next recorded decision predates the landmark. Hazard rates therefore apply to the stated two-outcome risk sets.
- End-or-continue landmark. When five seconds have passed since the latest fill without an intervening decision, look for the next booking before 30 seconds from the previous fill. The first booking is same-side, opposing, or quiet through the deadline. The latter is temporary silence, with later decisions still possible. Orders arriving in the first five seconds lie outside this landmark panel.

There are **126,635** combined minute and post-fill observations before market-coverage and outcome filters. Same-symbol matching also requires the same UTC day, held side and previous raw order side, with pre-order marked inventory plus $1 within a factor of two and seconds since the last order plus one within a factor of three. Controls are matched without replacement within each panel. The matched comparisons are exploratory, and pairs can come from the same lifecycle. Each analysis uses completed five-second bars plus the established 100 ms availability rule. Later-date validation removes shared training lifecycles, and symbol validation holds out entire symbols. The archive supplies only nine execution dates and covers some traded symbols unevenly.

## 1. First ADD and first REDUCE

The 15-second signal table uses the same marked denominator for both tails in each row. All counts are action starts in the next minute.

| Action and held side | Baseline | ≥10 bp adverse | ≥10 bp favorable |
| --- | ---: | ---: | ---: |
| First long ADD | 103/28,475 (0.36%) | **45/4,303 (1.05%)** | 28/4,413 (0.63%) |
| First short ADD | 173/20,534 (0.84%) | 61/3,915 (1.56%) | **62/3,836 (1.62%)** |
| First long REDUCE | 306/28,678 (1.07%) | 97/4,355 (2.23%) | 96/4,481 (2.14%) |
| First short REDUCE | 431/20,792 (2.07%) | 141/3,995 (3.53%) | 152/3,926 (3.87%) |

For the long ADD, the raw adverse 15-second lift was **2.89×**. The later marked sample gave 10/448 (2.23%) versus 18/2,080 (0.87%). Across 88 same-symbol pairs, the median case-minus-control signed move was **−4.1 bp** and 51 pairs had a more adverse case move, versus 36 in the other direction and one tie. Later it was −11.7 bp, with 10 of 13 pairs more adverse. The one-minute signed move in 84 eligible pairs was −22.4 bp at the median, and 11 of 13 symbols had a negative median paired difference. The short ADD had a **+5.0 bp** matched 15-second difference (157 pairs), consistent with the lack of an adverse-only hazard on that side.

Among actual ADD executions, both sides began more often on an adverse 15-second move. At the 30-second grouping gap the long proportions were 66/130 firsts versus 142/598 continuations, and the short proportions were 94/181 versus 116/321. The direction of this contrast persisted at 5, 15, 60 and 120 seconds. A persistent buy or sell signal aligned with recent price direction cannot on its own explain this entry-to-run contrast. First-clip conditioning differs from timing on the clock.

For REDUCE, both price tails were active. The median matched signed 15-second return difference was effectively zero on longs and +0.3 bp on shorts. The matched cost displacement was −7.0 bp for long reduction starts (274 pairs) and −6.7 bp for short starts (404 pairs). In the later subset it was −23.3 bp long and −12.9 bp short. On the other hand, among executed short REDUCEs at the 30-second gap, 169/488 first clips were more than 100 bp in profit versus 84/459 continuations. Thus a cost-pressure tendency on the clock can coexist with profit-taking among individual started runs.

## 2. When accumulation ends or the next order changes side

After a pause longer than 30 seconds following an order aligned with the held position, a 10 bp favorable 15-second move preceded an opposing order in **48/62** marked long executions (77%, baseline **113/178** or 63%) and **60/90** short executions (67%, baseline **134/246** or 54%). A favorable short-horizon move accompanies some sales into a rising long or buys into a falling short. A different clue is the cost basis. Shorts over 100 bp underwater switched in **67/95** executions (71%, baseline **137/249** or 55%), including 26/36 later versus 38/78 overall in the later marked sample. In 39 same-symbol pairs, the switched short was **182 bp** further underwater at the median, with 29 negative versus 10 positive differences. The later 10 pairs had a median gap of −214 bp.

The immediate post-ADD sample tells a different story. Among marked five-second landmarks with either an opposing booking by the 30-second deadline or silence through that deadline, opposing bookings occurred in **39/150 long** and **23/201 short** observations. After a 15-second adverse move, the figures were **25/94** (27%) and **9/106** (8.5%). The corresponding overall rates were 26% and 11%. An adverse move offers little evidence of an immediate stop-and-switch rule at this resolution.

For continuation rather than direction change, marked short landmarks had higher five-minute volatility before another same-side booking than before silence in **103/179** matched comparisons (76 lower), with a median difference of **+5.1 bp**. The later subset had 20/27 higher and a +64.2 bp median difference, concentrated in four symbols. The model with market features improved chronological log loss over state for that short landmark (.636 to .613), but worsened unseen-symbol loss (.613 to .655). This supports a volatility association in sampled runs. It does not establish a general continuation trigger.

## 3. Which clip sizes initiate a larger allocation?

Among same-direction follow-up orders after a clip below $20, a jump to at least $100 occurred more often after a gap.

| Held side | Within the 30-second run | After more than 30 seconds |
| --- | ---: | ---: |
| Long | 32/1,521 (2.1%) | 67/399 (16.8%) |
| Short | 58/812 (7.1%) | 66/495 (13.3%) |

These figures cover BUYs and SELLs independently of the ADD/REDUCE accounting labels. In marked matches, 11/16 long big-clip events had a more adverse one-minute move than their control, with a −26.7 bp median case difference. Shorts had a +64.6 bp median difference from weighted cost (26 positive and 12 negative among 38 pairs); the later five pairs had four negative differences. Those small and inconsistent matched samples do not establish a price rule for changing size. In model validation the addition of market features to state made held-out-symbol loss worse for both sides, from .113 to .132 long and .256 to .275 short.

The gap changes the count of first orders substantially, although it cannot change actual BUY/SELL switches.

| Gap | Long ADD starts | Short ADD starts | Long REDUCE starts | Short REDUCE starts |
| --- | ---: | ---: | ---: | ---: |
| 5 s | 474 | 537 | 1,002 | 1,233 |
| 15 s | 293 | 401 | 824 | 1,074 |
| 30 s | 240 | 343 | 761 | 980 |
| 60 s | 203 | 289 | 692 | 877 |
| 120 s | 184 | 237 | 605 | 725 |

At 30 seconds, median absolute fill-price spacing was 127/174 bp on first long/short ADDs and 11/22 bp on continuations. However, **51/894** long and **27/583** short ADD continuations executed at effectively the same reported price as the previous fill (within 0.01 bp). That weakens a fixed nonzero spacing requirement for every clip. Time-varying levels or market orders following repeated signals could still produce these observations.

## 4. How much can signed order size explain?

Inventory accounting agrees with the raw signed executions on **6,040/6,040** rows. A same-sign order adds. An opposing order smaller than the prior base inventory reduces, equal closes, and larger reverses. The test uses whole reported order quantity, including both legs of a reversal, with relative tolerance 1e-8 for equality. This verifies the decomposition; observed order size is only known after execution.

Even among within-burst opposing clips, small residual exposure often makes the next clip a reversal. At ≤$25, continuation clips flipped **35/52** outgoing longs and **27/49** outgoing shorts. At ≥$250 none of **173 long or 210 short** opposing continuations flipped. First opposing clips showed the same contrast, with **41/61** and **46/60** flips at low inventory, versus **0/119** and **1/267** at high inventory.

To avoid using the current order's actual size as a predictor, draw opposing sizes from a disjoint training fold, express them at their own pre-order marks, then compare that empirical clip distribution with the held-out inventory at its pre-order mark. Compare against a constant trained reversal frequency by side. The later-date score uses 356 long and 337 short opposing executions. The clip projection improved **.329 → .198** long and **.322 → .208** short in log loss. Conditioning the training clip distribution on whether the previous order was below $20 improved results to **.169/.187**. The test still projected about **55 long and 53 short** reversals versus **36 and 33** observed. All six side-by-symbol fold comparisons improved over frequency. The independent-clip model therefore explains substantial mechanical variation, but overpredicts across the later sample. Rejection of that simplified calibration leaves inventory-dependent sizing, instrument variation and regime shifts as alternatives.

## 5. What transfers and which explanations survive?

Selected later-date / unseen-symbol log losses on marked decision cohorts. Arrows compare prior inventory and history with the same model plus market features.

| Decision test | Later-date | Unseen symbols |
| --- | ---: | ---: |
| First long ADD vs quiet | .0411 → .0425 | .0191 → .0198 |
| First short ADD vs quiet | .1678 → .1763 | .0373 → .0372 |
| First long REDUCE vs quiet | .1194 → .1249 | .0446 → .0451 |
| First short REDUCE vs quiet | .2020 → .2045 | .0738 → .0758 |
| Long opposing order vs same-side restart | .6734 → .6933 | .6214 → .7313 |
| Short opposing order vs same-side restart | .6548 → .6191 | .8127 → .7986 |
| Short reversal vs reduction | .1530 → .1727 | .1588 → .1597 |

For the short restart comparison, the unseen-symbol combined score was slightly worse than its frequency baseline (.7965). The model's chronology improvement and the matched cost effect are enough to keep underwater-short switching as a lead. Independent symbol transfer remains weak. Exact sample counts, prevalence, equal-symbol log loss and AUC are in the validation export.

- **Persistent directional signals or parent allocations** can produce dense same-direction runs and small consecutive clips. The gap sensitivity and sizeable within-run clip changes prevent a clean identification of private parent orders.
- **Dynamic inventory or inventory-dependent risk management** fits the exposure dependence of opposing outcomes and some underwater switching. Clip projection explains part of the pattern without either private policy. Its calibration error and the rapid $16-to-$174 example leave sizing state as a relevant alternative.
- **Independent BUY and SELL triggers** remain plausible because an opposing order can recur in its own run and simply cross the residual quantity. Timestamped market data alone cannot distinguish separate private triggers from one rule issuing signed orders.
- **Price grids** may explain some recurring clips, but both directions trade through repeated prices and across widely different gaps. A strict fixed-spacing or adverse-only rule has direct counterexamples.
- **Transferable private trigger remains unidentified.** More independently covered symbol-days would help distinguish the long ADD adverse-move lead from high-activity sampling and test whether short switches respond to inventory losses. Pre-order book or parent-order data would better distinguish intent from repeated execution if such data becomes public.

## Complete positions in Explorer

- AKEUSDT long switched from a $28.47 buy to a $5.64 sell after a 134 bp adverse move since the last order and a 222-second pause. [AKEUSDT reduction](http://127.0.0.1:8765/#explorer?source=binance&symbol=AKEUSDT&lifecycle=1125658646960246784%3A389&execution=5b26b456ff91f61c819a9bf49a8d5c44f5d3be7367906a8ce31680afb8ba439e)
- BULLAUSDT long provides the opposite sign. It switched from buying to a $7.77 sell after a 280 bp favorable move since the last order. [BULLAUSDT reduction](http://127.0.0.1:8765/#explorer?source=binance&symbol=BULLAUSDT&lifecycle=1174525816309649408%3A17&execution=a2bc5ceda3db0a778c686af37518ed970bc76e210aa73406a45233b1c818d2b5)
- EVAAUSDT short bought $16.32 and then $173.61 about eight seconds later, both reducing the short. This directly contradicts a fixed size within every burst. [EVAAUSDT clip jump](http://127.0.0.1:8765/#explorer?source=binance&symbol=EVAAUSDT&lifecycle=1160034319677886464%3A77&execution=b50808ff0f61b270d447717b4af6240539ccdb44029ac6f63b7cc2367e21aa4d)

The [example catalog](/G:/Quant/data/research/lead-trader/burst-study-2026-11-29/examples.json) includes six links, and [the adjustment sequences](/G:/Quant/data/research/lead-trader/burst-study-2026-11-29/example-adjustment-sequences.csv) retain order-by-order size, price, state and timing for those lifecycles. The Explorer includes the opening fill. Some study-only supplemental bars are unavailable in the Explorer.

## Evidence and reproduction

[Candidate bursts at 30 seconds](/G:/Quant/data/research/lead-trader/burst-study-2026-11-29/execution-bursts-30s.csv) · [Grouping sensitivity](/G:/Quant/data/research/lead-trader/burst-study-2026-11-29/burst-sensitivity.csv) · [Market sensitivity by grouping gap](/G:/Quant/data/research/lead-trader/burst-study-2026-11-29/burst-market-sensitivity.csv) · [Conditional action rates](/G:/Quant/data/research/lead-trader/burst-study-2026-11-29/market-conditions.csv) · [Same-symbol matched comparisons](/G:/Quant/data/research/lead-trader/burst-study-2026-11-29/market-matched.csv) · [Validation](/G:/Quant/data/research/lead-trader/burst-study-2026-11-29/market-validation.csv) · [Geometry calibration](/G:/Quant/data/research/lead-trader/burst-study-2026-11-29/clip-projection-validation.csv)

```powershell
$out = 'data/research/lead-trader/burst-study-2026-11-29'
$env:OPENBLAS_NUM_THREADS = '2'
$env:OMP_NUM_THREADS = '2'
.venv/Scripts/python.exe -m research.lead_trader.burst_study $out
```

The script reads the frozen action study, confirmed public execution records and existing local market data. Use `--analyze-only` to regenerate the tables after the two typed input panels are exported. No new market downloads are required.
