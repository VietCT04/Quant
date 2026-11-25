"""Descriptive statistics and explicit sample-level inference for research."""

from dataclasses import dataclass

import numpy as np
from scipy import stats as scipy
from statsmodels.regression.linear_model import OLS, WLS
from statsmodels.tsa import stattools

from . import math as qm, ts
from ._core import array

pearson = qm.correlation
rolling_regression = ts.rolling_regression
rolling_beta = ts.rolling_beta


def _pair(x, y):
    x, y = array(x), array(y)
    if x.ndim != 1 or x.shape != y.shape:
        raise ValueError("Paired one-dimensional samples are required")
    mask = np.isfinite(x) & np.isfinite(y)
    return x[mask], y[mask]


def spearman(x, y):
    """Spearman correlation of average ranks of paired finite observations."""
    x, y = _pair(x, y)
    if len(x) < 2 or np.ptp(x) == 0 or np.ptp(y) == 0:
        return np.nan
    return float(scipy.spearmanr(x, y).statistic)


def kendall(x, y):
    """Kendall tau-b of paired finite observations."""
    x, y = _pair(x, y)
    return float(scipy.kendalltau(x, y).statistic) if len(x) >= 2 else np.nan


def _complete(x):
    x = array(x)
    if x.ndim != 1 or not len(x) or not np.isfinite(x).all():
        raise ValueError(
            "Supply a complete regular one-dimensional sample; do not collapse time gaps"
        )
    return x


def autocorrelation(x, lags):
    """Biased autocorrelation at lags 0..lags; complete regular sample required."""
    return stattools.acf(_complete(x), nlags=lags, fft=True)


def partial_autocorrelation(x, lags, *, method="ywm"):
    """Statsmodels partial ACF at lags 0..lags, default Yule–Walker MLE."""
    return stattools.pacf(_complete(x), nlags=lags, method=method)


def cross_correlation(x, y, lags):
    """Pearson correlation by lag from -lags through +lags; positive lag means x leads y."""
    x, y = _complete(x), _complete(y)
    if x.shape != y.shape or not 0 <= lags < len(x) - 1:
        raise ValueError(
            "Cross-correlation requires matching samples and sufficient overlaps"
        )
    return {
        i: float(
            pearson(x[: len(x) - i], y[i:])
            if i >= 0
            else pearson(x[-i:], y[: len(y) + i])
        )
        for i in range(-lags, lags + 1)
    }


@dataclass(frozen=True)
class Regression:
    """Fitted coefficients, uncertainty and residuals indexed on the source observations."""

    coefficients: np.ndarray
    standard_errors: np.ndarray
    fitted: np.ndarray
    residuals: np.ndarray
    r_squared: float
    observations: int
    covariance_type: str


def regression(y, x, *, weights=None, intercept=True, hac_lags=None):
    """OLS or WLS with optional Bartlett HAC covariance; rows are kept on the original time axis."""
    y, x = array(y), array(x)
    if x.ndim == 1:
        x = x[:, None]
    if y.ndim != 1 or x.ndim != 2 or len(x) != len(y):
        raise ValueError("Expected one response and an aligned predictor matrix")
    w = np.ones_like(y) if weights is None else array(weights)
    if w.shape != y.shape or np.any(w < 0):
        raise ValueError("Regression weights must be aligned and nonnegative")
    if intercept:
        x = np.column_stack([np.ones_like(y), x])
    keep = np.isfinite(y) & np.isfinite(x).all(axis=1) & np.isfinite(w) & (w > 0)
    if keep.sum() <= x.shape[1] or np.linalg.matrix_rank(x[keep]) < x.shape[1]:
        raise ValueError(
            "Regression requires a full-rank design with residual degrees of freedom"
        )
    if hac_lags is not None and (
        not keep.all() or not isinstance(hac_lags, int) or not 0 <= hac_lags < len(y)
    ):
        raise ValueError(
            "HAC requires contiguous complete input and a valid maximum lag"
        )
    model = (
        OLS(y[keep], x[keep])
        if weights is None
        else WLS(y[keep], x[keep], weights=w[keep])
    )
    result = (
        model.fit()
        if hac_lags is None
        else model.fit(
            cov_type="HAC", cov_kwds={"maxlags": hac_lags, "use_correction": True}
        )
    )
    fitted, residual = np.full_like(y, np.nan), np.full_like(y, np.nan)
    fitted[keep], residual[keep] = result.fittedvalues, result.resid
    return Regression(
        result.params,
        result.bse,
        fitted,
        residual,
        float(result.rsquared),
        int(keep.sum()),
        result.cov_type,
    )


linear_regression = regression
weighted_regression = regression
multiple_regression = regression


def beta(y, x):
    """OLS slope with intercept on paired finite observations."""
    return regression(y, x).coefficients[1]


def residuals(y, x, **kwargs):
    """Residuals of OLS/WLS on the original response axis."""
    return regression(y, x, **kwargs).residuals


def distribution_summary(x):
    """Count, mean, sample standard deviation, median, IQR, MAD and tail moments."""
    x = array(x).ravel()
    return {
        "n": int(qm.finite(x).sum()),
        "mean": float(qm.mean(x)),
        "std": float(qm.std(x)),
        "skewness": float(qm.skewness(x)),
        "excess_kurtosis": float(qm.kurtosis(x)),
        **qm.robust_summary(x),
    }


