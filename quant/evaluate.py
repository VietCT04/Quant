"""Forward labels and development-only predictive diagnostics."""

from dataclasses import dataclass

import numpy as np

from . import cs, stats, ts
from ._core import array, bars, divide, paired
from .data import Block
from .validation import _check

SELECTION_BIAS = (
    "Liquid 50 membership was selected using November 2026 activity and volume."
)


@dataclass(frozen=True)
class Labels:
    """Immutable-by-interface research labels with their dependency time."""

    timestamps_ms: np.ndarray
    symbols: tuple
    decision_ms: np.ndarray
    end_ms: np.ndarray
    horizon: int
    _returns: np.ndarray
    research_only = True

    def __array__(self, dtype=None, copy=None):
        raise TypeError("Use research_values for explicit research-only extraction")


def forward_returns(block, horizon, *, field="close", logarithmic=False):
    """Endpoint returns over a scheduled horizon, invalidating any unknown intermediate coverage."""
    if not isinstance(block, Block) or not len(block.timestamps_ms):
        raise ValueError("Forward labels require a nonempty aligned Block")
    _check(
        int(block.timestamps_ms[0]), int(block.timestamps_ms[-1]) + block.interval_ms
    )
    n = bars(horizon, block.interval_ms)
    x = block.values[field]
    out = np.full_like(x, np.nan)
    end = np.full(len(x), -1, dtype="i8")
    if n < len(x):
        count = np.vstack(
            [
                np.zeros((1, x.shape[1]), dtype="i8"),
                np.cumsum(~block.valid[field], axis=0, dtype="i8"),
            ]
        )
        covered = count[n + 1 :] - count[: -(n + 1)] == 0
        with np.errstate(divide="ignore", invalid="ignore"):
            computed = (
                (np.log(x[n:]) - np.log(x[:-n]))
                if logarithmic
                else divide(x[n:], x[:-n]) - 1
            )
        endpoint = block.available_at_ms[n:]
        out[:-n] = np.where(covered & np.isfinite(computed), computed, np.nan)
        end[:-n] = endpoint
    out.flags.writeable = False
    return Labels(
        block.timestamps_ms.copy(),
        block.symbols,
        block.available_at_ms.copy(),
        end,
        n,
        out,
    )


def research_values(labels):
    """Explicitly view extracted future returns for research analysis only."""
    if not isinstance(labels, Labels):
        raise TypeError("Expected forward Labels")
    return labels._returns.copy()


def iter_forward_returns(
    blocks, horizon, *, field="close", logarithmic=False, memory_bytes=256 << 20
):
    """Emit aligned source and diagnostic labels with bounded forward lookahead and unknown edge outcomes."""
    from dataclasses import replace
    from .data import _join, _slice

    pending = None
    for block in blocks:
        if not isinstance(block, Block) or not len(block.timestamps_ms):
            raise ValueError("Forward labels require nonempty continuous market blocks")
        n = bars(horizon, block.interval_ms)
        if pending is not None and (
            pending.symbols != block.symbols
            or pending.units != block.units
            or pending.interval_ms != block.interval_ms
        ):
            raise ValueError("Forward label streams require consistent axes and units")
        size = sum(x.nbytes for x in block.values.values())
        if pending is not None:
            size += sum(x.nbytes for x in pending.values.values())
        if size * 4 > memory_bytes:
            raise MemoryError(
                "Forward horizon and block exceed the working array budget"
            )
        pending = block if pending is None else _join(pending, block)
        stop = len(pending.timestamps_ms) - n
        if stop <= 0:
            continue
        labels = forward_returns(pending, n, field=field, logarithmic=logarithmic)
        prefix = replace(
            labels,
            timestamps_ms=labels.timestamps_ms[:stop],
            decision_ms=labels.decision_ms[:stop],
            end_ms=labels.end_ms[:stop],
            _returns=labels._returns[:stop],
        )
        yield _slice(pending, 0, stop), prefix
        pending = _slice(pending, stop, None)
    if pending is not None:
        yield (
            pending,
            forward_returns(pending, horizon, field=field, logarithmic=logarithmic),
        )


