"""Generic, deterministic search and experiment comparison utilities."""

from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor
from collections import deque
from itertools import islice, product

import numpy as np


def grid_search(parameters, *, constraint=None):
    """Yield candidates in input key order from a finite Cartesian product."""
    keys = tuple(parameters)
    for values in product(*(parameters[k] for k in keys)):
        item = dict(zip(keys, values))
        if constraint is None or constraint(item):
            yield item


def random_search(distributions, *, trials, seed=0, constraint=None, max_attempts=None):
    """Yield seeded candidates from choice sequences, scipy distributions or generator callables."""
    maximum = 10 * trials if max_attempts is None else max_attempts
    if trials < 1 or maximum < trials:
        raise ValueError("Require positive trials and sufficient attempts")
    rng = np.random.default_rng(seed)
    accepted = 0
    for _ in range(maximum):
        item = {}
        for key, source in distributions.items():
            if hasattr(source, "rvs"):
                item[key] = source.rvs(random_state=rng)
            elif callable(source):
                item[key] = source(rng)
            else:
                item[key] = source[rng.integers(len(source))]
        if constraint is None or constraint(item):
            yield item
            accepted += 1
            if accepted == trials:
                return
    raise ValueError("Candidate constraint exhausted the attempt budget")


def parallel(execute, candidates, *, workers=1, backend="process"):
    """Run independent experiments in stable order with bounded pending submissions."""
    if workers < 1 or backend not in {"process", "thread"}:
        raise ValueError("Choose positive worker count and a process or thread backend")
    if workers == 1:
        yield from map(execute, candidates)
        return
    kind = ProcessPoolExecutor if backend == "process" else ThreadPoolExecutor
    with kind(max_workers=workers) as pool:
        stream = iter(candidates)
        pending = deque(pool.submit(execute, c) for c in islice(stream, 2 * workers))
        for item in stream:
            yield pending.popleft().result()
            pending.append(pool.submit(execute, item))
        while pending:
            yield pending.popleft().result()


def pareto_frontier(objectives, *, maximize=None):
    """Return indices of finite undominated rows, keeping tied candidates."""
    x = np.asarray(objectives, dtype=float)
    if x.ndim != 2 or x.shape[1] == 0:
        raise ValueError("Supply a candidate by objective matrix")
    directions = (
        np.ones(x.shape[1], dtype=bool)
        if maximize is None
        else np.asarray(maximize, dtype=bool)
    )
    if directions.shape != (x.shape[1],):
        raise ValueError("One maximize flag per objective is required")
    x = x * np.where(directions, 1, -1)
    finite = np.flatnonzero(np.isfinite(x).all(axis=1))
    a = x[finite]
    return np.array(
        [
            idx
            for idx, row in zip(finite, a)
            if not np.any(np.all(a >= row, axis=1) & np.any(a > row, axis=1))
        ],
        dtype=int,
    )


def multi_objective_rank(objectives, *, maximize=None):
    """Rank finite candidates by successive Pareto fronts, starting at one."""
    x = np.asarray(objectives, dtype=float)
    if x.ndim != 2:
        raise ValueError("Supply a candidate by objective matrix")
    rank = np.full(len(x), np.nan)
    remaining = np.flatnonzero(np.isfinite(x).all(axis=1))
    stage = 1
    while len(remaining):
        winners = pareto_frontier(x[remaining], maximize=maximize)
        rank[remaining[winners]] = stage
        remaining = np.delete(remaining, winners)
        stage += 1
    return rank


def parameter_stability(parameters, metric):
    """Map each sampled parameter value to count and mean/stdev of its finite measured metric."""
    x = np.asarray(metric, dtype=float)
    if not parameters or x.shape != (len(parameters),):
        raise ValueError("Candidate and metric counts must align")
    keys = tuple(parameters[0])
    if any(set(p) != set(keys) for p in parameters):
        raise ValueError("Candidates must share parameter names")
    result = {}
    for key in keys:
        grouped = {}
        for p, m in zip(parameters, x):
            if np.isfinite(m):
                grouped.setdefault(p[key], []).append(m)
        result[key] = {
            value: {
                "n": len(values),
                "mean": float(np.mean(values)),
                "std": float(np.std(values, ddof=1)) if len(values) > 1 else None,
            }
            for value, values in grouped.items()
        }
    return result


__all__ = [
    "grid_search",
    "random_search",
    "parallel",
    "pareto_frontier",
    "multi_objective_rank",
    "parameter_stability",
]
