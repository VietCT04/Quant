"""Version 1 contract for explicitly registered local research strategies."""

from dataclasses import asdict, dataclass, field, replace
from functools import lru_cache
from hashlib import sha256
import inspect
from importlib.metadata import version
import json
from pathlib import Path
import re
from types import MappingProxyType
from typing import Callable

import numpy as np

from simulator.reporting import Logs

from . import data, features, registry
from .validation import HOLDOUT_START

API_VERSION = 1
_NAME = re.compile(r"[a-z][a-z0-9_]{0,63}\Z")
_KINDS = {"integer": int, "number": (float, int), "boolean": bool,
          "string": str, "array": list, "object": dict}
CLOCK_COLUMNS = {"ms", "available_ms", "symbol"}
ROOT = Path(__file__).resolve().parents[1]


@dataclass(frozen=True)
class Parameter:
    """Data-only parameter rule. A required value has no default."""
    kind: str
    default: object = None
    minimum: float | None = None
    maximum: float | None = None
    choices: tuple = ()

    def check(self, name, value):
        expected = _KINDS[self.kind]
        typ = type(value)
        if typ not in (expected if isinstance(expected, tuple) else (expected,)):
            raise ValueError(f"Parameter {name} requires {self.kind}")
        if typ in (int, float) and (
            typ is float and not np.isfinite(value)
            or self.minimum is not None and value < self.minimum
            or self.maximum is not None and value > self.maximum
        ):
            raise ValueError(f"Parameter {name} is outside its allowed range")
        if self.choices and value not in self.choices:
            raise ValueError(f"Parameter {name} must be one of {self.choices}")
        return value


@dataclass(frozen=True)
class Plot:
    """Description of a float64 strategy observation. Labels map optional numeric reason codes."""
    name: str
    label: str
    units: str
    pane: str = "signal"
    color: str = "#7ce3b8"
    thresholds: tuple = ()
    labels: dict = field(default_factory=dict)


@dataclass(frozen=True)
class Plan:
    """Parameter-dependent stream graph, warm-up and research display contract."""
    features: dict = field(default_factory=dict)
    warmup_bars: int = 0
    decision_ticks: int | None = None
    plots: tuple[Plot, ...] = ()


@dataclass(frozen=True)
class Decision:
    """Targets in signed USDT and optional series on the same axes. NaN leaves the target unchanged."""
    targets: object
    series: dict = field(default_factory=dict)


@dataclass(frozen=True)
class Portfolio:
    """Read-only native position ledger from the preceding completed tick."""
    clock_ms: int | None
    positions: np.ndarray
    equity: float


@dataclass(frozen=True)
class Strategy:
    """Trusted callbacks. Use exactly one of targets or decide for native simulation."""
    id: str
    version: str
    parameters: dict[str, Parameter]
    fields: tuple[str, ...]
    prepare: Callable
    initialize: Callable
    signal: Callable | None = None
    targets: Callable | None = None
    decide: Callable | None = None
    capabilities: tuple[str, ...] = ()
    sources: tuple[Path, ...] = ()


def _source_hash(paths):
    paths = tuple(sorted(set(paths)))
    stats = (path.stat() for path in paths)
    return _hash_sources(paths, tuple((item.st_mtime_ns, item.st_size) for item in stats))


@lru_cache(maxsize=96)
def _hash_sources(paths, revision):
    digest = sha256()
    for path in paths:
        relative = path.relative_to(ROOT).as_posix().encode()
        payload = path.read_bytes().replace(b"\r\n", b"\n")
        digest.update(relative + b"\0" + sha256(payload).digest())
    return digest.hexdigest()


