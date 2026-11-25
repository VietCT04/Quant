# High-throughput research execution

Phase 4 extends the existing SDK and native engine. Parquet and its audited metadata define market data. Research jobs use development dates and the same portfolio accounting, execution rules, risk limits, trade coverage, mark availability and funding as individual simulations.

## Run the acceptance campaign

```bash
uv sync --locked
uv run python -m examples.research_campaign --out bench-data/campaign --interrupt-only
uv run python -m examples.research_campaign --out bench-data/campaign
uv run python -m quant research-status bench-data/campaign/research.sqlite
```

The campaign registers 100 predeclared EMA configurations. The first call records an interrupted attempt for eight configurations. The next runs the queued screening jobs and compares the complete outcomes against sequential screening. It then runs eight predeclared native simulations in summary mode, checks sequential equivalence, and replays a fixed candidate with full audit logs. The selected configurations are fixed before evaluation.

## Batch native simulation

```python
import numpy as np
import quant as q

data = q.data.select(q.data.open(), start="2026-06-10", end="2026-06-11")
fast = q.features.expression("quant.ts.ema", "close", span=20, units="USDT/base")
slow = q.features.expression("quant.ts.ema", "close", span=100, units="USDT/base")

def targets(block):
    return 100 * np.sign(block.values["fast"] - block.values["slow"])

candidates = [q.simulator.Candidate("example", targets, {"fast": fast, "slow": slow})]
result = q.simulator.run_batch(
    data, candidates, out="bench-data/example",
    cache_directory="bench-data/prepared", workers="auto",
    memory_budget_gib=4, disk_budget_gib=8, logging="summary",
)
```

A candidate supplies either a deterministic callback or one fixed notional per symbol with `allocation`. The callback sees immutable causal arrays on a chronological block and returns signed USDT in the same shape. The batch input fields are `close`, `execution` (audited bar VWAP), `volume`, and `mark`. It includes the scheduled timestamps, availability clocks, and coverage status. Candidate-specific aliases point to shared output arrays. Use stateful streaming feature kernels for decisions that depend on prior blocks. Keep each callback's mutable state private to the candidate. Parallelism advances independent portfolios and preserves chronological order within each portfolio.

`features.plan` enumerates the unique dependency graph. Registered aliases with the same canonical function and identical serialized parameters share a node. Each node advances once per block for the wave. Stateful EMA and Wilder restart only on their documented boundaries. Shared outputs carry the same causal revision through the wave. Persisted recursive outputs would need a key for the complete prior history or state snapshot. The current disk cache persists market inputs and keeps recursive state in memory.

### Storage, budgets, and cancellation

Direct streaming is the default. A prepared-cache request reserves disk space for the selection before work. Time-major float64 stores close, execution reference, volume, and mark in 32 bytes per scheduled cell. One more byte retains status, with separate funding events and metadata. A full-development Liquid 50 selection needs approximately 18.2 GB. This estimate excludes report artifacts and filesystem overhead.

A single writer streams source blocks to disk and publishes a complete generation after a successful write. Readers memory-map committed bytes read-only in bounded windows. Publication and recovery use the existing OS writer lock and an atomic rename. Active generations are pinned. Cache cleanup is a maintenance operation performed after readers stop. A read validates the recorded identity, shape and dtype. Source audit receipts and auxiliary inventory are part of the identity. Cache files belong on a trusted local filesystem.

A batch's memory reservation determines the wave and block sizes. It accounts for framework working arrays and estimated native log capacity. The ceiling applies to reserved work, with OS page residency, native runtime scratch, and custom callback allocations measured separately. When a CPU limit is provided, the native feed disables multithreaded Arrow scanning for the batch. Third-party kernels in a callback may have their own thread pools. Automatic workers use available execution affinity and the detected physical-core count, with at most four workers for small native waves. Pass `workers` and `cpu_limit` to benchmark a deployment.

The cancellation event is checked between blocks, including during cache creation. Each engine finishes its active native call. Shared blocks and already committed waves remain reusable. Use a frozen `resume_token` in consolidated mode to restore a published wave after validating its data, configuration and callback version token. Restarted uncommitted waves replay chronologically from their beginning. Use the same wave configuration when resuming. For audit logs, keep separate output folders per attempt.

### Logging

| Mode | Output |
|---|---|
| `benchmark` | Terminal infrastructure counters and initial/final accounting output |
| `summary` | Daily marked returns, worst marked drawdown, risk-tick counts, peak gross exposure, terminal exposure and margin, fees, funding, turnover, positions, orders/fills counts and data warnings |
| `standard` | Summary accounting, periodic equity, targets, orders, fills, funding and closed-reduction statistics |
| `audit` | Standard events and equity at every tick |

Summary and benchmark persist one atomic JSON per wave by default. `Result.report_path` points to that artifact, keyed by candidate name. `report_storage="per-run"` restores separate reports. Summary omits closed-fill distribution statistics and individual logs. Choose standard or audit replay for those artifacts. All four modes use the same order and settlement logic.

## Diagnostic screening and scheduling

Use `q.evaluate.screen` with `ScreenCandidate` values to share market preparation, causal features and forward labels. It reports Pearson or rank IC, coverage, score turnover, monthly stability and optional quantiles and threshold events. `screen_decay` uses a fixed horizon grid. `screen_walk_forward` accepts existing validation splits and supplies only the training selection to the candidate factory. Screening metrics are descriptive. Score turnover is a proxy and omits native execution and costs.

Use `q.research.submit` to create jobs in the experiment registry. Each job has a handler kind, frozen parameters, optional dependency IDs and capability requirements. Jobs share SQLite provenance with ordinary experiments, including negative outcomes. Batch submission is atomic and deduplicated. Each claimed job receives an attempt number. A dispatcher recovering the database under its exclusive local lock requeues abandoned leases within the attempt limit. Cancellation, dependency failures and missing-capability links are recorded independently.

A trusted batch handler receives a list of registered job records and returns a mapping keyed by experiment ID. Each result carries metrics or a structured failure, and may carry artifacts and warnings. Bind the requested market data using `q.research.verify_selection`. Specify the handler code explicitly in Python or by using `research-run DB --handler kind=module:function`. The database stores data-only specifications. `research-submit DB SPEC.json` accepts the keyword arguments of `q.research.submit`, including `jobs`. `research-cancel DB ID` records cancellation. An in-flight handler can respond cooperatively to a cancellation event and its committed outcome reflects cancellation requests.

The reserved interval is [2026-08-25, 2026-11-25) UTC. Liquid 50 was frozen with November 2026 volume, so its historical membership has selection bias. [Research protocol](RESEARCH_PROTOCOL.md)
