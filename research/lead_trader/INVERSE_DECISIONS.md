# Inverse decision study | Binance lead portfolio 5140488422016171264

This study predicts the trader's next recorded action from information available before the clock. It retains the earlier [behavior study](BEHAVIOR_STUDY.md) and its four-hour market observation as separate research. No future market returns, simulated trades or research submissions are targets here.

## Evidence, clocks and denominators

- **Universe.** Compare markets at the time they have locally observed Binance USDT perpetual bars. The original roster includes both current exchange metadata and historical public archives. Continuous trailing turnover is required for cross-sectional ranks. Major coins are excluded from the altcoin benchmark. Absent source bars, non-observable listings and delivery statuses are kept as coverage limitations. The benchmarks use an equal-weight altcoin return excluding the subject contract, winsorized hourly component returns and 168-hour covariance estimates. Residual returns use an altcoin beta lagged by 24 hours. BTC beta, altcoin beta, residual volatility, and changes in rolling betas are separate features.
- **Selection.** A symbol-day beginning at UTC midnight is a 24-hour observation. Inactive symbols have no reported exposure during the prior seven days. Activation is the first reported opening of a new episode. Deactivation is the last reported close followed by seven days without another reported opening, with censoring at both archive edges. The deactivation risk set contains symbols already holding exposure at midnight. Negative selection labels depend on the public position archive and carry its paging uncertainty.
- **Entries.** Verified flat minutes begin after a reconciled pure close and end at the next archived order or verified order-window boundary. Entry labels need a side-consistent opening report at the next order timestamp. Comparison data also include conditional side at reported activations and market-aligned reported openings away from an opposite-side boundary. These latter analyses condition on an opening being reported. They estimate a side choice and cannot substitute for a verified flat denominator.
- **Inventory.** Complete reconciled lifecycles supply one-minute opportunities. The market and inventory covariates are sampled strictly before the minute. The label is the observed action within the next minute, with mixed and terminal actions separated. HOLD requires a certified no-order interval. A terminal reversal is attributed to the outgoing side. A raw stream gap invalidates affected 15- or 60-minute order counts. The size cases are conditional on an actual action, and use the earlier of its reported order and update clocks.
- **Inventory provenance.** Quantity and entry basis come from booked fills already elapsed at the clock. A reversal opening leg can require a retrospective reconciliation to expose the trader's contemporaneous state. The fitter receives no terminal position size, closing P&L, future executions or later report statistic as an input. The history-only and history-plus-market models omit retrospective inventory anchors and are sensitivity checks. Peak exposure for the small-opening growth study is a label and never an input. Complete-lifecycle sampling is an archive selection effect.
- **Resolution.** A completed hourly bar must end strictly before the clock. Market covariates cover returns, momentum, acceleration, drawdown and rebound, turnover, trade counts, flow, observed age and cross-sectional ranks. Fine sensitivity uses audited five-second inputs with the existing 100 ms publication lag and preserves known-empty and missing status. The audited archive ends before the late order days. Marks use the most recent audited close when available and otherwise the last completed hourly close. Past settled funding is observed only for covered symbols. Public five-minute open interest has an additional five-minute embargo. Current order-book depth, private strategy state, historical social popularity, reliable token taxonomy and private funding estimates remain unavailable.

## Validation and interpretation

Every binary probability table reports the signal sample and event count, P(action given signal), P(signal given action), the eligible baseline, absolute difference and lift. Training-fold quartiles define its high and low bands. Joint bands and 3-by-3 surfaces expose interactions without assuming they are private thresholds. Where the number of clusters supports it, the exporter reports separate exploratory 90% block intervals over dates and symbols. Neither interval removes the other source of dependence.

Models compare the observed frequency with regularized symbol and past-order models, then add inventory state and market features. The full model includes a fixed set of nonlinear hinges and interactions. Selection has an interpretable rank-only comparison. The open-interest cohort has a matched ablation with the derivative inputs removed. Each fit uses only its training split. For large binary training sets, a fixed seed retains every minority outcome, samples excess controls and restores the original training class weight. All evaluation and probability denominators retain the full support. Validation uses later UTC dates and separate deterministic held-out symbol folds. A lifecycle crossing the chronological boundary is excluded from training. Deactivation adds a seven-day label embargo. Log loss and average precision are the main binary diagnostics. Continuous amounts fit a smooth absolute loss and use held-out absolute log-size error, with actual USD distributions and remaining signed base quantities saved separately.

## Findings

### 1. Market conditions explain watchlist activation more than direction