_RUNTIME_MODULES = (
    "quant/_batch.py", "quant/_core.py", "quant/_prepared.py", "quant/_screen.py",
    "quant/control_handlers.py", "quant/strategy.py", "quant/evaluate.py", "quant/features.py",
    "quant/data.py", "quant/registry.py", "quant/validation.py", "quant/simulator.py",
    "quant/cs.py", "quant/math.py", "quant/ts.py", "quant/indicators.py", "quant/stats.py",
    "quant/volatility.py", "quant/microstructure.py", "quant/portfolio.py", "quant/risk.py",
    "quant/signal.py", "quant/performance.py", "marketdata/reader.py", "marketdata/model.py", "marketdata/aggregate.py",
    "simulator/feed.py", "simulator/native.py", "simulator/reporting.py", "simulator/core.cpp", "uv.lock",
)
_RUNTIME_PATHS = tuple(path for name in _RUNTIME_MODULES if (path := ROOT / name).is_file())
_LOADED_RUNTIME = _source_hash(_RUNTIME_PATHS)
_DEPENDENCIES = {name: version(name) for name in ('numpy', 'pyarrow', 'polars', 'scipy', 'statsmodels')}


@lru_cache(maxsize=1)
def _bindings():
    from research_strategies.catalog import STRATEGIES

    result = {}
    for strategy in STRATEGIES:
        key = (strategy.id, strategy.version)
        if (
            not _NAME.fullmatch(strategy.id)
            or not re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", strategy.version)
            or key in result or (strategy.targets is None) == (strategy.decide is None)
            or not strategy.fields or set(strategy.fields) - (data.UNITS.keys() | {"mark", "execution"})
            or any(not _NAME.fullmatch(name) or rule.kind not in _KINDS for name, rule in strategy.parameters.items())
        ):
            raise ValueError(f"Invalid or duplicate trusted strategy binding: {key}")
        callables = (strategy.prepare, strategy.initialize, strategy.signal, strategy.targets, strategy.decide)
        paths = tuple(Path(inspect.getsourcefile(fn)).resolve() for fn in callables if fn is not None)
        paths += tuple(Path(path).resolve() for path in strategy.sources)
        result[key] = strategy, paths, _source_hash(paths)
    return result


def available():
    """Describe all strategies explicitly registered by the local catalog."""
    return [{
        "id": item.id, "version": item.version, "api_version": API_VERSION,
        "implementation_hash": digest,
        "parameters": {k: asdict(rule) | {"required": rule.default is None} for k, rule in item.parameters.items()},
        "fields": item.fields, "capabilities": item.capabilities, "screen": item.signal is not None,
        "portfolio_feedback": item.decide is not None,
    } for item, _, digest in _bindings().values()]


def _node(node):
    if isinstance(node, str):
        return node
    return {"function": node.name, "parameters": json.loads(node.parameters_json),
            "units": node.units, "inputs": [_node(value) for value in node.inputs]}


def _inputs(node):
    if isinstance(node, str):
        yield node
    else:
        for dependency in node.inputs:
            yield from _inputs(dependency)


