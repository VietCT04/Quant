"""Block-major research execution over the existing native portfolio engine."""

from concurrent.futures import ThreadPoolExecutor
from collections import defaultdict
from copy import copy
from dataclasses import asdict, dataclass, field, replace
import hashlib
from pathlib import Path
from time import perf_counter
from types import MappingProxyType
import re
import shutil

import numpy as np

from marketdata.model import DAY, load_json, write_json
from simulator.feed import MarketDataFeed, order_rules
from simulator.native import Engine, OrderSettings
from simulator.reporting import Logs, performance, record

from . import features
from ._core import array
from ._prepared import prepared
from ._resources import hardware, worker_count
from .data import UNITS, Block, Selection, fingerprint
from .validation import _check

MODES = {"benchmark": 0, "summary": 2, "standard": 1, "audit": 1}
FIELDS = {
    "close": (0, "USDT/base"),
    "execution": (1, "USDT/base"),
    "volume": (2, "base"),
    "mark": (3, "USDT/base"),
}


@dataclass(frozen=True)
class Candidate:
    """Independent portfolio specification and causal target callback or static allocations."""

    name: str
    decision: object = None
    expressions: dict = field(default_factory=dict)
    config: object = None
    allocation: tuple | None = None
    fields: tuple = ()
    feedback: bool = False
    trace_width: int = 0
    warmup_bars: int = 0
    feature_periods: dict = field(default_factory=dict)


@dataclass
class BatchResult:
    """Successful runs, isolated per-candidate failures, and shared execution measurements."""

    results: dict
    failures: dict
    profile: dict


class _DecisionFrame:
    """A callback-local namespace over immutable shared arrays."""

    def __init__(self, source, values, units):
        self._source = source
        self.values = MappingProxyType(values)
        self.units = MappingProxyType(units)

    def __getattr__(self, name):
        return getattr(self._source, name)


def _validate(candidates):
    names = set()
    for c in candidates:
        if (
            not isinstance(c, Candidate)
            or not re.fullmatch(r"[A-Za-z0-9_-]+", c.name)
            or c.name in names
            or (c.decision is None) == (c.allocation is None)
            or c.decision is not None
            and not callable(c.decision)
            or c.allocation is not None and (c.expressions or c.feedback or c.fields)
            or set(c.fields) - (UNITS.keys() | FIELDS.keys())
            or type(c.trace_width) is not int or c.trace_width < 0
        ):
            raise ValueError(
                "Candidates need unique safe names and a callback or static allocation"
            )
        names.add(c.name)


