# AGENTS.md — Quant Research Platform

## 1. Mission

Quant is a general-purpose, high-performance quantitative research platform, not a single trading bot. Its long-term goal is to enable autonomous researchers to discover, test, reject, refine and combine statistically defensible trading alphas using a shared research SDK and a trusted portfolio simulator.

Build reusable infrastructure before automated discovery. Optimize for correctness, causality, reproducibility, research productivity, and measured performance, in that order. Negative research results are valuable. Never optimize the infrastructure to make a particular strategy look profitable.

The owner defines project priorities, research constraints and phase transitions. Do not silently expand scope, release the holdout, place live orders, or make trading decisions with real capital.

## 2. Repository context and source of truth

Inspect the working tree, current branch, recent commits, README, relevant source and tests before making changes. Respect more specific AGENTS.md instructions in subdirectories. Do not overwrite unrelated or uncommitted user work.

Established components at the time this file was authored:

- `marketdata/`: Binance USD-M data acquisition, aggregation, validation, coverage and Arrow reader.
- `simulator/`: native C++20 futures simulation core, Python C ABI, market data feed, reference accounting, strategy adapters and reports.
- `data/binance/um/bars_5s/`: audited historical bars.
- `data/metadata/universes/liquid50.json`: frozen crypto-only Liquid 50 universe.
- `data/metadata/universes/liquid50_gaps.csv`: missing and quarantined intervals.
- `data/binance/um/simulator_aux/`: historical mark prices and funding.
- `quant/`: public research SDK for aligned data, features, numerical operators, diagnostics, simulation, research validation and reproducible experiment records.

Baseline: 50 crypto perpetual futures; 517,720,813 five-second bars; 30.38 GB compressed; UTC range `[2024-11-25, 2026-11-25)`; 98.23% planned post-listing coverage. Simulator V1 was merged in PR #1. Its reported lightweight native throughput was 65.8M observed bars/s; end-to-end development replay was about 2M bars/s. These are historical reported measurements, not performance guarantees.

The code, manifests, test results and current task are authoritative over stale documentation or numerical snapshots in this file. Keep README and detailed documentation synchronized with actual behavior. Use ROADMAP.md, if present, for phase-specific status.

## 3. Project boundaries and phases

1. Historical market data platform and Liquid 50 — completed baseline.
2. Portfolio simulator and accounting — V1 completed baseline.
3. Quant research SDK — implemented foundation; consult the generated catalog and roadmap for verified capabilities and remaining limits.
4. Research execution infrastructure — efficient experiment scheduling and resource sharing; complete remaining prerequisites for agents.
5. Automated alpha discovery — hypothesis generation and systematic evaluation using the SDK.
6. Research-driven SDK extensions and multi-alpha portfolio construction.
7. Paper trading, then live execution only with separate explicit authorization.

Do not begin a later phase just because an earlier task is complete. Small dependency fixes within the active phase are allowed; document them. Do not expand Liquid 50 or redownload audited source data unless the task requires it or a genuine correctness issue is found. Preserve TradFi data separately.

## 4. Default engineering workflow

For each task:

1. Read the applicable instructions, inspect existing components and determine the smallest coherent scope.
2. Search for an existing implementation or SDK capability before creating new code. Confirm contracts, dependencies and relevant tests.
3. For nontrivial work, make a short implementation plan with acceptance criteria and risk areas. Proceed independently on ordinary engineering decisions; ask only for material unresolved choices, destructive changes, or authorization boundaries.
4. Implement the smallest reusable, correct solution. Keep strategies separate from infrastructure and prefer small, reviewable changes.
5. Add meaningful tests, run focused tests, then regression tests. Investigate failures rather than disabling or weakening checks.
6. Benchmark representative workloads when changing a hot path. Compare identical configurations; record machine, data range, cache state, logging, memory and throughput.
7. Update documentation, examples, schemas, registry metadata and changelog where applicable. Never document planned capabilities as implemented.
8. Report what changed, exact test and benchmark results, commands to reproduce, artifacts, limitations, and any unfinished work. Do not claim completion without evidence.

Use `uv sync --locked` and `uv run pytest -q` where applicable; inspect current project tooling for other commands. Preserve Windows and Linux compatibility. Use the existing C++20 toolchain and C ABI unless a measured reason justifies changing them. Do not commit credentials, local datasets, generated caches or bulky logs. Do not reset, force-push, merge, or publish changes unless authorized by the user; use reviewable branches when publishing is requested.

## 5. SDK design contract