def prepare(reference, parameters, *, rebalance_ticks, screen=False):
    """Resolve a data-only selector and freeze the effective graph, schema, and implementation."""
    if not isinstance(reference, dict) or set(reference) != {"id", "version"} or any(type(v) is not str for v in reference.values()):
        raise ValueError("Strategy selector requires id and version strings")
    key = (reference["id"], reference["version"])
    if key not in _bindings():
        raise ValueError(f"Unknown trusted strategy {key[0]} version {key[1]}")
    strategy, paths, digest = _bindings()[key]
    if _source_hash(paths) != digest or _source_hash(_RUNTIME_PATHS) != _LOADED_RUNTIME:
        raise ValueError("Trusted strategy or runtime files changed. Restart the process before submitting or executing.")
    if not isinstance(parameters, dict) or set(parameters) - strategy.parameters.keys():
        raise ValueError("Unknown strategy parameter")
    parameters = {
        name: rule.check(name, parameters.get(name, rule.default))
        for name, rule in strategy.parameters.items()
    }
    # Reject non-JSON values including non-finite nested data before storing a receipt.
    parameters = json.loads(json.dumps(parameters, allow_nan=False))
    plan = strategy.prepare(MappingProxyType(parameters))
    if not isinstance(plan, Plan) or type(plan.warmup_bars) is not int or plan.warmup_bars < 0:
        raise ValueError("Strategy must supply a Plan with nonnegative warm-up")
    cadence = plan.decision_ticks if plan.decision_ticks is not None else rebalance_ticks
    if type(cadence) is not int or cadence < 1 or rebalance_ticks % cadence:
        raise ValueError("Decision cadence must divide the native rebalance interval")
    if screen and (strategy.signal is None or set(strategy.fields) - data.UNITS.keys()):
        raise ValueError("This strategy has no screening signal or needs native-only market inputs")
    graph = features.plan(plan.features)
    if set(plan.features) & (set(strategy.fields) | CLOCK_COLUMNS | {"target"}) or any(
        set(_inputs(node)) - set(strategy.fields) for node in plan.features.values()
    ):
        raise ValueError("Declare every raw feature input and use unique output names")
    if any(not isinstance(plot, Plot) for plot in plan.plots):
        raise ValueError("Strategy visualization descriptors must be Plot values")
    supplied = {plot.name for plot in plan.plots}
    extras = [Plot(name, name, node.units) for name, node in plan.features.items() if name not in supplied]
    if "target" not in supplied:
        extras.append(Plot("target", "Requested target", "USDT"))
    plan = replace(plan, plots=(*plan.plots, *extras))
    names = [plot.name for plot in plan.plots]
    if len(set(names)) != len(names) or any(
        not _NAME.fullmatch(name) or name in CLOCK_COLUMNS or name in strategy.fields for name in names
    ):
        raise ValueError("Strategy plots require unique, nonreserved names")
    if any(plot.pane not in {"price", "signal"} or not re.fullmatch(r"#[a-fA-F0-9]{6}", plot.color)
           for plot in plan.plots):
        raise ValueError("Use a price or signal pane and a hex plot color")
    if len(names) > 32:
        raise ValueError("At most 32 strategy series may be recorded")
    requirements = sorted(set(strategy.capabilities) | {node.name for node in graph})
    receipt = {
        **reference, "api_version": API_VERSION, "implementation_hash": digest, "runtime_hash": _LOADED_RUNTIME,
        "fields": list(strategy.fields), "warmup_bars": plan.warmup_bars, "decision_ticks": cadence,
        "feedback": strategy.decide is not None, "dependency_versions": _DEPENDENCIES,
        "capabilities": requirements, "graph": {name: _node(node) for name, node in plan.features.items()},
        "kernel_versions": {node.name: registry.describe(node.name)["version"] for node in graph},
        "plots": [{"display": "line", **asdict(plot)} for plot in plan.plots],
    }
    return strategy, plan, parameters, json.loads(json.dumps(receipt, allow_nan=False))


def resolve_frozen(spec, settings, *, screen=False):
    frozen = spec["strategy"]
    resolved = prepare({key: frozen[key] for key in ("id", "version")}, spec["parameters"],
                       rebalance_ticks=settings.rebalance_ticks, screen=screen)
    if resolved[3] != frozen:
        raise ValueError("Incompatible frozen strategy version, code, or feature graph")
    return resolved


def _array(name, values, shape):
    result = np.asarray(values, dtype=float)
    if result.shape != shape or np.isinf(result).any():
        raise ValueError(f"Strategy {name} must use the supplied axes and contain finite values or NaN")
    return result