The supplemental pull recovered 162 of 190 missing roster symbols with public hourly data. The expanded source has 912 contracts, with 28 roster entries still lacking observed bars. The 105-day selection panel contains 57,616 covered inactive symbol-days across 697 symbols and 32,398 active symbol-days across 380 symbols. It records 158 activations (95 long, 63 short) and 51 deactivations. The remaining markets in the roster can contribute to a cross-sectional rank when they have usable bars. Missing scope is explicit.

**Volatility, directional rank and liquidity carry the clearest activation signal.** In the joint high-volatility/high-return band, there were 92 activations in 5,887 observations (1.56%), versus 158 in 57,616 (0.274%) overall. The lift was 5.70x and the difference was +1.29 percentage points. That band captured 58.2% of activations and 10.2% of observations. The date-cluster 90% rate interval was 1.17% to 1.92%. In the chronological test it gave 10/1,711 (0.58%) versus 22/19,052 (0.115%), a 5.06x lift. About 98.4% of the pooled high/high observations still had no activation. This is an observable selection preference with a very low absolute occurrence rate.

Individually, high volatility had 143/14,492 activations and high turnover rank had 140/14,334. Each was about 3.6x the baseline. High beta-adjusted residual volatility had 137/14,138 (3.49x) in its eligible sample. High 24-hour relative turnover and trade counts had lower pooled lifts near 2.7x. Median market ranks and the absence of BTC/ETH trading in the longer prior study also favor a repeatable active-altcoin filter. The equal-weight altcoin comparisons, residuals and relevant beta/correlation bands are exported separately. Shared market moves leave a material residual component, but the rank-only model captured most of the improvement on unseen symbols. This provides weaker evidence for a special private residual formula. Historical token themes and popularity labels remain unavailable.

| Activation validation | History and symbol log loss | Ranks-only log loss | Full log loss | Full average precision |
| --- | ---: | ---: | ---: | ---: |
| Later dates, 22 events | 0.01012 | 0.00873 | 0.00770 | 0.028 |
| Unseen symbols, 158 events | 0.01880 | 0.01416 | 0.01408 | 0.072 |

**Initial direction remains weakly explained.** The high/high rank band increased the probability of both long and short activation (5.46x and 6.06x pooled). Once conditioned on a symbol activation, chronological log loss was 0.759 for the full market model versus 0.684 for the unconditional side frequency. On unseen symbols it was 0.703 versus 0.679. A second test of 315 hourly-aligned, reported opening boundaries without an immediate opposite handoff also favored the frequency or symbol baseline over the full model in log loss. Rank preferences describe where the trader operates. The results leave the directional rule unresolved. Actual order evidence supplies only one long entry and one short entry across 728 reconstructed flat minutes (238 market-ready), so a validated flat-entry timing model would be premature.

**Deactivation remains unresolved.** The full model had chronological log loss 0.01621 versus 0.01521 for the frequency baseline. On unseen symbols it was 0.01200 versus 0.01174. An unusually negative change in altcoin beta gave a 2.03x chronological lift, but only 11 qualifying events and a pooled lift of 1.33x. Market activity and volatility bands relate only weakly to the reported ending event. The result leaves open gradual disengagement, inventory close criteria and incomplete reported history.

### 2. Past execution cadence and inventory explain most of the next adjustment

The complete-path tape has 123,005 unique holding minutes, including 120,316 verified no-order minutes, plus 728 verified-flat opportunities before market-readiness filtering. The directional model panels contain 61,662 long and 58,639 short holding minutes. The positive counts are 183/308 for additions, 613/835 for reductions and 244/253 for long-to-short/short-to-long reversals. Standalone closures are too sparse to model reliably. The four-day audited fine-resolution panel is a separate comparison.

| Signal for the next-minute action | Pooled actions / signal minutes | Baseline | Rate and lift | Later-date actions / signal minutes |
| --- | ---: | ---: | ---: | ---: |
| Long add, last fill within about 7 min | 109 / 4,750 | 0.906% | 2.29%, 2.53x | 33 / 1,260 (2.62%, 2.26x) |
| Short add, last fill within about 6.3 min | 209 / 5,359 | 1.337% | 3.90%, 2.92x | 84 / 1,235 (6.80%, 2.52x) |
| Long reduce, marked exposure above about $57 | 495 / 15,191 | 0.994% | 3.26%, 3.28x | 155 / 2,387 (6.49%, 3.22x) |
| Short reduce, exposure above about $60 and marked over 389 bp below cost in the position direction | 149 / 2,269 | 1.424% | 6.57%, 4.61x | 92 / 540 (17.04%, 6.52x) |
| Short-to-long, last fill within about 6.3 min | 119 / 5,359 | 0.903% | 2.22%, 2.46x | 56 / 1,235 (4.53%, 2.45x) |

