# Author and submit trusted strategies

The version 1 strategy interface lives in **quant.strategy**. Author strategy modules under **research_strategies** and add the exported Strategy instance to **research_strategies/catalog.py**. Restart the local service to load reviewed changes. **quant research strategies** or **GET /api/strategies** lists loaded versions, implementation hashes, parameter schemas, input fields, capability requirements, and support for screening or portfolio feedback.

Registration is an explicit local code review step. A submission selects a loaded identifier and version. The HTTP service accepts JSON data and resolves only the local catalog. Parameters can't select a Python import, executable expression, or shell command. Keep old registered modules available when new versions must coexist with queued or archived experiments.

## Strategy contract

| Part | Contract |
|---|---|
| Strategy | Unique identifier, semver version, typed parameters, required SDK capabilities, raw field projection, and named callbacks. Include local helper source files in sources. |
| prepare(parameters) | Returns a Plan with registered causal feature expressions, a scheduled-bar warm-up, an optional decision cadence, and plot descriptors. The graph is resolved and frozen before queue submission. |
| initialize(parameters, symbols, rng) | Returns candidate-local state. The parameter mapping, ordered symbols, and NumPy RNG seeded from the experiment are supplied at initialization. Keep mutable state private to this object. |
| signal(state, block) | Optional independent diagnostic score. Return a float64-compatible time-by-symbol array. The evaluator keeps labels outside the callback. |
| targets(state, block) | Vectorized native pathway. Return targets or a Decision with targets and named observation arrays. Use trailing operators and causal state updates. |
| decide(state, row, portfolio) | Feedback native pathway. Receives one market row and the actual native ledger after the preceding booked tick. The current row's native fills become visible on a later decision. |
| Decision | Signed USDT targets and optional declared observations on the same axes. NaN leaves the current target unchanged. Zero requests flat. Positive and negative values request long and short exposure. |
| Plot | Name, label, units, pane, color, thresholds, and optional numeric-code labels. The audit records graph outputs and targets by default. Extra observations can describe regimes, entry and exit conditions, signals, or decision rationale. |

The projected Block retains UTC bar starts, availability, symbol order, units, and coverage. Native execution supports all audited bar fields in **quant.data.UNITS**, plus native mark and execution reference inputs. Screening supports the audited bar fields and requires a diagnostic signal. Use **quant.registry.describe** to check a feature's signature, units, gaps, and streaming capabilities. For an absent capability, use the feature-request workflow.

Decisions use a zero-based scheduled bar count from the original range start. The Plan's decision cadence defaults to the simulator rebalance interval and must divide it. A vector callback may compute every row. The driver passes only warmed, scheduled targets with usable coverage to the engine. The feedback callback runs on eligible decision rows after warm-up and receives read-only native positions and booked equity, including modeled fills and funding through the preceding tick. Its quantity is actual base quantity. Its position record includes the native average entry, realized P&L and costs. Same-tick fill feedback is outside this interface. Tight decision cadences carry more Python and native boundary cost than vectorized targets.

Warm-up is a minimum number of scheduled observations from the original start. Use kernel min-periods or explicit causal state rules for gap resets. Treat the full block as an execution optimization. For vectorized callbacks, a value at row t may depend only on rows through t and on state carried from prior blocks. Avoid future normalization, full-block statistics that mix later rows, unsnapshotted data, and process-global RNG or mutable state. Run future-input perturbation and arbitrary chunk-size tests before trusting a new strategy.

Use target and optional rationale traces for strategy intent. Use native orders, fills, and positions for execution. Scaling, partial exits, holding limits, and cooldowns can inspect booked quantities and clocks. In feedback strategies with sparse cadence, first observation of a fill can lag its actual booking. The shock and scaling examples make decisions each native tick and observe fills on the following tick.

## Submit a parameterization

```json
{
  "kind": "native",
  "strategy": {"id": "ema_crossover", "version": "1.0.0"},
  "parameters": {"fast": 10, "slow": 30, "notional": 300, "side": "long"}
}
```

