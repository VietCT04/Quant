"""Chronological evaluation boundaries and explicit holdout release records."""

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path

import numpy as np

from marketdata.reader import as_millis
from ._core import array, duration

HOLDOUT_START = as_millis("2026-08-25")
HOLDOUT_END = as_millis("2026-11-25")


def _check(start, end, *, infrastructure=False):
    start, end = as_millis(start), as_millis(end)
    if end <= start:
        raise ValueError("Expected an increasing UTC range")
    if start < HOLDOUT_END and end > HOLDOUT_START and not infrastructure:
        raise PermissionError("The reserved strategy evaluation period is protected")
    return start, end


@dataclass(frozen=True)
class Split:
    """Half-open training, validation and optional test intervals in UTC epoch milliseconds."""

    train: tuple
    validation: tuple
    test: tuple | None = None


def chronological_split(start, validation_start, test_start, end):
    """Construct half-open chronological development train, validation and test ranges."""
    lo, hi = _check(start, end)
    validation, test = as_millis(validation_start), as_millis(test_start)
    if not lo < validation < test < hi:
        raise ValueError("Split boundaries must be strictly ordered")
    return Split((lo, validation), (validation, test), (test, hi))


def walk_forward(
    start, end, train, validation, *, step=None, expanding=False, embargo_ms=0
):
    """Yield full chronological windows with optional expanding training and a trailing training embargo."""
    lo, hi = _check(start, end)
    train, validation = duration(train), duration(validation)
    stride = validation if step is None else duration(step)
    if not 0 <= embargo_ms < train:
        raise ValueError(
            "Embargo must be nonnegative and smaller than the training window"
        )
    for boundary in range(lo + train, hi - validation + 1, stride):
        yield Split(
            (lo if expanding else boundary - train, boundary - embargo_ms),
            (boundary, boundary + validation),
        )


def purge(
    timestamps_ms, label_end_ms, evaluation_start, evaluation_end, *, embargo_ms=0
):
    """Return a training mask excluding labels that touch evaluation or start during its embargo."""
    timestamps, endings = np.asarray(timestamps_ms), np.asarray(label_end_ms)
    lo, hi = _check(evaluation_start, evaluation_end)
    if (
        timestamps.shape != endings.shape
        or np.any(endings < timestamps)
        or embargo_ms < 0
    ):
        raise ValueError("Invalid label intervals or embargo")
    return (endings < lo) | (timestamps >= hi + embargo_ms)


def record_final_release(path, *, owner_authorization, frozen_spec):
    """Durably record explicit owner approval and a frozen final-evaluation specification."""
    required = {
        "strategy",
        "parameters",
        "universe",
        "dataset_fingerprint",
        "git_commit",
        "cost_model",
        "target_digest",
    }
    if (
        not isinstance(owner_authorization, str)
        or not owner_authorization.strip()
        or not required <= frozen_spec.keys()
    ):
        raise ValueError(
            "An owner authorization reference and complete frozen specification are required"
        )
    payload = {
        "authorization": owner_authorization,
        "specification": frozen_spec,
        "holdout_ms": [HOLDOUT_START, HOLDOUT_END],
    }
    body = json.dumps(payload, sort_keys=True, allow_nan=False)
    payload["release_id"] = hashlib.sha256(body.encode()).hexdigest()
    with Path(path).open("x", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, allow_nan=False)
    return payload


def _verify_release(path):
    with Path(path).open(encoding="utf-8") as handle:
        payload = json.load(handle)
    identity = payload.pop("release_id")
    actual = hashlib.sha256(
        json.dumps(payload, sort_keys=True, allow_nan=False).encode()
    ).hexdigest()
    if (
        actual != identity
        or not payload["authorization"].strip()
        or payload["holdout_ms"] != [HOLDOUT_START, HOLDOUT_END]
    ):
        raise PermissionError("Invalid final evaluation release receipt")
    return {**payload, "release_id": identity}


def holdout_symbols(symbols, held_out):
    """Split a fixed symbol universe in its existing order with an explicit symbol holdout."""
    symbols, held_out = tuple(symbols), set(held_out)
    if not held_out or not held_out < set(symbols) or len(set(symbols)) != len(symbols):
        raise ValueError("Specify a nonempty proper subset of a unique universe")
    return (
        tuple(s for s in symbols if s not in held_out),
        tuple(s for s in symbols if s in held_out),
    )


