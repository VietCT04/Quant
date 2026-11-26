# Phase 5 research knowledge

## PH5-001 | Five-minute mechanism screen

**Decision.** No tested configuration qualified for August confirmation. The first campaign found directional screening evidence for several mechanisms and a decisive economic rejection of their specified execution policies. There is no newly validated alpha.

- **Protocol.** [Preregistered protocol](PROTOCOL.md). Campaign `C-31edca5336e4521a`, hypothesis groups `H-0001`–`H-0020`. The synthetic production-screen fixture yielded Pearson IC 1.0000 for the known signal and −0.0086 for the null across 4,095 observable labels. Both outcomes matched at block sizes 113 and 1,024. Label alignment, split-edge exclusion and the Holm adjustment fixture passed.
- **Development boundaries.** Screen folds were May 12–14 and June 9–11, 2026. Batches used 207,360 scheduled symbol-bars each, with 72 unavailable in May and none in June. July 1–22 was the later native validation window. The August development confirmation and protected final evaluation were untouched. The six-symbol results inherit Liquid 50 selection bias.
- **Run records.** The registry is `data/quant-control-phase5/research.sqlite`. Frozen configuration, dataset fingerprints, receipts, candidate and job identifiers, raw registry results, and review decisions are under `data/research/phase5-001/`. The [local Research Control Center](http://127.0.0.1:8785/) shows the queue, applied verdicts and evidence. The initial submission recorded a dirty working tree with the new research code present. The registry separately froze the strategy implementation and feature graph used by the jobs.

### Screening

All 240 jobs completed. The scheduler evaluated all 120 configurations together in each fold and shared 49 feature nodes per fold. The following table gives the best lower-fold IC across the 12 configurations in each family, including ineligible configurations. Forty-two configurations cleared the paired-fold gate.

| Mechanism | Best lower-fold IC | Gate passes | Decision |
| --- | ---: | ---: | --- |
| Quiet price shock reversal | 0.0596 | 6 | Native validation |
| Aggressor-flow persistence | 0.0156 | 5 | Outside five-family cap |
| Muted response to aggressive flow | −0.0167 | 0 | Screen rejection |
| High-volume exhaustion | 0.0363 | 7 | Native validation |
| Activity-confirmed range breakout | −0.0350 | 0 | Screen rejection |
| Volatility compression breakout | 0.0140 | 1 | Outside five-family cap |
| Peer relative-value reversal | 0.0819 | 10 | Native validation |
| BTC-to-alt lagged response | −0.0065 | 0 | Screen rejection |
| Concentrated print reversal | 0.0322 | 9 | Native validation |
| Value versus count aggressor divergence | 0.0555 | 4 | Native validation |

The range-breakout result does not validate the reverse direction. Its sign makes a failed-breakout hypothesis worth distinguishing from the original continuation mechanism using new dates. Flow persistence and compression passed on some settings but were not tested economically.

### Native validation

Campaign `C-8fb9011b915bb186`, hypothesis groups `H-0021`–`H-0030`. Costs and PnL below are in USDT. Gross excludes fees, spread, slippage, impact and funding. Every test reached the existing 35% drawdown suspension. All summaries have 21 daily marks and passed the review's mark completeness gate. All July Holm-adjusted p-values were 1.00. Losses cluster near the risk stop and should not be used to rank families.

| Mechanism | Window / threshold | May IC | June IC | Base gross | Base net | Stress net |
| --- | --- | ---: | ---: | ---: | ---: | ---: |
| Peer relative-value reversal | 24 / 0.5 | 0.0819 | 0.0961 | 512 | −35,043 | −35,062 |
| Quiet price shock reversal | 60 / 1.0 | 0.0596 | 0.0669 | 218 | −35,001 | −35,049 |
| Value versus count divergence | 6 / 1.5 | 0.0555 | 0.0910 | −538 | −35,033 | −35,005 |
| High-volume exhaustion | 12 / 0.5 | 0.0363 | 0.0524 | −372 | −35,028 | −35,031 |
| Concentrated print reversal | 24 / 1.0 | 0.0322 | 0.0962 | 446 | −35,014 | −35,056 |

### Exact replay and execution evidence

[Backtest Explorer for base-cost peer reversal](http://127.0.0.1:8785/#explorer?hypothesis=H-0021&candidate=96dbf87a699d45f7bba33c19959de3f6). The detailed job `3d9f1d97279d4cf2bfcd0f65901d7a76` (`H-0031`) reproduced native PnL and was attached by the service. The Explorer reconstructed the per-symbol positions. The native execution ledger contains 51,945 order records, including 49,880 booked fills, 48,844 marked partial, associated with 860 distinct decision timestamps. Turnover was 50.80M USDT. Gross edge was 0.101 bp per USDT of filled notional versus approximately 7 bp modeled friction. Fees were 25,400 USDT, modeled spread and slippage 10,160 USDT, and funding +5.32 USDT. The last fill was July 4 at 05:29:15.100 UTC.

The largest closed position loss in the Explorer was ETHUSDT-202, a short held 305 seconds across four fills. Its booked-fill realized loss was 162.57 USDT, plus 20.07 USDT in fees, for net −182.64 USDT. Modeled fill slippage is already reflected in the booked-fill realized amount. Sampled excursions have full recorded five-second mark coverage for this position.

### Follow-up hypotheses

1. **Net edge may require persistent dislocations and a no-trade band.** The strongest relative-return score earned 0.101 bp gross per USDT traded before the risk stop. Test whether a calibrated expected spread persists long enough to clear a native one-way implementation hurdle (7 bp base, 11.5 bp stress plus modeled impact). On chronologically prior development samples, estimate direction, cost-normalized edge and decay at sparse decision times. Carry actual filled positions forward with the SDK's portfolio-state callback. Test turnover suppression separately from changing the forecast and require absolute out-of-sample net improvement over cash at stress. Reject if gross edge per USDT traded remains below the hurdle or performance is concentrated in one symbol or day.
2. **Failed price breaks might behave differently from successful range expansion.** The continuation direction failed the paired screen on every configuration. A separate hypothesis would classify the first close back inside the range using only information observable at that new decision, compare it with similar activity-matched nonbreakouts, and test for retracement after realistic costs. The original breakout scores remain discovery evidence only. Reject on new chronological blocks if the conditioned spread fails to widen consistently.

These hypotheses are proposed from the first campaign's evidence. No follow-up execution or retuning used the July validation or August confirmation dates. Independent review is pending.
