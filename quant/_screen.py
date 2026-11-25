"""Bounded development diagnostics reusing scores' feature nodes and forward labels."""

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from time import perf_counter

import numpy as np

from . import cs, data as market, evaluate as ev, features
from ._resources import hardware, worker_count
from .validation import _check


@dataclass(frozen=True)
class ScreenCandidate:
    """Causal score callback over named feature arrays and projected market fields."""

    name: str
    expressions: dict
    score: object


class _Metrics:
    def __init__(self, groups):
        self.group_count = np.zeros(groups, dtype="i8")
        self.group_sum = np.zeros(groups)
        self.count = self.score_count = self.active = self.turnover_count = 0
        self.event_count = self.event_sum = 0
        self.ic_sum = self.ic_square = self.turnover = 0.0
        self.months = {}
        self.previous = None

    def add(self, score, block, labels, method, groups, event_threshold):
        ic = ev.information_coefficient(score, labels, method=method)
        valid = np.isfinite(ic)
        self.count += int(valid.sum())
        self.ic_sum += float(np.nansum(ic))
        self.ic_square += float(np.nansum(ic**2))
        n = block.status <= 1
        self.active += int(n.sum())
        self.score_count += int((n & np.isfinite(score)).sum())
        if groups:
            quantiles, counts = ev.quantile_returns(score, labels, groups=groups)
            self.group_sum += np.nansum(quantiles * counts, axis=0)
            self.group_count += counts.sum(axis=0)
        if event_threshold is not None:
            events = ev.conditional_returns(labels, np.abs(score) >= event_threshold)
            self.event_count += events["observations"]
            if events["observations"]:
                self.event_sum += events["mean"] * events["observations"]
        weights = cs.gross_normalize(score, active=n)
        if self.previous is not None:
            weights = np.vstack([self.previous, weights])
        delta = ev.signal_turnover(weights)
        self.turnover += np.nansum(delta)
        self.turnover_count += int(np.isfinite(delta).sum())
        self.previous = weights[-1].copy()
        months = block.timestamps_ms.astype("datetime64[ms]").astype("datetime64[M]")
        for month in np.unique(months):
            subset = ic[months == month]
            total, count = self.months.get(str(month), (0.0, 0))
            self.months[str(month)] = (
                total + float(np.nansum(subset)),
                count + int(np.isfinite(subset).sum()),
            )

    def report(self):
        mean = self.ic_sum / self.count if self.count else None
        sd = (
            np.sqrt(
                max(
                    0.0,
                    (self.ic_square - self.ic_sum**2 / self.count) / (self.count - 1),
                )
            )
            if self.count > 1
            else 0.0
        )
        return {
            "mode": "descriptive approximate screening",
            "mean_ic": mean,
            "ic_observations": self.count,
            "icir": mean / sd if sd > 0 else None,
            "coverage": self.score_count / self.active if self.active else None,
            "normalized_score_turnover": self.turnover / self.turnover_count
            if self.turnover_count
            else None,
            "turnover_observations": self.turnover_count,
            "quantile_returns": [
                s / int(n) if n else None
                for s, n in zip(self.group_sum, self.group_count)
            ],
            "quantile_observations": self.group_count.tolist(),
            "event_return": self.event_sum / self.event_count
            if self.event_count
            else None,
            "event_observations": self.event_count,
            "monthly_ic": {
                m: {"mean": s / n if n else None, "observations": n}
                for m, (s, n) in self.months.items()
            },
        }