class Runner:
    """One isolated callback state and optional audit recorder per experiment."""

    def __init__(self, resolved, symbols, seed, *, trace=None, end=None):
        strategy, self.plan, params, self.receipt = resolved
        self.strategy = strategy
        self.names = tuple(plot.name for plot in self.plan.plots)
        self.fields = self.strategy.fields
        self.state = strategy.initialize(MappingProxyType(params), tuple(symbols), np.random.default_rng(seed))
        self.offset = 0
        self.trace = trace
        self.holdout_boundary = end == HOLDOUT_START
        self.annotations = {}
        self._targets = None

    def _view(self, block):
        names = (*self.fields, *self.plan.features)
        return data.Block(block.timestamps_ms, block.symbols, block.available_at_ms, block.status,
                          {name: block.values[name] for name in names}, block.interval_ms,
                          {name: block.units[name] for name in names}, block.provenance)

    def screen(self, block):
        block = self._view(block)
        values = _array("signal", self.strategy.signal(self.state, block), block.status.shape)
        warm = np.arange(len(values)) + self.offset < self.plan.warmup_bars
        self.offset += len(values)
        return np.where(warm[:, None], np.nan, values)

    def _decision(self, value, shape):
        decision = value if isinstance(value, Decision) else Decision(value)
        if set(decision.series) - set(self.names) or set(decision.series) & (self.plan.features.keys() | {"target"}):
            raise ValueError("Decision observations must use declared non-feature plot names")
        return (_array("targets", decision.targets, shape),
                {name: _array(name, values, shape) for name, values in decision.series.items()})

    def __call__(self, block):
        view = self._view(block)
        target, annotations = self._decision(self.strategy.targets(self.state, view), view.status.shape)
        ticks = np.arange(len(target)) + self.offset
        enabled = (ticks >= self.plan.warmup_bars) & (ticks % self.receipt["decision_ticks"] == 0)
        if self.holdout_boundary:
            enabled &= view.available_at_ms < HOLDOUT_START
        target = np.where(enabled[:, None] & (view.status <= 1), target, np.nan)
        self.offset += len(target)
        self._log(view, target, annotations)
        return target

    def feedback_rows(self, block):
        n = len(block.timestamps_ms)
        self._targets = np.full(block.status.shape, np.nan)
        self.annotations = {}
        cadence = self.receipt["decision_ticks"]
        earliest = max(self.offset, self.plan.warmup_bars)
        first = -(-earliest // cadence) * cadence - self.offset
        return range(first, n, cadence)

    def step(self, block, row, state):
        view = self._view_row(block, row)
        value, series = self._decision(self.strategy.decide(self.state, view, state), view.status.shape)
        self._targets[row] = np.where(view.status[0] <= 1, value[0], np.nan)
        if self.trace is not None:
            for name, array in series.items():
                if name not in self.annotations:
                    self.annotations[name] = np.full_like(self._targets, np.nan)
                self.annotations[name][row] = array[0]
        return self._targets[row:row + 1]

    def _view_row(self, block, row):
        sl = slice(row, row + 1)
        names = (*self.fields, *self.plan.features)
        return data.Block(block.timestamps_ms[sl], block.symbols, block.available_at_ms[sl],
                          block.status[sl], {name: block.values[name][sl] for name in names},
                          block.interval_ms, {name: block.units[name] for name in names}, block.provenance)

    def finish_feedback(self, block):
        self.offset += len(block.timestamps_ms)
        self._log(block, self._targets, self.annotations)

    def _log(self, block, target, observations):
        if self.trace is None:
            return
        size, symbols = block.status.shape
        trace = np.empty(size * symbols, dtype=[
            *((key, "i8") for key in ("ms", "available_ms", "symbol")),
            *((key, "f8") for key in self.names),
        ])
        trace["ms"] = np.repeat(block.timestamps_ms, symbols)
        trace["available_ms"] = np.repeat(block.available_at_ms, symbols)
        trace["symbol"] = np.tile(np.arange(symbols), size)
        for name in self.names:
            values = target if name == "target" else block.values.get(name) if name in self.plan.features else observations.get(name)
            trace[name] = np.where(block.status <= 1, values, np.nan).ravel() if values is not None else np.nan
        self.trace.append("strategy_trace", trace)


__all__ = ["API_VERSION", "Parameter", "Plot", "Plan", "Decision", "Portfolio", "Strategy", "available"]