def _pair(scores, labels):
    if not isinstance(labels, Labels):
        raise TypeError("Forward labels must be created with forward_returns")
    if isinstance(scores, Block):
        if (
            not np.array_equal(scores.timestamps_ms, labels.timestamps_ms)
            or scores.symbols != labels.symbols
        ):
            raise ValueError("Scores and labels require exact aligned axes")
        if len(scores.values) != 1:
            raise ValueError("Provide one score field")
        if len(scores.timestamps_ms):
            _check(
                int(scores.timestamps_ms[0]),
                int(scores.timestamps_ms[-1]) + scores.interval_ms,
            )
        permissible = scores.available_at_ms <= labels.decision_ms
        scores = np.where(
            permissible[:, None], next(iter(scores.values.values())), np.nan
        )
    return paired(scores, labels._returns)


def information_coefficient(scores, labels, *, method="spearman", axis=1):
    """Pearson or Spearman predictive association across symbols (axis=1) or through time (axis=0)."""
    x, y = _pair(scores, labels)
    if axis not in (0, 1) or method not in {"pearson", "spearman"}:
        raise ValueError("Use axis 0 or 1 and Pearson or Spearman")
    if method == "spearman":
        from scipy.stats import rankdata

        keep = np.isfinite(x) & np.isfinite(y)
        x, y = (
            rankdata(np.where(keep, z, np.nan), axis=axis, nan_policy="omit")
            for z in (x, y)
        )
    return stats.pearson(x, y, axis=axis)


def per_symbol_ic(scores, labels, *, method="spearman"):
    """Temporal information coefficient per symbol."""
    return information_coefficient(scores, labels, method=method, axis=0)


cross_sectional_ic = information_coefficient


def icir(ic, *, periods_per_year=None):
    """Mean/sampled standard deviation of IC, optionally annualized as a descriptive statistic."""
    from . import math as qm

    if periods_per_year is not None and periods_per_year <= 0:
        raise ValueError("Sampling periods per year must be positive")
    x = array(ic)
    return divide(qm.mean(x), qm.std(x)) * np.sqrt(
        1 if periods_per_year is None else periods_per_year
    )


def rolling_ic(
    scores, labels, window, *, method="spearman", interval_ms=None, min_periods=None
):
    """Trailing mean of observed cross-sectional information coefficients."""
    return ts.rolling_mean(
        information_coefficient(scores, labels, method=method),
        window,
        interval_ms=interval_ms,
        min_periods=min_periods,
    )


def alpha_decay(scores, block, horizons, *, method="spearman"):
    """Average timestamp-level IC at each supplied forward horizon."""
    from . import math as qm

    return {
        h: float(
            qm.mean(
                information_coefficient(
                    scores, forward_returns(block, h), method=method
                )
            )
        )
        for h in horizons
    }


def quantile_returns(scores, labels, *, groups=5):
    """Equal-weight mean label by timestamp and midpoint-rank quantile, with observation counts."""
    x, y = _pair(scores, labels)
    group = cs.quantile_groups(x, groups=groups)
    counts = np.column_stack(
        [((group == i) & np.isfinite(y)).sum(axis=1) for i in range(groups)]
    )
    sums = np.column_stack(
        [np.nansum(np.where(group == i, y, np.nan), axis=1) for i in range(groups)]
    )
    return divide(sums, counts), counts


def quantile_spread(scores, labels, *, groups=5):
    """Top minus bottom same-timestamp quantile mean forward return."""
    results, _ = quantile_returns(scores, labels, groups=groups)
    return results[:, -1] - results[:, 0]


def signal_coverage(scores, *, active=None):
    """Fraction of eligible historical slots containing a finite score."""
    x = array(scores)
    active = np.ones(x.shape, dtype=bool) if active is None else ts._events(active)
    if x.shape != active.shape:
        raise ValueError("Active mask must align")
    return divide((np.isfinite(x) & active).sum(), active.sum())


def signal_persistence(scores):
    """Fraction of adjacent finite nonzero scores with unchanged sign."""
    x = array(scores)
    a, b = x[:-1], x[1:]
    valid = np.isfinite(a) & np.isfinite(b) & (a != 0) & (b != 0)
    return divide((valid & (np.sign(a) == np.sign(b))).sum(axis=0), valid.sum(axis=0))


