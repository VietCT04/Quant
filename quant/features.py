"""Lazy feature composition and bounded per-block content-addressed caches."""

from collections import OrderedDict
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile

import numpy as np

from ._core import MissingCapabilityError, array
from .data import Block

_FEATURE_KERNELS = {"stateless", "finite-lookback", "recursive-state"}


def _json(x):
    return json.dumps(x, sort_keys=True, separators=(",", ":"), allow_nan=False)


@dataclass(frozen=True)
class Feature:
    """A causal expression with inputs, serialized parameters, and one output unit."""

    name: str
    inputs: tuple
    parameters_json: str
    units: str


def expression(name, *inputs, units, **parameters):
    """Build a lazy expression from a registered causal function or a bound special kernel."""
    from .registry import describe, resolve

    info = describe(name)
    if info["causality"] != "causal" or info["streaming"] not in _FEATURE_KERNELS:
        raise MissingCapabilityError(name, ("causal streaming kernel",))
    if not inputs or not all(isinstance(x, (str, Feature)) for x in inputs):
        raise TypeError("Feature inputs must be field names or nested expressions")
    if not units:
        raise ValueError("An output unit is required")
    function = resolve(name)
    canonical = f"{function.__module__}.{function.__name__}"
    try:
        if resolve(canonical) is function:
            name = canonical
    except KeyError:
        pass
    return Feature(name, tuple(inputs), _json(parameters), units)


class Cache:
    """Optional JSON-keyed array cache with per-instance memory and file budgets."""

    def __init__(self, *, directory=None, memory_bytes=64 << 20, disk_bytes=0):
        if memory_bytes < 0 or disk_bytes < 0 or (directory is None and disk_bytes):
            raise ValueError("Invalid feature cache budgets")
        self.directory = Path(directory) if directory is not None else None
        self.memory_bytes, self.disk_bytes = memory_bytes, disk_bytes
        self.memory = OrderedDict()
        self.bytes = 0
        self.hits = 0
        if self.directory is not None:
            self.directory.mkdir(parents=True, exist_ok=True)

    def get(self, key):
        """Retrieve a read-only array by its SDK-generated cache key."""
        if not isinstance(key, str) or not re.fullmatch(r"[0-9a-f]{64}", key):
            raise ValueError("Invalid content-addressed cache key")
        if key in self.memory:
            value = self.memory[key]
            self.memory.move_to_end(key)
        elif self.directory is not None and self.disk_bytes:
            path = self.directory / f"{key}.npy"
            try:
                if path.stat().st_size > self.disk_bytes:
                    return None
                value = np.load(path, allow_pickle=False)
                value.flags.writeable = False
            except (FileNotFoundError, ValueError, OSError):
                return None
        else:
            return None
        self.hits += 1
        return value

    def put(self, key, value):
        """Store an immutable numeric array under the configured cache budgets."""
        if not isinstance(key, str) or not re.fullmatch(r"[0-9a-f]{64}", key):
            raise ValueError("Invalid content-addressed cache key")
        value = np.array(value, copy=True, order="C")
        if value.dtype.kind not in "buif":
            raise TypeError("Only real numeric arrays may be cached")
        value.flags.writeable = False
        if self.memory_bytes and value.nbytes <= self.memory_bytes:
            if key in self.memory:
                self.bytes -= self.memory.pop(key).nbytes
            self.memory[key] = value
            self.bytes += value.nbytes
            while self.bytes > self.memory_bytes:
                self.bytes -= self.memory.popitem(last=False)[1].nbytes
        if (
            self.directory is not None
            and self.disk_bytes
            and value.nbytes + 4096 <= self.disk_bytes
        ):
            target = self.directory / f"{key}.npy"
            if target.exists():
                return
            paths = sorted(
                self.directory.glob("*.npy"), key=lambda p: p.stat().st_mtime
            )
            remaining = sum(p.stat().st_size for p in paths)
            for path in paths:
                if remaining + value.nbytes + 4096 <= self.disk_bytes:
                    break
                size = path.stat().st_size
                path.unlink(missing_ok=True)
                remaining -= size
            with tempfile.NamedTemporaryFile(
                dir=self.directory, delete=False, suffix=".tmp"
            ) as handle:
                temp = Path(handle.name)
                try:
                    np.save(handle, value, allow_pickle=False)
                    handle.flush()
                    os.fsync(handle.fileno())
                except BaseException:
                    handle.close()
                    temp.unlink(missing_ok=True)
                    raise
            try:
                os.replace(temp, target)
            finally:
                temp.unlink(missing_ok=True)


def cache_key(
    *, function, version, fingerprint, parameters, missing_policy, configuration
):
    """Hash the complete computation identity with a deterministic JSON encoding."""
    if not all(
        isinstance(x, str) and x
        for x in (function, version, fingerprint, missing_policy)
    ):
        raise ValueError(
            "Function, version, dataset fingerprint and missing policy are required"
        )
    from . import __version__

    return hashlib.sha256(
        _json(
            [
                __version__,
                function,
                version,
                fingerprint,
                parameters,
                missing_policy,
                configuration,
            ]
        ).encode()
    ).hexdigest()


def _digest(arrays):
    h = hashlib.sha256()
    for value in arrays:
        a = np.ascontiguousarray(value)
        h.update(repr((a.dtype.str, a.shape)).encode())
        h.update(memoryview(a).cast("B") if a.size else b"")
    return h.hexdigest()