`quant` is the stable research-facing namespace. Keep `marketdata` and `simulator` as the underlying implementations. Do not build a second production simulator or competing accounting system.

The SDK should expose composable, independently testable primitives for data access, mathematics, time-series operations, statistics, indicators, cross-sectional operations, volatility, market microstructure, signals, portfolio construction, risk, simulation, evaluation, validation, optimization and experiment management. The current phase specification defines the exact implementation inventory.

- Give each public function a canonical name, explicit signature, mathematical definition, units, shape and axis semantics, parameter constraints, missing-data policy, warm-up behavior, numerical conventions, causality classification and examples.
- Specify standard defaults, including sample versus population variance, EMA initialization and adjustment, Wilder smoothing, rank ties, quantile interpolation, time windows and annualization. Different conventions must be explicit, never accidental.
- Prefer a single canonical implementation. Public aliases must delegate to it. Strategies must use shared SDK functions instead of duplicating EMA, returns, rolling statistics, etc.
- Support a consistent time/symbol data abstraction with aligned timestamps, symbol order, availability, validity and units. Reject ambiguous or incompatible inputs rather than silently broadcasting or shifting them.
- For stateful functions, implement bounded-memory streaming and document reset/gap behavior. Where streaming is supported, batch and arbitrary chunking must agree within documented tolerances. Explicitly label batch-only functions.
- Prefer NumPy, Polars, Arrow and established scientific libraries for reliable operations. Add native kernels when profiling establishes value. No unnecessary dependency or binding rewrite.
- Avoid placeholder implementations. If a requested feature or required input is unavailable, provide an explicit capability error and document the limitation.

The implemented registry is the source of truth for public names and supported semantics. Generate `docs/FUNCTION_CATALOG.json` and the Markdown reference from the code with `uv run python -m quant catalog`. Test catalog and source agreement before publishing.

## 6. Data, causality and evaluation boundaries

Use UTC, half-open ranges and the existing reader's data availability contract. A five-second bar beginning at `t` is normally observable at `t + 5000 ms + availability_lag_ms` (default lag 100 ms). A feature may not be used before all its inputs are observable. Higher-resolution aggregations must become available only after their constituent information is available.

- Distinguish traded, confirmed empty, missing, quarantined and pre-listing data. Never synthesize unknown market prices or flow; never confuse missing observations with zero volume. Do not silently bridge unknown gaps.
- Use trailing windows, causal as-of joins and historically observable information. No centered windows, backward fills, future-dependent normalization, future constituent selection or full-sample fitted parameters in tradable features.
- Keep forward returns, labels and research-only statistics logically separate from causal features. Future labels must not enter strategy input or caches used by strategies.
- Preserve listing times, active-symbol masks and coverage. Liquid 50 was selected using Oct 26–Nov 24, 2026 volume among then-active contracts. Historical membership therefore has survivorship and selection bias; never call this dataset point-in-time unbiased.
- Do not assume historical tick/lot filters or margin brackets are known exactly. Preserve limitations of the simulator's flat maintenance margin, modeled execution, mark fallback and incomplete funding. Aggregate-trade bars are not order books or exact executable quotes.
- Preserve simulator accounting and timing. Do not use a signal computed from a bar to fill at an earlier price. Report uncertain valuations, missing funding and simulated cost assumptions. No trading at missing or synthetic empty-bar prices.

### Protected evaluation period

- Development range: `[2024-11-25, 2026-08-25)` UTC.
- Reserved final evaluation: `[2026-08-25, 2026-11-25)` UTC.
- Ordinary research, feature selection, optimization, profitability diagnostics and candidate ranking must stay in development. Forward labels, fitted transformations and warm-up handling must respect evaluation boundaries; purge or embargo overlapping samples where needed.
- Infrastructure integrity tests and non-profitability performance benchmarks may use the full dataset. Do not inspect strategy PnL, alpha statistics or regime outcomes from the reserved period as part of such tasks.
- Final evaluation requires explicit owner authorization and a frozen strategy, parameters, universe, data fingerprint, code version and cost model. Record the release and results; do not reuse the period for tuning while continuing to call it untouched. The frozen universe bias remains even after release.
- Within development, use chronological train/validation and walk-forward splits. Fit scaling, selection and model parameters only on the applicable training data. No random IID splits for dependent time-series observations.

## 7. Research workflow (after the discovery phase is authorized)

A researcher proposes hypotheses, not changes to trusted accounting or evaluation logic. Each candidate should have a plausible market mechanism, testable prediction, horizon, feature definitions and a rejection criterion recorded before inspecting its results.

