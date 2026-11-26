# Quant Research Control Center

The control center runs predeclared development hypotheses in a detached local service. An accepted submission has a durable hypothesis ID and one experiment ID per trial. You can close the terminal and browser while the service runs. Review outcomes in the inbox when you return.

## Start and stop

Prerequisites are the locked Python environment, Node.js with npm for the first UI build, the audited market dataset, and a working C++20 compiler for native simulations. On Windows the existing optional Zig toolchain is sufficient.

```sh
uv sync --locked
uv pip install ziglang==0.15.2
uv run quant service start --build-ui
```

Open the URL from the JSON response, normally `http://127.0.0.1:8765/`. Later starts reuse the built assets.

```sh
uv run quant service start
uv run quant service status
uv run quant service stop
```

Stop prevents new claims and lets the current wave finish. The HTTP stop request returns an acknowledgment immediately. Run `quant service serve` in the foreground when supervising the process externally. After an interruption, start again on the same registry. Queued trials remain eligible and interrupted attempts are retained. Uncommitted work restarts from the beginning of its wave subject to the per-trial attempt limit.

The default home is `data/quant-control` with `research.sqlite`, `service.log`, and an artifact directory under it. The service and research command groups take `--home`, `--database`, and `--port` before their subcommands. Their environment equivalents are `QUANT_CONTROL_HOME`, `QUANT_CONTROL_DATABASE`, and `QUANT_CONTROL_PORT`. Use `service start --source PATH` to pick the local audited bar root. Set `--database` to an existing Phase 4 registry to share the same database. Experiment IDs, attempts, and outcomes remain in their existing tables. Standalone Phase 4 jobs have no hypothesis entry until registered through the hypothesis submission interface.

## Asynchronous CLI

Generate the documented **fixed infrastructure fixture**, or provide your own predeclared specification.

```sh
uv run python -m examples.control_campaign data/quant-control/hypotheses.json
uv run quant research submit data/quant-control/hypotheses.json
uv run quant research queue
uv run quant research status H-0001
uv run quant research inbox --unreviewed
uv run quant research results H-0001
uv run quant research acknowledge H-0001 --revision 215
```

Use the actual revision shown in the inbox when acknowledging. If a later outcome or verdict arrives, that newer revision remains unread. Acknowledgment without a revision reads the current result revision first. A hypothesis enters the inbox once all of its trials reach an outcome. Failed, cancelled, and capability-blocked outcomes are retained. A recorded research verdict creates a new reviewable revision. Completing work leaves the verdict unassigned until a reviewer records a verdict and note.

```sh
uv run quant service pause
uv run quant service budgets --cpu-limit 4 --memory-gib 2 --disk-gib 8
uv run quant service resume
uv run quant research priority H-0001 20
uv run quant research cancel H-0001
uv run quant research retry H-0001
uv run quant research verdict H-0001 inconclusive --note "Evidence is limited to an infrastructure fixture."
uv run quant research audit H-0005 EXPERIMENT_ID
```

Priorities run from −100 to 100. Pause affects the next claim. Running cancellations are cooperative at block boundaries and an active native call or artifact write can finish first. A shared wave keeps processing other candidates after a partial cancellation. Retry requeues cancelled or failed trials under the same experiment ID and preserves their attempts. Retry an upstream hypothesis first, then retry affected dependents once their prerequisite succeeds. After a missing SDK capability is implemented, restart with the reviewed SDK version and retry its blocked trial. The historical research verdict is preserved when a retry clears the current verdict.

Audit replay takes a completed native experiment ID and creates a linked hypothesis with the frozen selection and settings. It records a new experiment with full accounting artifacts. Audit replay records the executing code version separately from submission provenance.

## Submission format

A body may contain one hypothesis or an envelope with a `hypotheses` array and a campaign name. A campaign name identifies a durable campaign in the current registry. A hypothesis declares `name`, `family`, `mechanism`, `definition`, `prediction`, `stage`, `protocol`, and `candidates`. Optional fields are `key`, `parameters`, `capabilities`, `depends_on`, `priority`, and `max_attempts`. Dependency entries can use existing `H-…` IDs or keys in the same submission. A dependent waits for all prerequisite trials to complete. Identical resubmissions return the same IDs. Candidate configurations must be distinct within each hypothesis.

The protocol freezes `start`, `end`, ordered `universe`, and `dataset_fingerprint`. Include the `evaluation` object with the proposed split, diagnostic settings, and rejection criteria, and optional `seed`, `features`, and native `costs`. Compute the fingerprint with `q.data.fingerprint(selection)`. Handlers verify this identity against the selected source before processing it.

```json
{
  "kind": "screen",
  "parameters": {"fast": 8, "slow": 24, "normalize": true, "direction": 1}
}
```

The built-in trusted handlers are `screen`, `native`, and `audit`. EMA spans are convenient for the fixed example. Alternatively, supply `parameters.expressions` with `fast` and `slow` feature nodes. A node has a registered causal streaming function `name`, `units`, `inputs`, and an optional `parameters` object. Inputs are nested nodes or `"close"`. The two outputs must have matching units. Screening evaluates a signed spread with optional close normalization. Native simulation uses the signed spread with configurable `notional` in USDT, optional `direction`, and `settings` overrides for modeled costs and risk. The selected signal shapes use one close projection and preserve the batch engine's causal bar clocks. An owner can extend the trusted handler map in `quant/control_handlers.py` through the normal review workflow.

