"""Real-valued NumPy transforms and finite-observation reductions."""

import functools
import inspect
import warnings
import numpy as np
from scipy import stats

from ._core import array, divide as safe_divide


def _transform(function, description):
    @functools.wraps(function)
    def apply(*operands):
        operands = np.broadcast_arrays(*(array(x) for x in operands))
        with np.errstate(all="ignore"):
            result = function(*operands)
        valid = np.isfinite(result)
        for operand in operands:
            valid &= np.isfinite(operand)
        return np.where(valid, result, np.nan)

    apply.__doc__ = description
    apply.__signature__ = inspect.Signature(
        inspect.Parameter(name, inspect.Parameter.POSITIONAL_ONLY)
        for name in ("x", "y")[: function.nin]
    )
    return apply


absolute = _transform(np.abs, "Absolute magnitude.")
sign = _transform(np.sign, "Sign of real values in {-1, 0, 1}.")
sqrt = _transform(np.sqrt, "Square root on nonnegative values.")
power = _transform(
    np.power, "Real exponentiation; non-real and overflow results become missing."
)
log = _transform(np.log, "Natural logarithm on positive values.")
exp = _transform(np.exp, "Natural exponential; overflow becomes missing.")
log1p = _transform(np.log1p, "Accurate log(1+x) for x > -1.")
expm1 = _transform(np.expm1, "Accurate exp(x)-1 near zero.")
minimum = _transform(np.minimum, "Elementwise minimum.")
maximum = _transform(np.maximum, "Elementwise maximum.")


def clip(x, lower, upper):
    """Clamp values to inclusive ordered bounds."""
    if np.any(array(lower) > array(upper)):
        raise ValueError("Bounds are reversed")
    return np.clip(array(x), lower, upper)


def where(condition, a, b):
    """Select from real operands with a boolean condition."""
    if np.asarray(condition).dtype.kind != "b":
        raise TypeError("Condition must be boolean")
    return np.where(condition, array(a), array(b))


def finite(x):
    """Identify finite values."""
    return np.isfinite(array(x))


def missing(x):
    """Identify NaN and infinite values."""
    return ~finite(x)


def _finite(x):
    x = array(x)
    return np.where(np.isfinite(x), x, np.nan)


def stable_sum(x, *, axis=0):
    """Pairwise float64 summation of finite observations; all-missing slices produce NaN."""
    x = _finite(x)
    if axis is not None:
        x = np.ascontiguousarray(np.moveaxis(x, axis, -1))
        axis = -1
    return np.where(np.isfinite(x).any(axis=axis), np.nansum(x, axis=axis), np.nan)


def weighted_mean(x, weights, *, axis=0):
    """Sum of x*w divided by paired weight sum; nonnegative finite weights only."""
    x, weights = np.broadcast_arrays(array(x), array(weights))
    if np.any(weights < 0):
        raise ValueError("Weights must be nonnegative")
    weights = np.where(np.isfinite(x), weights, np.nan)
    return safe_divide(
        stable_sum(x * weights, axis=axis), stable_sum(weights, axis=axis)
    )


def mean(x, *, axis=0):
    """Arithmetic mean of finite observations."""
    return _mean(_finite(x), axis=axis)


def _mean(x, *, axis, keepdims=False):
    with warnings.catch_warnings():
        warnings.filterwarnings(
            "ignore", message="Mean of empty slice", category=RuntimeWarning
        )
        return np.nanmean(x, axis=axis, keepdims=keepdims)


def median(x, *, axis=0):
    """Median of finite observations."""
    return np.nanmedian(_finite(x), axis=axis)


def quantile(x, q, *, axis=0):
    """Linearly interpolated quantile of finite observations."""
    return np.nanquantile(_finite(x), q, axis=axis)