1. Search the SDK registry for required capabilities and check data availability.
2. Define the hypothesis, candidate expressions, parameter budget, evaluation split and execution assumptions. Register the experiment before execution.
3. Compose causal features using shared primitives. Use small deterministic experiments first to check semantics, coverage and computational cost.
4. Measure forward-return diagnostics, event studies, conditional expectations, quantile behavior, IC, decay and stability when appropriate to the hypothesis. Do not treat IC as a universal gate for nonlinear or path-dependent strategies.
5. Promote promising candidates using criteria fixed before evaluating them. Run realistic portfolio simulation for execution, fees, funding, exposure, open PnL and risk; compare with simple baselines and cost stress.
6. Evaluate across development subperiods, symbols, regimes and reasonable parameter perturbations. Account for autocorrelation, overlapping labels, correlated assets and multiple comparisons. Report uncertainty, sample size and winner concentration.
7. Record successes, failures, rejected candidates and parameter trials. Avoid cherry-picking and report gross versus net results. Explain why an alpha was promoted, rejected or needs further evidence.

Do not repeatedly evaluate the final holdout or tune thresholds using it. Do not claim statistical significance from naive IID assumptions or claim a backtest proves future profitability. Research experiments may contain novel compositions and custom hypothesis-specific logic; reusable primitives belong in the SDK after review.

## 8. Missing capabilities and SDK extension workflow

Future research agents may request new features. Do not block a whole research campaign because one hypothesis is missing a capability; queue the request and continue independent experiments when possible.

Search for existing functionality first. If absent, create a structured request using the feature-request mechanism once implemented. Before that mechanism exists, create a clearly documented request artifact without pretending the queue is live.

Each request records:

- Stable ID, requested canonical function/module, requesting experiment and motivation.
- Mathematical specification, inputs and required data, output shape/units, parameters and edge cases.
- Causality, missing-data and warm-up policy, streaming needs and performance expectations.
- Acceptance examples, independent reference and required tests.
- Status (`pending`, `accepted`, `implemented`, `rejected`), implementation/version link and reviewer findings.

Role separation (logical roles, not necessarily separate running processes):

- **Researcher:** composes hypotheses and runs experiments; requests missing capabilities.
- **Library engineer:** implements reusable capabilities, tests and benchmarks; does not tune definitions for a candidate's PnL.
- **Reviewer:** independently checks definitions, causality, accounting, regression impact and research validity.

Use a separate reviewable change for SDK additions. Validate and register the capability before using it as a trusted dependency. Pin the SDK version in experiments. If an existing primitive has a bug, fix it with a regression test, invalidate affected caches/results, and rerun affected experiments as needed. Never quietly change a result's provenance.

Researchers must not silently change shared simulator execution/accounting, cost or risk models, numerical definitions, coverage masks, evaluation metrics or holdout policy. Legitimate improvements follow the engineering workflow and produce versioned, reviewable changes. If no independent reviewer is available, label the review as pending rather than claiming one occurred.

## 9. Performance and resource policy

Profile before optimizing. Existing benchmarks indicate data loading/preparation is the main bottleneck, not the native core. Avoid full-dataset materialization by default. Use column projection, bounded blocks, contiguous arrays, integer symbol IDs, streaming state and shared work where useful.

- Reuse decoded blocks and common features for parameter sweeps when safe. Prefer mathematically correct linear-time rolling algorithms where available.
- Cache keys must include data fingerprint, function version, parameters, causal availability, missing-data rules, universe/order and relevant execution context. Cache changes must not alter results.
- Use configurable memory, disk, process and thread limits. Avoid oversubscription, unbounded queues, uncontrolled parallel downloads and creating a complete cache of all possible features. Record cold/warm cache methodology honestly.
- Run small sanity checks before large sweeps. Estimate resource use; avoid redundant expensive runs. Preserve resumable progress and record failed work. Ask before exceeding an explicit budget or starting materially out-of-scope compute.
- Performance claims require measured workloads and reproducible commands. Report core-only and end-to-end throughput separately, plus peak RAM, hardware and data preparation costs.

## 10. Testing and acceptance gates

Every new public primitive needs meaningful tests and documented semantics. Test against hand-calculated examples and independent references; use established libraries only with matching definitions. Cover constant/empty inputs, zero denominators, extreme values, NaNs, warm-up, irregular coverage, symbol/time alignment, chunk boundaries and numerical stability as relevant.