Screen options include `horizon`, `method`, `groups`, and `event_threshold`. Native protocols can request `prepared_cache`. For missing features, add a canonical `capabilities` entry with optional `streaming`, `causal`, and a matching structured `request` from the [feature-request guide](FEATURE_REQUESTS.md). The dispatcher records missing capabilities as blocked outcomes and lets independent work continue.

Ordinary requests pass the existing development gate. The control center exposes development selection, descriptive screening, and native replay. Audit replay uses the same development gate.

## Backtest Explorer

Open a completed native candidate from Alpha Results, hypothesis or trial details, or Research Inbox. The [Backtest Explorer guide](BACKTEST_EXPLORER.md) covers audited candles, reconciled positions, synchronized panels, and exact replay.

## Local API and event lifecycle

| Route | Purpose |
|---|---|
| `GET /api/snapshot` | Consistent hypotheses, campaigns, lifecycle cursor, and SDK requests with current service telemetry |
| `POST /api/hypotheses`, `POST /api/campaigns` | Atomic durable submission through the same path as the CLI |
| `GET /api/queue`, `GET /api/hypotheses/{id}` | Current queue and frozen research definition |
| `POST /api/queue/{operation}`, `POST /api/hypotheses/{id}/{operation}` | Priority, retry, cancel, acknowledge, or verdict |
| `GET /api/hypotheses/{id}/results`, `POST /api/hypotheses/{id}/audit` | Trial outcomes, attempts, artifacts, and audit replay |
| `GET /api/artifacts/{experiment}/{index}`, `GET /api/batches/{id}` | Recorded downloads and measured batch profile |
| `GET /api/requests`, `POST /api/requests`, `POST /api/requests/{id}/review` | Capability specifications and review transitions |
| `GET /api/inbox?unreviewed=true` | Unacknowledged revisions with result links |
| `GET /api/service`, `GET /api/resources` | Worker state, hardware, and sampled resources |
| `POST /api/service/settings`, `POST /api/service/stop` | Local dispatch controls |
| `GET /api/events?after=CURSOR` | SSE lifecycle patches and live telemetry |

The service binds to IPv4 loopback and serves the compiled UI from the same origin. Mutations use JSON and the `X-Quant-Client: control` header. The server validates the Host and Origin headers. The API accepts data-only specifications for trusted handlers. Keep the endpoint on the trusted local machine. Artifact downloads require an experiment reference and an existing published path under the service artifact root.

The browser opens one EventSource after its initial snapshot. SQLite commits lifecycle events alongside the relevant changes. Increasing event IDs come from the journal. `Last-Event-ID` takes precedence over the initial cursor on reconnect. A cursor ahead of the registry requests a fresh snapshot. The service sends state patches and recent event rows in bounded chunks. Progress, resource telemetry, and worker detail are sampled live and can be skipped during disconnection. A reconnect receives current telemetry. The browser batches patches before rendering and retains table filters and selections.

## Execution and resource model

One dispatcher holds the existing exclusive local lease. HTTP submissions wake it after committing changes. Existing Python and Phase 4 CLI submissions also notify it through a loopback datagram endpoint. An idle or paused dispatcher uses a blocking wake event, with one reconciliation check per minute to recover missed notifications. After each wave it claims the next eligible work until idle.

The highest-priority eligible trial selects the next wave. The scheduler groups compatible jobs by trusted handler, dataset fingerprint, ordered universe, time range, and applicable shared diagnostic or cache options. Screening shares decoded market blocks, feature nodes, and forward labels. Native jobs share the existing block-major replay. Audit jobs run singly. CPU and memory budgets apply to the next wave and native report and prepared-cache reservations use the existing disk checks. The disk budget applies to active reservations and prepared cache. Retained experiment artifacts and SQLite history accumulate on disk.

The overview reports finished trials separately from market-range processing. The queue distinguishes first wait, combined execution time, and total elapsed time in the detail panel. The results view keeps descriptive diagnostics and native reports distinct. Side-by-side comparison uses completed trials with the same handler, selection, and evaluation protocol. Cost differences remain visible. Robustness panels show recorded subperiod evidence. Research verdicts require separate review.

## Verification

```sh
uv run pytest -q
npm --prefix control-ui run build
uv run python -m examples.control_acceptance bench-data/control-acceptance-local --port 8768 --interrupt
node control-ui/tests/production.mjs http://127.0.0.1:8768 bench-data/control-ui
```

Use a fresh output directory for the acceptance command. The browser check runs against the built production site and requires Edge, or another installed Playwright channel selected with `QUANT_BROWSER_CHANNEL`. The acceptance command leaves the service running so the browser check can inspect the real results. Stop it with `quant service --home bench-data/control-acceptance-local --port 8768 stop`.

The Windows development acceptance run submitted six hypotheses in 0.746 seconds. Four hypotheses were still running and two were dependency-blocked at the first post-submission read. Terminating the process interrupted 100 attempts. Restarting recovered all 100 screening trials and dispatched all eight native trials automatically. A single shared screen wave processed 108,000 market cells and 10,800,000 candidate cells across 13 feature nodes and four workers. Audit replay preserved final equity, risk, net P&L, daily marked equity, and execution costs. The range was June 10, 2026 from 00:00 to 03:00 UTC on Liquid 50. This was an infrastructure fixture, with uncontrolled OS cache state.

Screenshots from the local backend are available for the [overview](control-center/overview.png), [queue](control-center/queue.png), [hypothesis detail](control-center/hypothesis.png), [result comparison](control-center/results.png), and [system view](control-center/system.png).