The previous-fill conditions use only the subset with an observable prior order, so their baselines differ from the full directional minute rates. The absolute rate differences in the pooled rows are approximately +1.39, +2.56, +2.27, +5.14 and +1.32 percentage points. For the short reduction interaction, the date-cluster 90% rate interval is 2.95% to 13.73%. The band captured 149/835 reductions (17.8%) while comprising 2,269/58,639 holding minutes (3.9%). The effect direction held in 17 of 20 symbols with sufficient band support. These are pooled associations conditional on existing inventory. Recent fills and marked exposure can encode earlier decisions and unobserved intent.

**Holding is the default observed behavior between bursts.** Even within the long-add recent-fill signal, 97.7% of minutes had no long ADD. The atlas and matched examples separate pure HOLD from other actions. Side-specific recency cutoffs are empirical training quantiles, not a trading schedule. The short-reduction interaction and the contrast with opposing actions suggest testing inventory-dependent distributions rather than a common price-only rule.

| Next-minute model | Later-date frequency log loss | History and symbol | History and state | Full |
| --- | ---: | ---: | ---: | ---: |
| Long add | 0.0338 | 0.0273 | 0.0274 | 0.0384 |
| Short add | 0.0839 | 0.0599 | 0.0589 | 0.0996 |
| Long reduce | 0.1041 | 0.0810 | 0.0743 | 0.0835 |
| Short reduce | 0.1263 | 0.0902 | 0.0853 | 0.1001 |
| Long-to-short reversal | 0.0559 | 0.0498 | 0.0449 | 0.0656 |
| Short-to-long reversal | 0.0645 | 0.0529 | 0.0501 | 0.0714 |

Reductions show the clearest incremental effect of state beyond past trading. On held-out symbols, long-reduction log loss fell from 0.0489 to 0.0458 with state, and short-reduction loss fell from 0.0616 to 0.0573. On the same split, short-to-long still had log loss 0.0285 with state versus 0.0278 for the frequency baseline. Long-to-short had stronger later-date evidence with state but weaker unseen-symbol improvement. Terminal timing, why a position switches direction, and common thresholds across symbols remain uncertain.

On the fine panel, long additions were more common after recent orders in busy minutes (22/593 versus a 0.734% eligible baseline). Later dates retained the lift but only nine events supported it. Short additions had a different relationship. Fine market models failed to improve both dates and symbol-separated validation consistently. Recency of a prior fill is therefore better supported than an inferred universal price, rebound or taker-flow trigger. See the paired no-execution examples and all condition-level results.

The HOLD model made the same point. Within the 120,301 market-ready holding minutes, 117,774 (97.9%) were pure HOLD. On later dates, adding history and state lowered HOLD log loss from 0.1972 to 0.1235. On held-out symbols it fell from 0.1025 to 0.0746. Adding all market inputs increased log loss on both splits. As one concrete counterexample, an HMSTRUSDT long reduced at the Nov 20 21:55 UTC opportunity with about $6,990 marked exposure and 63 seconds since its last order. It then held without execution at 21:57 with $6,933 and 71 seconds since its last order. The exact paired examples are available with their risk-state covariates.

### 3. Large clips cluster. Exact size and future growth are different questions

The 2,060 observed additions had a median clip of $11.86. Of those, 477 (23.2%) reached $50 and 62 reached $500. Previous executed order size provided the clearest empirical separation. The thresholds below use actual USDT and include only cases with a known prior order. Rates are conditional on an observed add or reduction.

| Current decision and previous order | Actions in band | At least $50 | Rate | Lift vs all actions of this type |
| --- | ---: | ---: | ---: | ---: |
| ADD after a previous clip under $20 | 1,364 | 99 | 7.3% | 0.31x |
| ADD after a previous clip at least $100 | 316 | 213 | 67.4% | 2.91x |
| REDUCE after a previous clip under $20 | 1,931 | 199 | 10.3% | 0.44x |
| REDUCE after a previous clip at least $100 | 644 | 357 | 55.4% | 2.38x |

For adds of at least $500, the corresponding counts were 6/1,364 and 36/316. Among known marked states, 76/664 adds reached $50 on positions under $75 versus 254/632 on positions of at least $250. Previous clip, recent order count and existing exposure are therefore useful to rank the size regime. They reflect accumulated order intent and may be correlated with the trader's private allocation and market. Even after a $100 previous order, 103 of 316 additions stayed below $50.

