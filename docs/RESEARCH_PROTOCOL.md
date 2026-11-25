# Research protocol

Development is [2024-11-25, 2026-08-25) UTC. Final evaluation is reserved for [2026-08-25, 2026-11-25) UTC. Liquid 50 membership uses November 2026 data and retains historical selection bias.

## Development

1. Register a plausible mechanism, features, horizon, evaluation split, costs, rejection criterion, and candidate budget before inspecting outcomes.
2. Fit and select only on training data. Use chronological walk-forward windows. Purge labels that overlap evaluation and apply an explicit embargo where warranted.
3. Report effect, usable observations and coverage. IC, quantile spreads and event means are descriptive. Supply complete regular arrays for HAC, and justify a lag at least as long as label overlap. Use joint observations and justified block lengths when resampling. Register the hypothesis family before applying Holm, Bonferroni, BH or BY.
4. Compare across symbols, periods, regimes, parameters and cost assumptions. Placebos permute joint symbol blocks. Sparse permuted inputs can reduce coverage. The deflated Sharpe utility requires a documented independent-trial count, consistent sampling and a valid independent-return approximation. [Original Deflated Sharpe paper](https://davidhbailey.com/dhbpapers/deflated-sharpe.pdf)
5. Separate marked portfolio performance and open PnL from closed reductions. Record modeled execution costs, uncertain marking and funding gaps.
6. Retain negative and failed trials. `q.experiment.reproduce` returns the recorded inputs and provenance. It does not execute scripts stored in SQLite.
7. Submit predeclared candidate waves through the local scheduler and require a trusted handler to check the saved selection. Treat batched screening as descriptive. Replay a selected native experiment in audit mode using its recorded configuration for detailed decisions and fills. [Research execution guide](RESEARCH_ENGINE.md)

## Owner-authorized final evaluation

1. Obtain explicit owner authorization for a frozen strategy, parameters, universe, data fingerprint, clean Git commit, target digest and all native cost and risk settings.
2. Create an infrastructure selection for exactly the reserved window and deterministically prepare targets with the frozen strategy, without reviewing outcomes. Record the approval and frozen specification with `q.validation.record_final_release`.
3. Run `q.simulator.final_run` with the exact selection, target snapshot, settings and receipt. It checks the clean commit, data and settings, claims the local receipt once and embeds it in the native report. Review a failed claim with the owner before attempting a retry.
4. Report the release reference and the frozen universe bias. Subsequent tuning consumes independent data.

The approval reference is an audit record in this local workflow. Phase 3 verified its path with isolated fixtures and did not perform a real holdout evaluation.