def variance(x, *, axis=0, ddof=1):
    """Finite-observation variance with denominator n-ddof."""
    with warnings.catch_warnings():
        warnings.filterwarnings(
            "ignore",
            message="Degrees of freedom <= 0 for slice",
            category=RuntimeWarning,
        )
        return np.nanvar(_finite(x), axis=axis, ddof=ddof)


def std(x, *, axis=0, ddof=1):
    """Square root of finite-observation variance."""
    return sqrt(variance(x, axis=axis, ddof=ddof))


def geometric_mean(x, *, axis=0):
    """Exp(mean(log(x))) for positive finite observations; any nonpositive value invalidates its slice."""
    x = _finite(x)
    return np.where((x <= 0).any(axis=axis), np.nan, exp(mean(log(x), axis=axis)))


def harmonic_mean(x, *, axis=0):
    """Reciprocal of mean reciprocal for positive finite observations."""
    x = _finite(x)
    return np.where(
        (x <= 0).any(axis=axis),
        np.nan,
        safe_divide(1, mean(safe_divide(1, x), axis=axis)),
    )


def covariance(x, y, *, axis=0, ddof=1):
    """Paired finite-observation covariance with denominator n-ddof."""
    x, y = array(x), array(y)
    if x.shape != y.shape:
        raise ValueError("Covariance requires paired shapes")
    valid = np.isfinite(x) & np.isfinite(y)
    x, y = np.where(valid, x, np.nan), np.where(valid, y, np.nan)
    xc = x - _mean(x, axis=axis, keepdims=True)
    yc = y - _mean(y, axis=axis, keepdims=True)
    n = valid.sum(axis=axis)
    return np.where(
        n > ddof, safe_divide(np.nansum(xc * yc, axis=axis), n - ddof), np.nan
    )


def correlation(x, y, *, axis=0):
    """Pearson correlation on the same paired finite observations."""
    x, y = array(x), array(y)
    if x.shape != y.shape:
        raise ValueError("Correlation requires paired shapes")
    valid = np.isfinite(x) & np.isfinite(y)
    count = valid.sum(axis=axis, keepdims=True)
    x, y = np.where(valid, x, 0.0), np.where(valid, y, 0.0)
    mean_x = safe_divide(x.sum(axis=axis, keepdims=True), count)
    mean_y = safe_divide(y.sum(axis=axis, keepdims=True), count)
    xc = np.where(valid, x - mean_x, 0.0)
    yc = np.where(valid, y - mean_y, 0.0)
    # The sample normalization cancels between covariance and the two standard deviations.
    with np.errstate(over="ignore", invalid="ignore", under="ignore"):
        result = safe_divide(
            (xc * yc).sum(axis=axis),
            np.sqrt((xc * xc).sum(axis=axis)) * np.sqrt((yc * yc).sum(axis=axis)),
        )
    return np.clip(result, -1, 1)


def skewness(x, *, axis=0):
    """Adjusted Fisher–Pearson sample skewness."""
    return stats.skew(_finite(x), axis=axis, bias=False, nan_policy="omit")


def kurtosis(x, *, axis=0):
    """Bias-corrected excess kurtosis."""
    return stats.kurtosis(_finite(x), axis=axis, bias=False, nan_policy="omit")


def mad(x, *, axis=0, scale=1.0):
    """Median absolute deviation divided by scale; use scale='normal' for Gaussian consistency."""
    return stats.median_abs_deviation(
        _finite(x), axis=axis, scale=scale, nan_policy="omit"
    )


def robust_summary(x, *, axis=0):
    """Median, interquartile range and raw median absolute deviation."""
    return {
        "median": median(x, axis=axis),
        "iqr": quantile(x, 0.75, axis=axis) - quantile(x, 0.25, axis=axis),
        "mad": mad(x, axis=axis),
    }


__all__ = """absolute sign sqrt power log exp log1p expm1 minimum maximum clip safe_divide where finite
missing stable_sum weighted_mean geometric_mean harmonic_mean mean median quantile variance std covariance
correlation skewness kurtosis mad robust_summary""".split()