Put one or more such candidates in the existing hypothesis envelope. The protocol sets the development range, ordered universe, canonical close-selection fingerprint, seed, evaluation plan, and native settings. For registered strategies, native settings belong in protocol costs. Each candidate freezes defaulted parameters, a graph with kernel versions, a receipt for its implementation and trusted runtime, dependency versions, fields, warm-up and cadence. Submissions that reuse a strategy version after the implementation changes require a new version. A changed or missing runtime fails a frozen job or replay explicitly. Run an older frozen version in its original environment when necessary.

```sh
uv run quant research strategies
uv run quant research submit hypotheses.json
uv run quant research queue
uv run quant research inbox --unreviewed
```

Candidates share one direct market scan and feature graph per compatible wave. Distinct parameter sets can share identical graph nodes. The existing prepared cache covers close, volume, execution reference and mark. Requests for an extended projection with the prepared-cache option are rejected explicitly. Use the direct scan for cross-asset projections and flow fields. The scheduler and framework budgets cover bounded input, feature, native and trace buffers. Trusted callback authors are responsible for bounding their own state and scratch allocations.

## Reference modules

- **ema_crossover** uses two recursive SDK EMA features with long, short and both-side targets.
- **shock_reversal** fades a short-horizon price shock and limits holding using the filled book and cooldown.
- **flow_imbalance** uses aggressive buy and sell quote flow with a causal activity filter and continuous targets.
- **cross_sectional_momentum** ranks trailing return on each cross-section and produces signed portfolio targets.
- **stateful_scaling** sizes from marked portfolio equity and uses observed quantity to confirm pyramids and partial reductions.

These are fixed demonstrations. The fixture registers native and screening waves, checks shared computations, requests five detailed replays, and compares Explorer values against recorded traces and native reports.

```sh
uv run quant service --home bench-data/strategy-acceptance --port 8781 start
uv run python -m examples.strategy_acceptance --port 8781 --out bench-data/strategy-acceptance
node control-ui/tests/strategies.mjs http://127.0.0.1:8781 bench-data/strategy-acceptance/acceptance.json
```

Open a completed native candidate and generate an exact detailed replay in Backtest Explorer. Native accounting has to agree before attachment. The registered series are read from the recorded audit artifact with original timestamps and availability. Numeric rationale codes appear beside the native reason on fills when the requesting decision and target can be matched. Archived detailed traces remain readable when their implementation is no longer available for a replay.

The registered strategy interface is ready for predeclared development campaigns. The owner still authorizes the transition to discovery and any final evaluation.


## Measured development acceptance

The Windows acceptance used four symbols over the UTC interval 2026-06-10 00:00–00:30. The persistent API accepted ten hypotheses in 1.056 seconds. Both the eight-trial native wave and the eight-trial screening wave shared seven unique feature nodes across 1,440 market cells and 11,520 candidate cells. Five additional queued audits reproduced native accounting and recorded feature and decision values. The screen and native profiles measured 0.201 s and 0.669 s wall time respectively with four configured workers.

The matched legacy vector benchmark used the same 12 EMA candidates, four symbols, four workers, 2 GiB framework budget, summary logging, and a direct scan over 2026-06-10 00:00–03:00 UTC. All terminal accounting records matched. OS page-cache state was uncontrolled. Median wall time across passes two through four was 0.322 s on the Phase 4.6 baseline and 0.287 s after integration, for approximately 26.8k and 30.1k observed bars/s respectively. The 103,680 native candidate cells per pass spent a median 0.0124 s and 0.0100 s in summed native worker time. Median data preparation was 0.0986 s and 0.0873 s. Peak process RSS sampled every 10 ms was 435.7 MiB and 443.1 MiB. Windows reported an Intel Family 6 Model 154 CPU and 16 logical CPUs. These timings describe this workload and cache state.

Run **uv run python -m examples.strategy_benchmark bench-data/strategy-benchmark.json** and retain the JSON when comparing revisions.
