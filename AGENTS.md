# AGENTS.md — Quant Research Platform

## Mission

Find one economically meaningful alpha with attractive net ROI and daily Sharpe across the available historical conditions. Preserve causality, execution and accounting integrity, reproducibility, and honest reporting. Real orders, exchange credentials, capital deployment and multi-alpha portfolio construction are outside the authorized scope.

## Research workflow

1. Read [ROADMAP.md](ROADMAP.md), the [research protocol](docs/RESEARCH_PROTOCOL.md), [strategy authoring](docs/STRATEGY_AUTHORING.md), and the [simulator contract](docs/SIMULATOR_V2.md). Review the [previous findings](research/phase5/KNOWLEDGE.md) and [V2 findings](research/v2/KNOWLEDGE.md).
2. Start with a falsifiable market mechanism. Search the [SDK catalog](docs/FUNCTION_CATALOG.json) and reuse trusted operations. Strategies own sizing, scheduling, entry, holding, rebalancing and exit decisions. Register trusted versioned modules and keep each candidate's mutable state private.
3. Submit native simulations asynchronously through the [Control Center](docs/CONTROL_CENTER.md). Use UTC ranges [2026-05-25, 2026-11-25) for initial tests and [2024-11-25, 2026-11-25) for promising candidates. Default to Liquid 50 with documented subsets where appropriate. Both ranges include research data and cannot establish independent out-of-sample evidence.
4. Continue independent work. Review the inbox at useful checkpoints or use SSE activity. Keep initial and full-history results separate. Prioritize net ROI and daily Sharpe, then examine drawdown, execution, trade concentration and data concerns. Statistical screening, default baselines, and numeric promotion thresholds are outside the default workflow.
5. Record hypotheses, experiment IDs, strategy versions, ranges, outcomes, contradictions, concerns, confidence, and follow-ups. Preserve failed and negative evidence. Reconcile executions and lifecycle questions in the [Backtest Explorer](docs/BACKTEST_EXPLORER.md). Distinguish execution constraints, technical failures and economically poor outcomes.

## Data and trading integrity

Causal features use completed bars and observable history. Unknown, quarantined and pre-listing intervals stay explicit. A confirmed empty bar provides no executable open. Higher timeframes use completed observations. Research labels never enter tradable features. The November 2026 Liquid 50 selection creates historical selection bias.

Use [explicit one-way market orders and the V2 defaults](docs/SIMULATOR_V2.md). Holding maintains actual base quantity. Preserve one native accounting engine, including fees, funding, solvency, buying power, weighted entries and virtual terminal closes. Keep legacy results readable and reject incompatible replays explicitly. HTTP submissions contain JSON data and resolve only trusted local code.

## Engineering and publishing

Inspect status, branch, recent commits, relevant source and tests before editing. Respect more specific instructions and preserve unrelated work. Reuse the existing scheduler, registry, artifact storage and quantitative SDK. Prefer small, validated changes and measured workloads. Request missing capabilities through the SDK request mechanism when warranted.

Use the locked environment and run relevant regression tests and the production UI build. Keep Windows and Linux compatibility. Use deterministic fixtures for execution and accounting. Report measured elapsed time and memory for representative workloads. Exclude credentials, local datasets, databases, caches and generated runs from Git. Publish authorized work in focused fast-forward commits directly to main. Open a pull request only on explicit owner request. Resets, forced pushes and merges require separate authorization.

The earlier protected holdout split was superseded by the V2 owner specification. Historical policy and prior registry evidence remain intact.