The market-ready large-add classifier used 1,932 cases and 393 positives. History and state beat the frequency baseline on later-date log loss (0.280 vs 0.455) and on held-out symbols (0.379 vs 0.514). On held-out symbols, history alone was essentially tied (0.379). Average precision was 0.629 on later dates and 0.627 across unseen symbols. Large reductions used 3,198 cases and 673 positives. State improved the model over both its frequency and history baselines on later dates (0.419 vs 0.460 and 0.481) and on unseen symbols (0.428 vs 0.519 and 0.448). Adding the broader market feature block hurt calibration on both actions' later dates.

**Exact amount differs from size class.** On later dates, the unconditional historical median beat both the history/state and full models for exact add and reduce clip size in absolute log-size error. The same comparison differs for the actual order containing a reversal. We independently matched outgoing state for 450 of 564 incoming reversal allocations. In the 538 market-ready reversal order cases, history and state lowered later-date absolute log-size error from 1.266 to 0.874 and unseen-symbol error from 1.255 to 0.923. The after-booking result is saved as signed base quantity alongside USDT exposure. Post-add and post-reduction exposure was much more predictable than the clip alone. For example, later-date reduction exposure error fell from 1.177 for the median baseline to 0.334 with history and state. That partly reflects the accounting relationship between starting and remaining exposure.

A genuine pure opening is rare in the reconciled tape. Only five such bookings were available, so there is no stable model of the size chosen at a flat start. Among 225 market-ready lifecycles beginning with an allocation of $20 or less, mostly reversals, 42 later reached $100 of peak marked exposure. Chronological frequency log loss was 0.513, versus 0.559 for history and 0.949 for the full model. The opening snapshot leaves substantial uncertainty about which small positions later grow. In paired trajectory examples, an EVAAUSDT short grew from $43 to $7,651 and reported +$1,396. A TAGUSDT short grew from $26 to $5,790 and reported -$1,675. Both increased by a mix of small clips and much larger adds. The comparison is descriptive and both outcomes remain in the archive.

### 4. Open interest and funding are secondary evidence here

All 96 requested official daily metric files were retrieved for the 12 targeted active contracts. Using prior-only alignment yielded 40,471 eligible holding minutes with usable open-interest change across 11 contracts. Funding snapshots were locally available for 50 contracts with narrower overlap. The derivative block gave no consistent incremental improvement. In later-date OI-cohort reduction models, full log loss was 0.10688 versus 0.10617 without OI and funding, and 0.09403 for history and state. In OI-cohort addition and reversal models, removing derivatives also improved later-date log loss. Observed OI spikes can coincide with action bursts, so a descriptive lift is insufficient to identify an independent positioning trigger.

## What remains open

Three pieces of the decision process should stay separate in future work. Market ranks explain which inactive symbol is likely to become active. Recent orders and exposure explain when inventory management is likely to resume. The eventual size of a small opening and the private rule for changing direction remain unresolved. A longer verified order archive, more flat-to-open intervals, historical order-book depth and known publication clocks would be most informative. The four-hour rebound observation remains in the earlier knowledge base and has no role in these models.



## Reproduction

Run these commands from the repository root in the locked environment. The lead collector and research registry are independent of this workflow.

```powershell
$out = 'data/research/lead-trader/decision-study-v2-expanded'
$base = 'data/research/lead-trader/decision-study-v2'
$hourly = 'data/research/lead-trader/market-hourly'
$env:OPENBLAS_NUM_THREADS = '2'
$env:OMP_NUM_THREADS = '2'
.venv/Scripts/python.exe -m research.lead_trader.decision_market_supplement $hourly ($base + '/market-supplement')
.venv/Scripts/python.exe -m research.lead_trader.decision_study $out --hourly-extra ($base + '/market-supplement') --metrics-dir ($base + '/public-metrics') --get-metrics
.venv/Scripts/python.exe -m research.lead_trader.decision_atlas $out
```

The expanded evidence build took 68 seconds on the local machine. It produced 90,014 selection rows and 130,342 holding, flat and sizing rows. The frozen input is the local November 28 archive and preceding typed [behavior evidence](BEHAVIOR_STUDY.md). Use `--source` for a refreshed frozen behavior dataset. Optional `--get-metrics` retrieves official public open-interest files in a serial schedule. Market file formats follow the [Binance public market archive documentation](https://github.com/binance/binance-public-data/blob/master/README.md). The atlas is a self-contained HTML file. The JSON and CSV contain full denominators, uncertainty and validation metrics. Source code is versioned. Local market files, public archives and generated tables stay outside Git.