For causal primitives, perturb future inputs and verify earlier outputs do not change. For streaming primitives, verify arbitrary chunk sizes and batch equivalence. For execution/accounting changes, use independent ledger comparisons and test reversals, partial fills, costs, funding, equity and risk constraints. Retain all prior ingestion and simulator regression tests.

For performance-sensitive code, provide a representative benchmark. For research evaluation, test known synthetic signals and null cases; verify split/purge boundaries and failure handling. Never weaken tolerances or remove validation solely to make tests pass.

A capability is complete only when its code, tests, docs, registry metadata (when applicable), runnable example and known limitations agree. A phase is complete only when its requested deliverables and acceptance gates are satisfied or outstanding blockers are explicitly reported.

## 11. Reproducibility and reporting

Use the experiment registry when available. Every experiment must have a durable ID and record hypothesis, parameters, candidate count, time split, universe, dataset fingerprint, code commit/dirty state, SDK and simulator versions, seed, feature definitions, costs, runtime, warnings, artifacts, metrics and final status. Persist failed and negative trials as well as successes. Use actual marked-to-market equity for portfolio performance and distinguish closed-position metrics.

Do not fabricate test results, benchmark numbers, source data, feature availability or research findings. Separate observed evidence from hypotheses and simulated assumptions. Make blockers, incomplete work and discretionary decisions visible to the owner. Report commands, output locations and any user action needed. Keep durable docs and experiment records in the repository or designated local data storage, not only in conversational context.

No secrets in source or logs. Use public historical sources and read-only data access by default. No live orders, exchange credentials or production deployment as part of research work.

## 12. SDK entry points

- Search using `uv run python -m quant functions`, `uv run python -m quant describe quant.ts.ema`, or `uv run python -m quant search "cross-sectional neutralization"`. Python equivalents live in `quant.registry`.
- Open the frozen dataset with `q.data.open()`, choose development intervals and projected fields with `q.data.select`, and stream coverage-aware blocks with `q.data.iter_blocks`.
- Compose registered causal expressions through `q.features.expression` and evaluate them in bounded blocks with `q.features.evaluate`. For small causal matrices, call the corresponding numerical primitive directly.
- Use `q.evaluate.forward_returns` and diagnostics with the research label type. Keep label arrays outside candidate inputs and feature caches. Use chronological splits and dependency-aware inference under [the research protocol](docs/RESEARCH_PROTOCOL.md).
- Register the hypothesis and configuration with `q.experiment.register` before analysis. Use `q.experiment.run` to persist failures and metrics. Convert exposure weights to signed USDT using observable equity, then bind causal targets with `q.simulator.targets` and replay with `q.simulator.run`.
- Check or queue missing capabilities with `q.requests`. Review requirements are in [the feature-request guide](docs/FEATURE_REQUESTS.md).
- Ordinary workflows keep the final holdout protected. Final evaluation requires the owner-authorized, frozen and recorded procedure, separate from development work.
- Researchers must leave shared ingestion eligibility, simulator execution/accounting, numerical definitions, risk and cost semantics, holdout gates and evaluation metrics to the reviewed engineering workflow.

See the [SDK guide](docs/SDK.md), [data contract](docs/DATA_CONTRACT.md), [causality contract](docs/CAUSALITY.md), and [benchmarks](docs/BENCHMARKS.md). Run `uv run python -m examples.research_sdk all` for the development examples.

## 13. Research execution

Submit predeclared development experiments through `q.research.submit`. A trusted local handler uses `verify_selection` to bind its source data and returns outcomes by experiment ID. Share causal expressions via `q.evaluate.screen` or `q.simulator.run_batch`, and use a prepared cache only when the disk reservation fits. Screen results are descriptive. Replay selected experiments in audit mode for full accounting artifacts. Keep callback state private and use frozen replay tokens for committed waves. Review the [Phase 4 execution guide](docs/RESEARCH_ENGINE.md) for resource limits, cancellation, and audit procedures.


## 14. Research Control Center

Phase 4.5 adds the [local Research Control Center](docs/CONTROL_CENTER.md) for asynchronous development submissions, execution, and outcome review. Start the full application with `uv run quant service start --build-ui` on the first build and `uv run quant service start` thereafter. CLI and browser submissions share the experiment registry. Use the returned hypothesis IDs and pick up changed outcomes with `uv run quant research inbox --unreviewed`. Acknowledge the result revision after reviewing it. Execution states remain separate from research verdicts.

The service uses trusted data-only handlers and the existing development selection, resource budgets, and block-major execution. Keep application changes local to this phase. Phase 5 discovery, final evaluation release, and live execution require separate owner authorization.