def screen(
    selection,
    candidates,
    *,
    horizon=12,
    method="spearman",
    groups=0,
    event_threshold=None,
    workers="auto",
    cpu_limit=None,
    block_steps=4096,
    batch_size=128,
    memory_budget_gib=2,
    decoded_cache=None,
    cancel=None,
):
    """Evaluate descriptive IC, coverage, turnover and optional events and quantiles in bounded shared waves."""
    _check(selection.start, selection.end)
    candidates = list(candidates)
    if (
        not candidates
        or len({c.name for c in candidates}) != len(candidates)
        or any(
            not isinstance(c, ScreenCandidate) or not callable(c.score)
            for c in candidates
        )
        or "close" not in selection.fields
        or method not in {"pearson", "spearman"}
        or type(groups) is not int
        or groups == 1
        or groups < 0
        or type(batch_size) is not int
        or batch_size < 1
        or type(block_steps) is not int
        or block_steps < 1
        or memory_budget_gib <= 0
        or event_threshold is not None
        and (not np.isfinite(event_threshold) or event_threshold < 0)
    ):
        raise ValueError(
            "Invalid candidates, close projection, diagnostic configuration or resource limit"
        )
    count = worker_count(workers, cpu_limit=cpu_limit, tasks=len(candidates))
    cap = min(hardware()["available_bytes"], int(memory_budget_gib * (1 << 30)))
    metrics, failures = {}, {}
    started = perf_counter()
    profile = dict(
        workers=count,
        feature_nodes=0,
        market_cells=0,
        observed_bars=0,
        candidate_cells=0,
        source_and_features_seconds=0.0,
        diagnostics_seconds=0.0,
    )
    with ThreadPoolExecutor(max_workers=count) as pool:
        for offset in range(0, len(candidates), batch_size):
            wave = candidates[offset : offset + batch_size]
            merged = {}
            for c in wave:
                for node in c.expressions.values():
                    if node not in merged:
                        merged[node] = f"feature_{len(merged)}"
            plan = features.plan({alias: node for node, alias in merged.items()})
            profile["feature_nodes"] += len(plan)
            per_row = len(selection.symbols) * (
                128 + 32 * len(merged) + 128 * min(len(wave), count)
            )
            steps = min(block_steps, cap // per_row)
            if steps < 1:
                raise MemoryError("Insufficient screening memory for one row")
            states = {c.name: _Metrics(groups) for c in wave}
            feature_errors = {}

            def feature_failed(node, error):
                feature_errors[node] = f"{type(error).__name__}: {error}"

            base = None

            def source():
                nonlocal base
                for base in market.iter_blocks(
                    selection, block_steps=steps, decoded_cache=decoded_cache
                ):
                    for x in (
                        *base.values.values(),
                        base.status,
                        base.available_at_ms,
                        base.timestamps_ms,
                    ):
                        x.flags.writeable = False
                    yield base

            def panels():
                from dataclasses import replace

                if not merged:
                    yield from source()
                    return
                for computed in features.evaluate(
                    source(),
                    {v: k for k, v in merged.items()},
                    memory_bytes=cap,
                    on_error=feature_failed,
                ):
                    yield replace(
                        computed,
                        values=dict(base.values) | dict(computed.values),
                        units=dict(base.units) | dict(computed.units),
                    )

            labels_stream = iter(
                ev.iter_forward_returns(panels(), horizon, memory_bytes=cap)
            )
            while states:
                if cancel is not None and cancel.is_set():
                    failures.update(dict.fromkeys(states, "cancelled"))
                    states.clear()
                    break
                tick = perf_counter()
                pair = next(labels_stream, None)
                profile["source_and_features_seconds"] += perf_counter() - tick
                if pair is None:
                    break
                block, labels = pair
                for c in wave:
                    if c.name in states:
                        blocked = next(
                            (
                                feature_errors[node]
                                for node in c.expressions.values()
                                if node in feature_errors
                            ),
                            None,
                        )
                        if blocked is not None:
                            failures[c.name] = blocked
                            states.pop(c.name)
                for x in (
                    *block.values.values(),
                    block.status,
                    block.available_at_ms,
                    block.timestamps_ms,
                ):
                    x.flags.writeable = False
                profile["market_cells"] += block.status.size
                profile["observed_bars"] += int(np.count_nonzero(block.status <= 1))

                def candidate(c):
                    from ._core import array

                    try:
                        named = {
                            name: value
                            for name, value in block.values.items()
                            if name in selection.fields
                        }
                        named.update(
                            {
                                name: block.values[merged[node]]
                                for name, node in c.expressions.items()
                            }
                        )
                        score = array(c.score(named))
                        if score.shape != block.status.shape:
                            raise ValueError(
                                "Screen score must follow [time,symbol] axes"
                            )
                        score = np.where(block.status <= 1, score, np.nan)
                        states[c.name].add(
                            score, block, labels, method, groups, event_threshold
                        )
                        return c.name, None
                    except Exception as error:
                        return c.name, f"{type(error).__name__}: {error}"

                active = [c for c in wave if c.name in states]
                tick = perf_counter()
                for name, error in (
                    pool.map(candidate, active) if count > 1 else map(candidate, active)
                ):
                    if error is None:
                        profile["candidate_cells"] += block.status.size
                    else:
                        states.pop(name)
                        failures[name] = error
                profile["diagnostics_seconds"] += perf_counter() - tick
            metrics.update({name: state.report() for name, state in states.items()})
    profile["wall_seconds"] = perf_counter() - started
    return {"results": metrics, "failures": failures, "profile": profile}


def screen_decay(selection, candidates, horizons, **kwargs):
    """Evaluate a predeclared diagnostic horizon grid and retain separate coverage and IC by horizon."""
    frozen = tuple(candidates)
    return {
        h: screen(selection, frozen, horizon=h, **kwargs)
        for h in dict.fromkeys(horizons)
    }


def screen_walk_forward(selection, splits, candidate_factory, **kwargs):
    """Screen separate chronological validation windows with a caller-controlled training-only factory."""
    from dataclasses import replace
    from .validation import Split

    for split in splits:
        if not isinstance(split, Split):
            raise TypeError("Use chronological validation Split objects")
        start, end = _check(*split.train)
        left, right = _check(*split.validation)
        if not selection.start <= start < end <= left < right <= selection.end:
            raise ValueError(
                "Training and validation must be disjoint and within the source selection"
            )
        if any(t % selection.dataset.interval_ms for t in (start, end, left, right)):
            raise ValueError(
                "Walk-forward boundaries must align to the source data grid"
            )
        candidates = candidate_factory(replace(selection, start=start, end=end))
        yield (
            split,
            screen(replace(selection, start=left, end=right), candidates, **kwargs),
        )