def signal_autocorrelation(scores, *, lag=1):
    """Descriptive paired Pearson correlation with a scheduled lag, per symbol."""
    return stats.pearson(scores, ts.lag(scores, lag), axis=0)


def signal_turnover(weights):
    """One-way sum of absolute signed weight changes, NaN when any member is unobserved."""
    x = array(weights)
    if x.ndim != 2:
        raise ValueError("One weight row per timestamp is required")
    return np.sum(np.abs(ts.diff(x)), axis=1)


def conditional_returns(labels, condition):
    """Mean return and sample count among aligned observations satisfying a condition."""
    if not isinstance(labels, Labels):
        raise TypeError("Expected future Labels")
    condition = ts._events(condition)
    if condition.shape != labels._returns.shape:
        raise ValueError("Condition and labels must align")
    y = labels._returns[condition]
    y = y[np.isfinite(y)]
    return {"observations": len(y), "mean": float(y.mean()) if len(y) else np.nan}


def event_study(events, block, horizons):
    """Conditional forward-return mean and count for each pre-specified event horizon."""
    return {h: conditional_returns(forward_returns(block, h), events) for h in horizons}


def long_short_asymmetry(scores, labels):
    """Report separate unadjusted conditional forward returns for positive and negative scores."""
    x, _ = _pair(scores, labels)
    return {
        "long": conditional_returns(labels, x > 0),
        "short": conditional_returns(labels, x < 0),
    }


def regime_performance(scores, labels, regime, *, method="spearman"):
    """Descriptive cross-sectional IC grouped by supplied time-aligned regimes."""
    ic = information_coefficient(scores, labels, method=method)
    regime = np.asarray(regime)
    if regime.shape != ic.shape:
        raise ValueError("One regime per timestamp is required")
    return {
        str(g): {
            "observations": int(np.isfinite(ic[regime == g]).sum()),
            "mean_ic": float(np.nanmean(ic[regime == g])),
        }
        for g in np.unique(regime)
    }


def monthly_stability(scores, labels, *, method="spearman"):
    """Descriptive regime performance grouped into UTC calendar months."""
    months = labels.timestamps_ms.astype("datetime64[ms]").astype("datetime64[M]")
    return regime_performance(scores, labels, months, method=method)


def dependent_mean_test(values, *, hac_lags, overlap_bars=1, confidence=0.95):
    """Test a zero mean using HAC; lag must cover at least the specified label overlap."""
    from scipy.stats import norm

    if overlap_bars < 1 or hac_lags < overlap_bars - 1:
        raise ValueError("HAC lag must cover the specified overlap")
    x = stats._complete(values)
    ci = stats.confidence_interval(x, hac_lags=hac_lags, confidence=confidence)
    if np.ptp(x) == 0:
        return {
            "mean": float(x[0]),
            "confidence_interval": ci,
            "pvalue": np.nan,
            "hac_lags": hac_lags,
        }
    fit = stats.regression(x, np.empty((len(x), 0)), hac_lags=hac_lags)
    z = divide(fit.coefficients[0], fit.standard_errors[0])
    return {
        "mean": float(x.mean()),
        "confidence_interval": ci,
        "pvalue": float(2 * norm.sf(abs(z))),
        "hac_lags": hac_lags,
    }


adjust_pvalues = stats.adjust_pvalues


def alpha_correlation(first, second, *, axis=0):
    """Descriptive pairwise Pearson correlation of aligned alpha scores."""
    return stats.pearson(first, second, axis=axis)


def incremental_ic(candidate, controls, labels, *, method="spearman"):
    """Cross-sectional IC of the candidate residual after per-date regression on control scores."""
    return information_coefficient(
        cs.residualize(candidate, controls), labels, method=method
    )


from ._screen import (
    ScreenCandidate as ScreenCandidate,
    screen as screen,
    screen_decay as screen_decay,
    screen_walk_forward as screen_walk_forward,
)

__all__ = """screen screen_decay screen_walk_forward forward_returns iter_forward_returns research_values information_coefficient per_symbol_ic cross_sectional_ic icir rolling_ic
alpha_decay quantile_returns quantile_spread signal_coverage signal_persistence signal_autocorrelation
signal_turnover conditional_returns event_study long_short_asymmetry regime_performance monthly_stability
dependent_mean_test adjust_pvalues alpha_correlation incremental_ic""".split()