def plan(expressions):
    """Return the unique feature graph in dependency order with one node per expression identity."""
    from .registry import describe, resolve

    nodes = {}

    def visit(node):
        if isinstance(node, str) or node in nodes:
            return
        if not isinstance(node, Feature):
            raise TypeError("Expected a feature expression or field name")
        for dependency in node.inputs:
            visit(dependency)
        info = describe(node.name)
        if info["causality"] != "causal" or info["streaming"] not in _FEATURE_KERNELS:
            raise MissingCapabilityError(node.name, ("causal streaming kernel",))
        nodes[node] = (resolve(node.name), info, json.loads(node.parameters_json))

    for node in expressions.values():
        if not isinstance(node, Feature):
            raise TypeError("Output definitions must be Feature expressions")
        visit(node)
    return nodes


def evaluate(
    blocks,
    expressions,
    *,
    cache=None,
    fingerprint=None,
    context=None,
    memory_bytes=256 << 20,
    on_error=None,
):
    """Evaluate registered streaming expressions once per distinct input and block."""
    from . import ts
    from ._core import bars

    expressions = dict(expressions)
    if not expressions or any(not isinstance(a, Feature) for a in expressions.values()):
        raise TypeError("Map output names to lazy Feature expressions")
    if cache is not None and not fingerprint:
        raise ValueError("A cache requires an explicit dataset fingerprint")
    if memory_bytes < 1:
        raise ValueError("Feature memory budget must be positive")
    graph = plan(expressions)
    states = {}
    failed = set()
    previous = None
    for block in blocks:
        if not isinstance(block, Block) or not len(block.timestamps_ms):
            raise ValueError("Expected nonempty regular market blocks")
        if previous is not None:
            last, symbols, interval = previous
            if (
                block.timestamps_ms[0] != last + interval
                or block.symbols != symbols
                or block.interval_ms != interval
            ):
                raise ValueError("Feature stream must retain ordered contiguous axes")
        previous = (block.timestamps_ms[-1], block.symbols, block.interval_ms)
        computed = {}

        def compute(node):
            if isinstance(node, str):
                if node not in block.values:
                    raise MissingCapabilityError("feature input", (node,))
                return block.values[node]
            if node in computed:
                return computed[node]
            args = [apply(n) for n in node.inputs]
            function, info, parameters = graph[node]
            parameters = dict(parameters)
            state_bytes = sum(
                part.nbytes
                for state in states.values()
                if isinstance(state, tuple)
                for part in state
            )
            live_bytes = sum(a.nbytes for a in computed.values())
            if function in {ts.ema, ts.wilder}:
                if (
                    state_bytes + live_bytes + sum(a.nbytes for a in args) * 3
                    > memory_bytes
                ):
                    raise MemoryError(
                        "Feature state and block exceed the working array budget"
                    )
                if node not in states:
                    p = dict(parameters)
                    window = p.pop("span" if function is ts.ema else "window")
                    p.setdefault("interval_ms", block.interval_ms)
                    kind = ts.EMAState if function is ts.ema else ts.WilderState
                    states[node] = kind(window, **p)
                result = states[node].update(*args)
            else:
                count = 0
                if info["streaming"] == "finite-lookback":
                    window = parameters.get("window", parameters.get("periods", 1))
                    parameters.setdefault("interval_ms", block.interval_ms)
                    n = bars(window, parameters["interval_ms"])
                    count = (
                        n
                        if function
                        in {ts.lag, ts.diff, ts.pct_change, ts.log_returns, ts.breakout}
                        else n - 1
                    )
                history = states.get(node)
                history_bytes = (
                    sum(a.nbytes for a in history) if history is not None else 0
                )
                if (
                    state_bytes
                    + live_bytes
                    + 3 * (history_bytes + sum(a.nbytes for a in args))
                    > memory_bytes
                ):
                    raise MemoryError(
                        "Feature state and block exceed the working array budget"
                    )
                combined = (
                    args
                    if history is None
                    else [np.concatenate([a, b]) for a, b in zip(history, args)]
                )
                if count:
                    states[node] = tuple(a[-count:].copy() for a in combined)
                key = None
                if cache is not None:
                    key = cache_key(
                        function=node.name,
                        version=info["version"],
                        fingerprint=fingerprint,
                        parameters=parameters,
                        missing_policy=info["missing_data"],
                        configuration=[
                            context,
                            block.symbols,
                            block.interval_ms,
                            node.units,
                            _digest(
                                (
                                    block.timestamps_ms,
                                    block.available_at_ms,
                                    block.status,
                                    *combined,
                                )
                            ),
                        ],
                    )
                result = None if key is None else cache.get(key)
                if result is None:
                    result = array(function(*combined, **parameters))[
                        -len(block.timestamps_ms) :
                    ]
                    if key is not None and np.shape(result) == block.status.shape:
                        cache.put(key, result)
            if np.shape(result) != block.status.shape:
                raise ValueError("Feature output must match the source block axes")
            computed[node] = result
            return result

        def apply(node):
            if node in failed:
                raise ValueError(f"Feature dependency failed: {node.name}")
            try:
                return compute(node)
            except Exception as error:
                if on_error is not None and node in graph:
                    failed.add(node)
                    states.pop(node, None)
                    on_error(node, error)
                raise

        valid = block.status <= 1
        outputs = {}
        for node in dict.fromkeys(expressions.values()):
            try:
                value = apply(node)
                masked = np.where(valid, value, np.nan)
            except Exception:
                if on_error is None:
                    raise
                masked = np.full(block.status.shape, np.nan)
            masked.flags.writeable = False
            outputs[node] = masked
        values = {name: outputs[node] for name, node in expressions.items()}
        yield Block(
            block.timestamps_ms,
            block.symbols,
            block.available_at_ms,
            block.status,
            values,
            block.interval_ms,
            {name: node.units for name, node in expressions.items()},
            (*block.provenance, *tuple(expressions.items())),
        )


__all__ = ["expression", "cache_key", "plan", "evaluate"]
