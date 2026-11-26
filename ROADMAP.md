# Quant Research Platform Roadmap

## Project direction

Quant is a quantitative research platform for discovering, testing, rejecting, refining and combining trading hypotheses. The trusted data and accounting layers serve many strategies through a shared research SDK. Correctness, causality and reproducibility take priority over research convenience and speed. Negative trials are durable research results.

The owner authorizes phase transitions, holdout release and any real-capital activity. Phases 1 and 2 are established baselines. Phase 3 delivered the public SDK. Phase 4 delivers the high-throughput research engine and supersedes the earlier Phase 2.5 proposal.

## Completed milestones

| Phase | Milestone | Verification and limitations |
|---|---|---|
| 1 | Binance USD-M ingestion, audit and frozen crypto-only Liquid 50 | 517,720,813 five-second bars, 30.38 GB of ZSTD Parquet and 98.23% of planned post-listing slots for the 50 crypto contracts. The warehouse audit covered 1,264 partitions across the crypto and preserved TradFi data, with zero output audit errors. Two Liquid 50 partitions retain explicit source gaps. The universe was selected using October 26 through November 24, 2026 volume among then-active contracts. |
| 2 | C++20 linear futures simulator V1 | Existing Arrow reader, bounded NumPy blocks, C ABI, modeled execution, portfolio accounting, funding, marks, warnings and reports. Five reference strategies and a cost-stress replay ran June 10–16, 2026 UTC. The full development benchmark reported 65.8M observed bars/s in the lightweight native path and about 2.00M/s end to end. The working tree passed 25 automated tests during the roadmap review. |

Data coverage, manifests and tests remain the evidence for these baselines. Simulator V1 uses approximate historical contract filters and flat maintenance margin, includes explicit mark fallback, and flags unavailable funding inputs. The MMTUSDT source contains one funding record with an unusable settlement mark. First-pass benchmark reads had uncontrolled OS cache. Reproduce the runs using [README.md](README.md) and [BENCHMARK.md](BENCHMARK.md).

## Phase 3 · Research SDK

**Dependencies.** Owner authorization and the Phase 3 capability specification. Reuse the audited data, availability contract, native simulator and independent accounting tests.

**Delivered.** Aligned and resampled data, causal operators, versioned capability discovery, diagnostics and purged validation, native simulation with streamed targets, experiment tracking, bounded caches and the capability request queue. The [function catalog](docs/FUNCTION_CATALOG.json) and [benchmarks](docs/BENCHMARKS.md) provide the verified inventory and measurements.

**Remaining limits.** Streaming explicitly covers registered stateless and finite-lookback kernels plus recursive EMA and Wilder state. Several composed indicators, statistics and diagnostics use caller-bounded batch arrays. Catalog descriptions for some functions rely on shared module conventions. Benchmark filesystem cache state was uncontrolled. Linux execution and real holdout evaluation were not run.

**Acceptance criteria**

- The stable `quant` namespace wraps `marketdata` and `simulator`. The phase specification controls the exact primitive inventory. Public aliases delegate to canonical implementations, and reference strategies share any primitives migrated in this phase.
- Public time/symbol data contracts carry UTC timestamps, symbol order, availability, validity and units. Shape, axis and alignment errors fail explicitly. Traded, confirmed empty, missing, quarantined and pre-listing states stay distinguishable.
- Every delivered primitive has a signature, mathematical definition, units, shape, parameter constraints, warm-up, gap policy, numerical defaults, causality class and runnable example. Streaming functions have bounded state and match batch results under arbitrary chunking within documented tolerances. Batch-only functions are labeled.
- Generate `docs/FUNCTION_CATALOG.json` from the implemented registry and test its agreement with exports, signatures and documentation. Unsupported capabilities produce explicit errors. Diagnostics and validation keep forward labels, research-only statistics and tradable features separate.
- Experiment and cache support satisfy the authorized phase scope. Provenance captures the inputs needed to reproduce results. Cache keys encode data fingerprint, function version, parameters, availability and gap rules, universe and symbol order, and relevant execution context. Cache hits leave results invariant.
- Prior ingestion and simulator tests remain green. New public capabilities have independent numerical references and tests for future-input perturbation, gaps, alignment, chunking, and applicable edge cases. Measure hot-path changes on representative workloads.

## Phase 4 · Research execution infrastructure

**Delivered.** Independent native portfolios advance over shared chronological blocks, with versioned read-only prepared caches, common feature graphs, bounded waves and summary or audit outputs. SQLite retains queued jobs, attempts and outcomes under the existing experiment IDs. Development screening and deterministic recovery are exercised by the [campaign example](docs/RESEARCH_ENGINE.md).

**Verification.** The release passed 88 tests and recovered a development campaign with 108 outcomes. A matched active wave improved end-to-end throughput 8.22×. The warm lightweight full-development replay took 30.161 s using a prepared cache. Direct source preparation and cold builds remain the dominant first-run cost. Benchmarks were run on Windows. [Phase 4 measurements](docs/RESEARCH_BENCHMARKS.md)

**Dependencies.** Accepted SDK contracts, capability registry, reproducible experiments and stable simulator adapters.

**Acceptance criteria**

- Authorized development experiments run with bounded worker, memory, disk and thread use. Jobs can resume and persist failures. Multiple candidates may share decoded blocks and causal features with versioned cache identity.
- Logged benchmarks show identical single-run and scheduled outputs for matching inputs. Separate feed preparation, native execution and end-to-end work in throughput and memory reports.
- Feature requests can be tracked without blocking independent experiments. Record the specification, data and causality requirements, acceptance examples, status, implementation version and reviewer findings.
- Researcher, library engineer and reviewer responsibilities have explicit boundaries. Candidate code cannot silently replace trusted eligibility, execution, accounting, risk, cost or evaluation rules.