def grouped_robustness(values, groups):
    """Return sample counts, means and sample SD for supplied symbol, period or regime groups."""
    x, group = array(values), np.asarray(groups)
    if x.shape != group.shape:
        raise ValueError("Observations and group labels must align exactly")
    from . import math as qm

    output = {}
    for label in np.unique(group):
        y = x[(group == label) & np.isfinite(x)]
        output[str(label)] = {
            "n": len(y),
            "mean": float(y.mean()) if len(y) else None,
            "std": float(qm.std(y)) if len(y) > 1 else None,
        }
    return output


def cost_sensitivity(settings, multipliers, execute):
    """Run an existing evaluation under scaled fee, spread, slippage and impact assumptions."""
    from dataclasses import replace
    from simulator.native import Settings

    if not isinstance(settings, Settings):
        raise TypeError("Supply a native simulator Settings instance")
    result = {}
    for multiplier in multipliers:
        if not np.isfinite(multiplier) or multiplier < 0 or multiplier in result:
            raise ValueError("Cost multipliers must be unique, nonnegative and finite")
        fields = {
            key: getattr(settings, key) * multiplier
            for key in ("fee_bps", "spread_bps", "slippage_bps", "impact_bps")
        }
        result[multiplier] = execute(replace(settings, **fields))
    return result


def placebo_signals(score, *, block_size, repetitions, seed=0):
    """Yield seeded permutations of nonoverlapping joint-symbol blocks, preserving the original missing mask."""
    x = array(score)
    if (
        x.ndim != 2
        or not isinstance(block_size, int)
        or block_size < 1
        or len(x) // block_size < 2
        or repetitions < 1
    ):
        raise ValueError(
            "At least two equal-length cross-sectional blocks are required"
        )
    rng = np.random.default_rng(seed)
    count = len(x) // block_size
    n = count * block_size
    valid = np.isfinite(x[:n])
    blocks = x[:n].reshape(count, block_size, x.shape[1])
    for _ in range(repetitions):
        out = np.full_like(x, np.nan)
        out[:n] = np.where(
            valid, blocks[rng.permutation(count)].reshape(n, x.shape[1]), np.nan
        )
        yield out


def deflated_sharpe(returns, *, trial_sharpes, independent_trials):
    """Probabilistic Sharpe against the expected maximum from independent Gaussian trial Sharpe estimates."""
    from scipy.stats import norm
    from . import math as qm, stats

    x = stats._complete(returns)
    trial = stats._complete(trial_sharpes)
    if (
        not isinstance(independent_trials, int)
        or not 2 <= independent_trials <= len(trial)
        or len(x) < 8
    ):
        raise ValueError(
            "Require at least eight returns and at least two documented independent trials"
        )
    sigma = qm.std(x)
    if sigma <= 0:
        raise ValueError("A nonconstant return sample is required")
    sharpe = x.mean() / sigma
    euler = 0.5772156649015329
    benchmark = qm.std(trial) * (
        (1 - euler) * norm.ppf(1 - 1 / independent_trials)
        + euler * norm.ppf(1 - 1 / (np.e * independent_trials))
    )
    variance = 1 - qm.skewness(x) * sharpe + (qm.kurtosis(x) + 2) * sharpe**2 / 4
    if variance <= 0:
        raise ValueError("Sharpe sampling variance is nonpositive")
    return {
        "probability": float(
            norm.cdf((sharpe - benchmark) * np.sqrt(len(x) - 1) / np.sqrt(variance))
        ),
        "sharpe": float(sharpe),
        "benchmark": float(benchmark),
        "independent_trials": independent_trials,
    }


def adjust_pvalues(pvalues, *, method="holm"):
    """Adjust p-values across the frozen family of trials using the shared inference implementation."""
    from .stats import adjust_pvalues as adjust

    return adjust(pvalues, method=method)


__all__ = [
    "chronological_split",
    "walk_forward",
    "purge",
    "record_final_release",
    "holdout_symbols",
    "grouped_robustness",
    "cost_sensitivity",
    "placebo_signals",
    "deflated_sharpe",
    "adjust_pvalues",
]
