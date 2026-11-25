"""Cross-sectional operators on [time, symbol] matrices; finite members only."""

import numpy as np
from scipy.stats import rankdata

from . import math as qm
from ._core import array, divide


def _panel(x, active=None, minimum=1):
    x = array(x)
    if x.ndim != 2 or minimum < 1:
        raise ValueError("Expected [time, symbol] and a positive minimum cross-section")
    valid = np.isfinite(x)
    if active is not None:
        active = np.asarray(active)
        if active.shape != x.shape or active.dtype.kind != "b":
            raise ValueError("The active mask must be boolean and share the panel axes")
        valid &= active
    valid &= valid.sum(axis=1, keepdims=True) >= minimum
    return np.where(valid, x, np.nan)


def rank(x, *, active=None, min_observations=2):
    """Ascending one-based rank with average ties."""
    return rankdata(_panel(x, active, min_observations), axis=1, nan_policy="omit")


def percentile(x, *, active=None, min_observations=2):
    """Ascending midpoint rank percentile (rank - 0.5)/n."""
    result = rank(x, active=active, min_observations=min_observations)
    return divide(result - 0.5, np.isfinite(result).sum(axis=1, keepdims=True))


def mean(x, *, active=None, min_observations=1):
    """One finite cross-sectional arithmetic mean per timestamp."""
    return qm.mean(_panel(x, active, min_observations), axis=1)


def std(x, *, active=None, min_observations=2, ddof=1):
    """One cross-sectional standard deviation per timestamp."""
    return qm.std(_panel(x, active, min_observations), axis=1, ddof=ddof)


def demean(x, *, active=None, min_observations=2):
    """Subtract the mean of currently valid members at each timestamp."""
    x = _panel(x, active, min_observations)
    return x - qm.mean(x, axis=1)[:, None]


def zscore(x, *, active=None, min_observations=2, ddof=1):
    """Mean-centered cross section scaled by its standard deviation."""
    x = _panel(x, active, min_observations)
    return divide(demean(x), qm.std(x, axis=1, ddof=ddof)[:, None])


def robust_zscore(x, *, active=None, min_observations=2):
    """Median-centered cross section scaled by Gaussian-consistent MAD."""
    x = _panel(x, active, min_observations)
    return divide(
        x - qm.median(x, axis=1)[:, None], qm.mad(x, axis=1, scale="normal")[:, None]
    )


def winsorize(x, *, lower=0.01, upper=0.99, active=None):
    """Clip to row-wise linear quantiles, preserving unavailable membership."""
    if not 0 <= lower <= upper <= 1:
        raise ValueError("Winsorization quantiles must be ordered in [0,1]")
    x = _panel(x, active)
    limits = qm.quantile(x, [lower, upper], axis=1)
    return np.clip(x, limits[0, :, None], limits[1, :, None])


def quantile_groups(x, groups=5, *, active=None):
    """Zero-based midpoint-percentile buckets, with tied members assigned together."""
    if not isinstance(groups, int) or groups < 2:
        raise ValueError("At least two integer groups are required")
    return np.floor(groups * percentile(x, active=active))


def top_k(x, k, *, active=None):
    """Select at most k highest finite scores; symbol column order breaks ties."""
    x = _panel(x, active)
    if not isinstance(k, int) or k < 1:
        raise ValueError("k must be a positive integer")
    winners = np.argsort(-x, axis=1, kind="stable")[:, :k]
    result = np.zeros_like(x, dtype=bool)
    np.put_along_axis(result, winners, True, axis=1)
    return result & np.isfinite(x)


def bottom_k(x, k, **kwargs):
    """Select at most k lowest finite scores; symbol column order breaks ties."""
    return top_k(-array(x), k, **kwargs)


def gross_normalize(x, *, gross=1.0, active=None):
    """Scale signed exposure scores to a row gross budget; zero rows stay zero."""
    if not np.isfinite(gross) or gross < 0:
        raise ValueError("Gross budget must be nonnegative")
    x = _panel(x, active)
    total = np.nansum(np.abs(x), axis=1, keepdims=True)
    return np.where((total == 0) & np.isfinite(x), 0, gross * divide(x, total))


def long_short_normalize(x, *, long=0.5, short=0.5, active=None):
    """Allocate independent nonnegative long and short exposure budgets; absent sides remain unallocated."""
    x = _panel(x, active)
    return gross_normalize(np.maximum(x, 0), gross=long) + gross_normalize(
        np.minimum(x, 0), gross=short
    )


def residualize(x, factors, *, active=None, weights=None):
    """Per-date regression residuals with intercept and one or more supplied point-in-time factor exposures."""
    x = _panel(x, active)
    factors = array(factors)
    if factors.ndim == 2:
        factors = factors[:, :, None]
    if factors.ndim != 3 or factors.shape[:2] != x.shape:
        raise ValueError("Factors require [time, symbol, factor] axes")
    w = np.ones_like(x) if weights is None else _panel(weights, active)
    if w.shape != x.shape or np.any(w < 0):
        raise ValueError("Regression weights must be aligned and nonnegative")
    result = np.full_like(x, np.nan)
    for i, (row, f, weight) in enumerate(zip(x, factors, w)):
        keep = (
            np.isfinite(row)
            & np.isfinite(f).all(axis=1)
            & np.isfinite(weight)
            & (weight > 0)
        )
        if keep.sum() <= f.shape[1] + 1:
            continue
        a = np.column_stack((np.ones(keep.sum()), f[keep]))
        scale = np.sqrt(weight[keep])
        coefs, _, rank_, _ = np.linalg.lstsq(
            a * scale[:, None], row[keep] * scale, rcond=None
        )
        if rank_ == a.shape[1]:
            result[i, keep] = row[keep] - a @ coefs
    return result


beta_neutralize = residualize
market_neutralize = demean


def group_neutralize(x, groups, *, active=None):
    """Demean separately in explicitly supplied symbol groups, leaving singleton groups missing."""
    x = _panel(x, active)
    groups = np.asarray(groups)
    if groups.shape != (x.shape[1],):
        raise ValueError("Supply one group label per symbol")
    result = np.full_like(x, np.nan)
    for group in np.unique(groups):
        mask = groups == group
        result[:, mask] = demean(x[:, mask])
    return result


def liquidity_weighted_normalize(x, liquidity, *, gross=1.0, active=None):
    """Multiply scores by contemporaneous nonnegative liquidity, then normalize gross exposure."""
    x, liquidity = _panel(x, active), _panel(liquidity, active)
    if liquidity.shape != x.shape or np.any(liquidity < 0):
        raise ValueError("Liquidity must be aligned and nonnegative")
    return gross_normalize(x * liquidity, gross=gross)


__all__ = """rank percentile mean std zscore robust_zscore demean winsorize quantile_groups top_k bottom_k
gross_normalize long_short_normalize market_neutralize beta_neutralize residualize group_neutralize
liquidity_weighted_normalize""".split()