## Phase 4.5 · Research Control Center

**Delivered.** The detached local service adds atomic hypothesis and campaign submission, durable outcome review, live queue management, audit replay, and one resumable browser event stream. It uses the existing SQLite experiment registry and native execution. The React workstation includes the overview, queue, library, results, inbox, SDK requests, and system views.

**Verification.** The development acceptance campaign completed 100 screening trials and eight native trials after process termination and restart. One additional audit replay matched the original native accounting metrics. The [control center guide](docs/CONTROL_CENTER.md) records reproduction commands and links to production browser screenshots.

**Boundary.** Research specifications are predeclared and run only on development selections. Phase 5 automation remains owner-authorized.

## Phase 5 · Authorized automated alpha discovery

**Dependencies.** Explicit owner authorization, verified scheduler and resource limits, an accepted capability and experiment registry, and tested chronological evaluation boundaries.

**Acceptance criteria**

- Research agents register a plausible mechanism, falsifiable prediction, horizon, feature definitions, parameter budget, split, costs and rejection criterion before examining results. They search the registry first and queue missing capabilities while independent trials continue.
- Candidates progress from small semantic and coverage checks to predeclared diagnostics and portfolio simulation. Use conditional returns, event studies, IC or other diagnostics where the hypothesis calls for them. Record promotion and rejection reasons.
- Evaluation uses chronological development splits and walk-forward testing, with training-only fits, purging or embargo where needed, and appropriate handling of dependence, correlated assets and multiple comparisons. Tests include known synthetic signals and null cases.
- Preserve failed, rejected and negative trials as well as successful ones. Compare against baselines and cost stress, report gross and net results, and assess robustness across subperiods, symbols, regimes and parameter changes. An independent review remains pending until performed.

## Phase 6 · Research-driven extensions and multi-alpha portfolios

**Dependencies.** Reviewed candidate evidence, versioned SDK capabilities and a trusted experiment history.

**Acceptance criteria**

- Reusable capability additions follow a separate engineering and review workflow. Specification, reference tests, registry, example and version are complete before downstream use. Corrections invalidate affected caches and results, with reruns recorded when needed.
- Portfolio composition evaluates correlated exposures, concentration, capacity, turnover and cost interactions using the existing simulator. Document selection and weighting rules, compare portfolio and component baselines, and retain the evidence for rejected combinations.

## Phase 7 · Paper trading and any later live execution

**Dependencies.** Separate owner authorization for paper trading, reviewed research and simulation evidence, and an operational scope agreed with the owner. Live execution requires its own later authorization.

**Acceptance criteria**

- Paper results reconcile modeled orders, positions and PnL against observable execution, with gaps and cost differences explained. Risk, monitoring and operational controls have documented tests and review evidence before any live decision.

## Evaluation and engineering gates

- Development is `[2024-11-25, 2026-08-25)` UTC. Reserved evaluation is `[2026-08-25, 2026-11-25)` UTC. Infrastructure-only integrity and performance work may use the full range. Profitability diagnostics, candidate ranking and model fitting stay in development until authorized release.
- Any owner-authorized final evaluation fixes the strategy, parameters, universe, data fingerprint, code and cost model before evaluation. Record the release and results. The released period cannot subsequently serve as an untouched tuning benchmark.
- Preserve the reader's bar availability timestamp, normally bar start plus five seconds plus 100 ms. Feature availability inherits all input availability. Earlier features must be invariant to later input changes. Preserve data and universe provenance, including the frozen Liquid 50 selection bias.
- Record experiment identity, parameters, trials, splits, data, universe, versions and working tree state, costs, seed, runtime, warnings, artifacts, metrics and final status. Report marked-to-market portfolio performance separately from closed-reduction metrics.
- Reuse the C++20 core and C ABI. Profile and benchmark before optimizing, compare equivalent workloads, and record hardware, logging, memory, cache conditions and both native and end-to-end rates. Preserve prior validation and regression coverage. Keep datasets, generated caches and credentials out of source commits.

## Repository alignment review

| Finding | Current behavior and readiness implication |
|---|---|
| Publishing and merge history | `AGENTS.md` records the owner's direct-to-`main` workflow. Simulator V1 commit `7f05e72` reached `main` by direct fast-forward push, and GitHub then marked PR #1 merged. |
| SDK namespace and catalog | `quant` wraps the existing Arrow and native interfaces. The command-line catalog is generated from the implemented inventory. |
| Gap and warm-up policy | SDK EMA defaults to resetting on gaps. The V1 EMA declares its legacy carry mode and shares the SDK recurrence. Native execution retains its eligibility checks. |
| Holdout and signal provenance | Ordinary SDK research methods reject the reserved window. The explicit release path checks provenance and persists an audit receipt. Arbitrary raw arrays still rely on caller provenance. |
| Experiment and cache provenance | The SQLite experiment registry records outcomes, failure, specifications, data fingerprints, seed and Git state. Bounded decoded and feature caches have versioned identities. |
| Platform verification | The review verified Windows execution and found no checked-in CI workflow. Linux compatibility remains an engineering target whose evidence should be recorded when validated. |

## Readiness

The audited dataset, native simulator and SDK support development campaigns with bounded shared execution and durable outcomes. Any Phase 5 research agent requires separate authorization. The reserved strategy period remains untouched.