def empirical_cdf(x):
    """Return ordered unique finite values and their inclusive empirical probabilities."""
    x = array(x).ravel()
    values, count = np.unique(x[np.isfinite(x)], return_counts=True)
    return values, count.cumsum() / count.sum() if len(count) else np.empty(0)


quantiles = qm.quantile


def adf(x, *, maxlag=None, regression="c", autolag="AIC"):
    """Augmented Dickey–Fuller test; null is a unit root."""
    result = stattools.adfuller(
        _complete(x), maxlag=maxlag, regression=regression, autolag=autolag
    )
    return {
        "statistic": result[0],
        "pvalue": result[1],
        "lags": result[2],
        "n": result[3],
        "critical": result[4],
    }


def kpss(x, *, regression="c", nlags="auto"):
    """KPSS test; null is level or trend stationarity under the selected specification."""
    value, p, lags, critical = stattools.kpss(
        _complete(x), regression=regression, nlags=nlags
    )
    return {"statistic": value, "pvalue": p, "lags": lags, "critical": critical}


def cointegration(y, x, *, trend="c", maxlag=None, autolag="aic"):
    """Augmented Engle–Granger cointegration test for aligned complete I(1) samples."""
    x, y = _complete(x), _complete(y)
    if x.shape != y.shape:
        raise ValueError("Samples must align")
    value, p, critical = stattools.coint(
        y, x, trend=trend, maxlag=maxlag, autolag=autolag
    )
    return {"statistic": value, "pvalue": p, "critical": critical}


def hurst(x, *, estimator, scales=(4, 8, 16, 32, 64)):
    """Estimate H by a log-log slope of increment standard deviation or rescaled range."""
    x = _complete(x)
    if estimator not in {"increments", "rescaled_range"}:
        raise ValueError("Choose increments or rescaled_range")
    points = []
    for n in sorted(set(scales)):
        if not isinstance(n, int) or n < 2:
            raise ValueError("Scales must be integer lengths >=2")
        if n >= len(x):
            continue
        if estimator == "increments":
            metric = np.std(x[n:] - x[:-n], ddof=1) if len(x) - n > 1 else np.nan
        else:
            segments = x[: len(x) // n * n].reshape(-1, n)
            centered = segments - segments.mean(axis=1, keepdims=True)
            metric = qm.mean(
                qm.safe_divide(
                    np.ptp(np.cumsum(centered, axis=1), axis=1),
                    segments.std(axis=1, ddof=1),
                )
            )
        if np.isfinite(metric) and metric > 0:
            points.append((n, metric))
    if len(points) < 3:
        raise ValueError("Hurst estimate requires three valid scales")
    scales, values = np.log(np.asarray(points)).T
    return float(np.polyfit(scales, values, 1)[0])


def confidence_interval(x, *, confidence=0.95, hac_lags):
    """Normal-approximation mean confidence interval using HAC standard errors."""
    x = _complete(x)
    if not 0 < confidence < 1:
        raise ValueError("Confidence must be in (0,1)")
    if not isinstance(hac_lags, int) or not 0 <= hac_lags < len(x):
        raise ValueError("An explicit valid inference lag is required")
    mean = float(x.mean())
    if not np.ptp(x):
        return mean, mean
    fit = regression(x, np.empty((len(x), 0)), hac_lags=hac_lags)
    radius = scipy.norm.ppf((1 + confidence) / 2) * fit.standard_errors[0]
    return mean - radius, mean + radius


def bootstrap(x, statistic=np.mean, *, repetitions=1000, block_size=1, seed=0):
    """Circular moving-block bootstrap distribution; block_size=1 assumes independent sampling."""
    x = _complete(x)
    if not 1 <= block_size <= len(x) or repetitions < 1:
        raise ValueError("Invalid bootstrap size")
    rng = np.random.default_rng(seed)
    offset = np.arange(block_size)
    blocks = (len(x) + block_size - 1) // block_size
    output = []
    for _ in range(repetitions):
        indices = (rng.integers(len(x), size=(blocks, 1)) + offset).ravel()[
            : len(x)
        ] % len(x)
        output.append(statistic(x[indices]))
    return np.asarray(output)


def block_bootstrap(x, statistic=np.mean, *, block_size, repetitions=1000, seed=0):
    """Circular moving-block bootstrap requiring an explicit dependency block size."""
    return bootstrap(
        x, statistic, block_size=block_size, repetitions=repetitions, seed=seed
    )


def adjust_pvalues(pvalues, *, method="holm"):
    """Adjust a registered test family with Holm, Bonferroni, BH or BY."""
    from statsmodels.stats.multitest import multipletests

    x = array(pvalues)
    if x.ndim != 1 or not np.isfinite(x).all() or np.any((x < 0) | (x > 1)):
        raise ValueError("Supply a complete finite p-value family in [0,1]")
    if method not in {"holm", "bonferroni", "fdr_bh", "fdr_by"}:
        raise ValueError("Unknown correction")
    return multipletests(x, method=method)[1] if len(x) else x.copy()


__all__ = """pearson spearman kendall autocorrelation partial_autocorrelation cross_correlation regression
linear_regression weighted_regression multiple_regression rolling_regression rolling_beta beta residuals
distribution_summary empirical_cdf quantiles adf kpss cointegration hurst confidence_interval bootstrap
block_bootstrap adjust_pvalues""".split()