class _Trial:
    def __init__(self, candidate, feed, rules, out, mode):
        from .simulator import _settings

        self.candidate = candidate
        self.directory = out / candidate.name
        if (self.directory / "report.json").exists():
            raise FileExistsError(f"Report already exists for {candidate.name}")
        config = _settings(candidate.config, feed)
        if (config.interval, config.lag) != (feed.step, feed.lag):
            raise ValueError(
                "Simulator configuration must match the feed interval and lag"
            )
        self.settings = replace(
            config,
            logging=MODES[mode],
            snapshot_ticks=1 if mode == "audit" and not isinstance(config, OrderSettings) else config.snapshot_ticks,
        )
        if (
            candidate.allocation is not None
            and not np.isfinite(candidate.allocation).all()
        ):
            raise ValueError("Static allocations must be finite")
        self.engine = Engine(self.settings, rules, candidate.allocation)
        self.daily = {}
        self.portfolio_clock = None
        self.initial_equity = config.initial_wallet
        self.mode = mode
        self.logs = (
            Logs(self.directory, feed.symbols) if self.settings.logging == 1 else None
        )
        self.ordered = None
        if isinstance(config, OrderSettings):
            from ._order_trial import OrderTrial
            self.ordered = OrderTrial(self, feed)

    def advance(self, starts, market, status, funding, block):
        if self.ordered is not None:
            return self.ordered.advance(starts, market, status, funding, block)
        if self.candidate.feedback:
            return self._advance_feedback(starts, market, status, funding, block)
        decision = self.candidate.decision
        clock = perf_counter()
        target = None
        if decision is not None:
            value = array(decision(block))
            if value.shape != status.shape or np.isinf(value).any():
                raise ValueError("Callbacks must return signed USDT on the block axes")
            target = np.where(status <= 1, value, np.nan)
        signal_seconds = perf_counter() - clock
        clock = perf_counter()
        self.engine.run_shared(starts, market, status, funding, targets=target)
        engine_seconds = perf_counter() - clock
        clock = perf_counter()
        if self.logs is not None:
            for key, records in self._logs():
                self.logs.append(key, records)
        elif self.mode == "summary":
            self._record_daily()
        return signal_seconds, engine_seconds, perf_counter() - clock

    def _logs(self):
        orders = self.engine.orders()
        return (
            ("orders", orders), ("fills", orders[orders["filled"] != 0]),
            ("equity", self.engine.curve()), ("targets", self.engine.targets()),
            ("funding", self.engine.cashflows()),
        )

    def _record_daily(self):
        curve = self.engine.curve()
        for ms, equity in zip(curve["ms"], curve["equity"]):
            self.daily[int((ms - self.settings.lag - 1) // DAY)] = float(equity)

    def _advance_feedback(self, starts, market, status, funding, block):
        from .strategy import Portfolio

        decision = self.candidate.decision
        signal_seconds = engine_seconds = logging_seconds = 0.0
        previous = funding_cursor = 0
        logs = {key: [] for key in ("orders", "fills", "equity", "targets", "funding")}

        def run(stop, value=None):
            nonlocal previous, funding_cursor, engine_seconds, logging_seconds
            clock = perf_counter()
            end_clock = int(block.available_at_ms[stop - 1])
            funding_stop = np.searchsorted(funding["ms"], end_clock, side="right")
            self.engine.run_shared(starts[previous:stop], market[previous:stop], status[previous:stop],
                                   funding[funding_cursor:funding_stop], targets=value)
            previous, funding_cursor = stop, funding_stop
            self.portfolio_clock = end_clock
            engine_seconds += perf_counter() - clock
            clock = perf_counter()
            if self.logs is not None:
                for key, records in self._logs():
                    logs[key].append(records)
            elif self.mode == "summary":
                self._record_daily()
            logging_seconds += perf_counter() - clock

        for row in decision.feedback_rows(block):
            if row > previous:
                run(row)
            clock = perf_counter()
            positions = self.engine.positions()
            positions.flags.writeable = False
            equity = self.initial_equity if self.portfolio_clock is None else float(self.engine.equity()["equity"])
            value = decision.step(block, row, Portfolio(self.portfolio_clock, positions, equity))
            signal_seconds += perf_counter() - clock
            run(row + 1, value)
        if previous < len(starts):
            run(len(starts))
        clock = perf_counter()
        decision.finish_feedback(block)
        if self.logs is not None:
            for key, pieces in logs.items():
                self.logs.append(key, np.concatenate(pieces) if len(pieces) > 1 else pieces[0])
        return signal_seconds, engine_seconds, logging_seconds + perf_counter() - clock

    def finish(self, feed, *, archive=None):
        from .simulator import Result

        order_report = self.ordered.finish() if self.ordered is not None else None
        end = record(self.engine.equity())
        risk = record(self.engine.risk())
        position = self.engine.positions()
        if order_report is not None:
            self.logs = None
        if self.logs is not None:
            self.logs.append("positions", position)
            self.logs.close(True)
            self.logs = None
        if self.mode == "benchmark":
            # Explicitly infrastructure-only output, since daily research statistics are disabled.
            report = {
                "end": end,
                "risk": risk,
                "orders": int(position["orders"].sum()),
                "fills": int(position["fills"].sum()),
                "mode": self.mode,
            }
        else:
            report = performance(
                self.directory,
                self.settings,
                end,
                position,
                feed,
                daily=self.daily if self.mode == "summary" or order_report is not None else None,
            )
            report |= {
                "risk": risk,
                "settings": asdict(self.settings),
                "mode": self.mode,
                "execution_model": "legacy_targets_v1",
            }
            if order_report is not None:
                report |= order_report | {"roi": report["total_return"]}
        destination = archive
        if destination is None:
            self.directory.mkdir(parents=True, exist_ok=True)
            destination = self.directory / "report.json"
            write_json(destination, report)
        return Result(self.directory, report, destination)

    def close(self):
        try:
            if self.ordered is not None:
                self.ordered.close()
            elif self.logs is not None:
                self.logs.close(False)
        finally:
            self.engine.close()


def _report_reservation(data, count, mode):
    ticks = (data.end - data.start) // data.dataset.interval_ms
    per = 131072 + len(data.symbols) * 8192 + ((data.end - data.start) // DAY) * 256
    if mode in {"standard", "audit"}:
        # Maximum one order, fill and target per symbol/tick, plus audit equity and row-group overhead.
        per += ticks * (len(data.symbols) * 768 + 1024)
    return per * count


def run_batch(
    data,
    strategies,
    *,
    out,
    workers="auto",
    cpu_limit=None,
    memory_budget_gib=4,
    disk_budget_gib=8,
    logging="summary",
    batch_size=64,
    block_steps=4096,
    cache_directory=None,
    cancel=None,
    report_storage="auto",
    resume_token=None,
):
    """Advance bounded waves of independent native portfolios over one immutable chronological market stream."""
    from .simulator import _feed

    removed = getattr(cancel, "cancelled", None)
    progress = getattr(cancel, "progress", None)
    if not isinstance(data, Selection):
        raise TypeError("Batch execution requires an SDK market Selection")
    _check(data.start, data.end)
    candidates = list(strategies)
    _validate(candidates)
    if (
        logging not in MODES
        or not candidates
        or type(batch_size) is not int
        or batch_size < 1
        or type(block_steps) is not int
        or block_steps < 1
        or not np.isfinite(memory_budget_gib)
        or not np.isfinite(disk_budget_gib)
        or min(memory_budget_gib, disk_budget_gib) <= 0
    ):
        raise ValueError(
            "Invalid wave, resource budget, strategy count or logging mode"
        )
    if report_storage == "auto":
        report_storage = "per-run" if logging in {"standard", "audit"} else "batch"
    if (
        report_storage not in {"per-run", "batch"}
        or report_storage == "batch"
        and logging in {"standard", "audit"}
    ):
        raise ValueError("Full logs require per-run reports")
    if resume_token is not None and (
        report_storage != "batch"
        or not isinstance(resume_token, str)
        or not resume_token
    ):
        raise ValueError(
            "Resuming requires consolidated reports and a nonempty frozen strategy token"
        )
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    if cache_directory is not None:
        cache_path = Path(cache_directory).resolve()
        if (
            cache_path == out.resolve()
            or cache_path in out.resolve().parents
            or out.resolve() in cache_path.parents
        ):
            raise ValueError("Keep cache and output in separate directories")
    budget = int(disk_budget_gib * (1 << 30))
    reserved = _report_reservation(data, len(candidates), logging)
    if logging == "audit":
        ticks = (data.end - data.start) // data.dataset.interval_ms
        reserved += ticks * len(data.symbols) * sum(128 + 8 * c.trace_width for c in candidates if c.trace_width)
    already_written = sum(p.stat().st_size for p in out.rglob("*") if p.is_file())
    if already_written + reserved > budget or reserved > shutil.disk_usage(out).free:
        raise MemoryError("Report reservation exceeds the available disk budget")
    caches = budget - reserved - already_written
    if cache_directory is not None:
        cache_bytes = sum(
            p.stat().st_size for p in Path(cache_directory).rglob("*") if p.is_file()
        )
        if cache_bytes > caches:
            raise MemoryError(
                "Existing cache and report reservation exceed the combined disk budget"
            )
    memory_cap = min(int(memory_budget_gib * (1 << 30)), hardware()["available_bytes"])
    n = len(data.symbols)
    worker_limit = worker_count(workers, cpu_limit=cpu_limit, tasks=len(candidates))
    if workers == "auto" and len(candidates) < 32:
        worker_limit = min(worker_limit, 4)
    results, failures = {}, {}
    profile = dict(
        workers=worker_limit,
        cache_reads=0,
        cache_builds=0,
        market_blocks=0,
        resumed_results=0,
        feature_nodes=0,
        observed_bars=0,
        scheduled_cells=0,
        native_candidate_cells=0,
        market_seconds=0.0,
        feature_seconds=0.0,
        signals_worker_seconds=0.0,
        native_worker_seconds=0.0,
        logging_worker_seconds=0.0,
        step_wall_seconds=0.0,
        finalization_seconds=0.0,
        disk_reservation=reserved,
    )
    begin = perf_counter()
    pointer = 0
    models = {(isinstance(c.config, OrderSettings), c.warmup_bars) for c in candidates}
    if len(models) != 1:
        raise ValueError("Batch only matching execution models and pre-warm-up requirements")
    ordered, warmup = models.pop()
    if ordered:
        from .validation import warmup_start
        warm_start = warmup_start(data.start, warmup, data.dataset.interval_ms)
        template = MarketDataFeed(data.dataset, data.symbols, warm_start, data.end, block_steps=block_steps,
                                  completed_clock=True, trading_start=data.start)
        rules = np.zeros((len(data.symbols), 2))
    else:
        template = _feed(data, block_steps)
        rules = order_rules(template.dataset, template.symbols)
    data_identity = fingerprint(data) if resume_token is not None else None
    with ThreadPoolExecutor(max_workers=worker_limit) as pool:
        while pointer < len(candidates):
            if cancel is not None and cancel.is_set():
                failures.update({c.name: "cancelled" for c in candidates[pointer:]})
                break
            wave = candidates[pointer : pointer + batch_size]
            # Budget for the shared input block, retained feature outputs and per-worker target/log arrays.
            while True:
                graph = features.plan(
                    {
                        f"{i}:{name}": node
                        for i, c in enumerate(wave)
                        for name, node in c.expressions.items()
                    }
                )
                live = min(worker_limit, len(wave))
                extra_fields = tuple(sorted(({name for c in wave for name in c.fields} - FIELDS.keys()) | ({"open", "quote_volume"} if ordered else set())))
                trace_bytes = 8 * max((c.trace_width for c in wave), default=0) if logging == "audit" else 0
                per_cell = (
                    128 + 24 * len(extra_fields) + 32 * len(graph)
                    + live * ((1024 if logging in {"standard", "audit"} else 48) + 3 * trace_bytes)
                )
                persistent = 16384 * len(wave) * n
                if memory_cap - persistent >= n * per_cell:
                    break
                if len(wave) == 1:
                    raise MemoryError("Insufficient memory for one portfolio block")
                wave = wave[: max(1, len(wave) // 2)]
            steps = max(
                1, min(block_steps, (memory_cap - persistent) // (n * per_cell))
            )
            feed = copy(template)
            feed.block_steps = steps
            feed.mark_missing_slots = np.zeros_like(template.mark_missing_slots)
            feed.status_counts = np.zeros_like(template.status_counts)
            feed.profile = dict.fromkeys(template.profile, 0.0)
            feed.read_threads = cpu_limit is None
            archive = None
            specification = None
            if report_storage == "batch":
                identity = hashlib.sha256(
                    repr([c.name for c in wave]).encode()
                ).hexdigest()[:16]
                archive = out / f"wave-{pointer:06d}-{identity}.json"
                if resume_token is not None:
                    specification = {
                        "data": data_identity,
                        "resume_token": resume_token,
                        "mode": logging,
                        "strategies": hashlib.sha256(
                            repr(
                                [
                                    (
                                        c.name,
                                        c.config,
                                        c.allocation,
                                        sorted(c.expressions.items()),
                                        *((c.fields, c.feedback) if c.fields or c.feedback else ()),
                                    )
                                    for c in wave
                                ]
                            ).encode()
                        ).hexdigest(),
                    }
                if archive.exists():
                    previous = load_json(archive)
                    if (
                        specification is None
                        or previous.get("specification") != specification
                    ):
                        raise FileExistsError(
                            f"Wave already published with incompatible resume identity: {archive}"
                        )
                    from .simulator import Result

                    results.update(
                        {
                            key: Result(out / key, value, archive)
                            for key, value in previous["results"].items()
                        }
                    )
                    failures.update(previous["failures"])
                    profile["resumed_results"] += len(previous["results"])
                    pointer += len(wave)
                    continue
            trials = {}
            expressions = {}
            sampled = defaultdict(dict)
            keys = {}
            feature_errors = {}

            def feature_failed(node, error):
                feature_errors[node] = f"{type(error).__name__}: {error}"

            for c in wave:
                try:
                    trials[c.name] = _Trial(c, feed, rules, out, logging)
                    keys[c.name] = dict(c.expressions)
                    for name, node in c.expressions.items():
                        period = c.feature_periods.get(name, 1)
                        key = (node, period)
                        if key not in expressions:
                            alias = expressions[key] = f"feature_{len(expressions)}"
                            sampled[period][alias] = node
                except Exception as error:
                    failures[c.name] = f"{type(error).__name__}: {error}"
            profile["feature_nodes"] += len(graph)
            clock = perf_counter()
            # Extended fields share the native feed's projected Parquet scan.
            source = iter(feed.blocks(shared=True, projected=extra_fields) if extra_fields
                          else prepared(data, feed, cache_directory, caches, profile, cancel))
            base = None
            current = None

            def projected():
                nonlocal base, current, clock
                while trials and (cancel is None or not cancel.is_set()):
                    if removed is not None:
                        for name in trials.keys() & removed():
                            failures[name] = "cancelled"
                            trials.pop(name).close()
                        if not trials:
                            break
                    clock = perf_counter()
                    current = next(source, None)
                    profile["market_seconds"] += perf_counter() - clock
                    if current is None:
                        return
                    starts, market, status = current[:3]
                    for arr in (starts, market, status):
                        arr.flags.writeable = False
                    missing = status > 1
                    any_missing = missing.any()
                    values = {}
                    for f, (idx, unit) in FIELDS.items():
                        x = market[:, :, idx]
                        if any_missing:
                            x = np.where(missing, np.nan, x)
                        x.flags.writeable = False
                        values[f] = x
                    if extra_fields:
                        for name, projection in current[3].items():
                            projection[missing] = np.nan
                            projection.flags.writeable = False
                            values[name] = projection
                    available = starts + feed.step + feed.lag
                    available.flags.writeable = False
                    clock = perf_counter()
                    base = Block(
                        starts,
                        feed.symbols,
                        available,
                        status,
                        values,
                        feed.step,
                        {f: unit for f, (idx, unit) in FIELDS.items()} | {f: UNITS[f] for f in extra_fields},
                        ("prepared_native",),
                    )
                    yield base

            try:
                if all(c.allocation is not None for c in wave):

                    def static_inputs():
                        nonlocal current, clock
                        for current in source:
                            if cancel is not None and cancel.is_set():
                                return
                            profile["market_seconds"] += perf_counter() - clock
                            clock = perf_counter()
                            yield None
                            clock = perf_counter()

                    blocks = static_inputs()
                elif 1 in sampled:
                    blocks = features.evaluate(
                        projected(),
                        sampled[1],
                        memory_bytes=max(1, memory_cap - persistent),
                        on_error=feature_failed,
                    )
                else:
                    blocks = projected()
                from ._timeframes import CompletedFeatures
                if sampled:
                    biggest = max(sampled)
                    if biggest * n * (128 + 24 * len(extra_fields)) > memory_cap - persistent:
                        raise MemoryError("Higher timeframe aggregation exceeds the configured memory budget")
                aligned_features = CompletedFeatures(sampled, feed.step, max(1, memory_cap - persistent), feature_failed)
                for feature_block in blocks:
                    higher = aligned_features.advance(base) if aligned_features.groups else {}
                    for name in tuple(trials):
                        blocked = next(
                            (
                                feature_errors[node]
                                for node in keys[name].values()
                                if node in feature_errors
                            ),
                            None,
                        )
                        if blocked is not None:
                            failures[name] = blocked
                            trials.pop(name).close()
                    if not trials:
                        break
                    profile["feature_seconds"] += perf_counter() - clock
                    starts, market, status = current[:3]
                    profile["market_blocks"] += 1
                    profile["scheduled_cells"] += status.size
                    profile["observed_bars"] += int(np.count_nonzero(status <= 1))
                    funding = feed.funding_through(starts[-1] + feed.step + feed.lag - int(ordered))

                    def advance(item):
                        name, trial = item
                        try:
                            block = None
                            if trial.candidate.decision is not None:
                                values = dict(base.values)
                                units = dict(base.units)
                                for alias, node in keys[name].items():
                                    period = trial.candidate.feature_periods.get(alias, 1)
                                    values[alias] = (feature_block.values if period == 1 else higher)[expressions[node, period]]
                                    units[alias] = node.units
                                block = _DecisionFrame(base, values, units)
                            timing = trial.advance(
                                starts,
                                market,
                                status,
                                funding,
                                block,
                            )
                            return name, timing, None
                        except Exception as error:
                            return name, None, f"{type(error).__name__}: {error}"

                    tick = perf_counter()
                    items = tuple(trials.items())
                    for name, timing, error in (
                        pool.map(advance, items)
                        if worker_limit > 1 and len(items) > 1
                        else map(advance, items)
                    ):
                        if error is not None:
                            failures[name] = error
                            trials.pop(name).close()
                        else:
                            profile["native_candidate_cells"] += status.size
                            for key, val in zip(
                                (
                                    "signals_worker_seconds",
                                    "native_worker_seconds",
                                    "logging_worker_seconds",
                                ),
                                timing,
                            ):
                                profile[key] += val
                    profile["step_wall_seconds"] += perf_counter() - tick
                    if progress is not None:
                        progress(profile, starts[-1] + feed.step)
                    if not trials:
                        break
                tick = perf_counter()
                completed = {}
                for name, trial in trials.items():
                    if cancel is not None and cancel.is_set():
                        failures[name] = "cancelled"
                    else:
                        try:
                            completed[name] = trial.finish(feed, archive=archive)
                        except Exception as error:
                            failures[name] = f"{type(error).__name__}: {error}"
                if (
                    archive is not None
                    and completed
                    and (cancel is None or not cancel.is_set())
                ):
                    write_json(
                        archive,
                        {
                            "specification": specification,
                            "results": {
                                key: value.report for key, value in completed.items()
                            },
                            "failures": {
                                c.name: failures[c.name]
                                for c in wave
                                if c.name in failures
                            },
                        },
                    )
                results.update(completed)
                profile["finalization_seconds"] += perf_counter() - tick
            finally:
                source.close()
                for trial in trials.values():
                    trial.close()
            for name, elapsed in feed.profile.items():
                profile[name] = profile.get(name, 0.0) + elapsed
            pointer += len(wave)
    profile["wall_seconds"] = perf_counter() - begin
    return BatchResult(results, failures, profile)
