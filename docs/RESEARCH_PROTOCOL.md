# Research protocol

The authorized research range is [2024-11-25, 2026-11-25) UTC. Initial native simulations use [2026-05-25, 2026-11-25). Submit promising configurations unchanged over the full range and preserve both results. Liquid 50 is the default universe. Its November 2026 selection creates historical selection bias. The two-year run reuses research data from the six-month window.

Describe the mechanism, parameter variants and intended trading behavior. Register a versioned order strategy and submit through the persistent [Control Center](CONTROL_CENTER.md). The service publishes results and diagnostics to the inbox independently of the submitting client. Use the base [execution model](SIMULATOR_V2.md) unless a research question calls for a recorded override.

Review ROI and daily Sharpe with net P&L, drawdown, position count and execution success. Inspect stale marks, funding gaps, unavailable symbols, parameter dependence, high rejection rates, and profit concentration. Record evidence, contradictions, concerns, confidence, and follow-up hypotheses. Use detailed replay for execution and position questions. The default workflow has no mandatory numeric thresholds, statistical tests or baselines.

The historical policy reserved [2026-08-25, 2026-11-25). V2 supersedes that policy for ordinary research. Prior registry outcomes and release records remain historical evidence. Independent future-data validation and any real-capital activity require a separate decision.
