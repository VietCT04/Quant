# Quant Research Platform Roadmap

## Active | Find one profitable alpha

The owner has authorized research throughout the existing two-year archive. Run asynchronous native simulations on the latest six months first. Review promising configurations across the full two years and preserve both results. ROI and daily net Sharpe are the primary research metrics. Use the execution ledger, limitations and counterevidence to judge whether a result warrants further research. [Research protocol](docs/RESEARCH_PROTOCOL.md)

V2 reuses native accounting, the SDK, block-major execution, the scheduler, and the registry. It adds explicit market orders, a completed-bar clock, full fills, execution diagnostics and terminal position accounting. Target-model reports retain their original semantics. [Simulator contract](docs/SIMULATOR_V2.md)

- [Earlier research](research/phase5/KNOWLEDGE.md) and [V2 research findings](research/v2/KNOWLEDGE.md)
- [Strategy authoring](docs/STRATEGY_AUTHORING.md) and [SDK guide](docs/SDK.md)
- [Control Center](docs/CONTROL_CENTER.md) and [Backtest Explorer](docs/BACKTEST_EXPLORER.md)

## Established platform

- Audited five-second Binance USD-M market archive, frozen Liquid 50, and coverage, mark and funding inventories.
- Native portfolio accounting, causal quantitative SDK, shared feature and market blocks, and parallel execution.
- Durable experiment registry, asynchronous hypothesis queue, recovery, SSE, research inbox and artifact-backed explorer.
- Versioned trusted strategy registration, detailed replays and frozen execution provenance.

## Deferred

Statistical significance and robustness, market regime analysis, advanced parameter sensitivity, simulator performance optimization, multi-alpha portfolios, capital allocation, leverage optimization, independent future-data validation, exchange-specific execution and capacity modeling, paper trading, and live trading. Real-capital activity requires separate explicit authorization.
