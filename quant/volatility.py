"""Causal volatility estimators for regularly sampled 24/7 markets."""

import numpy as np

from . import math as qm, ts
from ._core import paired, time_array

YEAR_MS = 365 * 86_400_000


def _factor(annualize, interval_ms):
    if not annualize:
        return 1.0
    if interval_ms is None or interval_ms <= 0:
        raise ValueError("Annualization requires a positive sampling interval")
    return YEAR_MS / interval_ms


def _std(variation, window, annualize, interval_ms, min_periods):
    return qm.sqrt(
        np.maximum(
            0,
            ts.rolling_mean(
                variation, window, interval_ms=interval_ms, min_periods=min_periods
            ),
        )
        * _factor(annualize, interval_ms)
    )


def realized_volatility(
    returns, window, *, annualize=False, interval_ms=None, min_periods=None
):
    """Root mean squared return per bar, optionally scaled to a 365-day year."""
    return _std(time_array(returns) ** 2, window, annualize, interval_ms, min_periods)


def ewma_volatility(returns, span, *, annualize=False, interval_ms=None, **kwargs):
    """Square root of recursive exponentially weighted squared returns."""
    return qm.sqrt(
        ts.ema(time_array(returns) ** 2, span, interval_ms=interval_ms, **kwargs)
        * _factor(annualize, interval_ms)
    )


def _log_ratio(a, b):
    return np.subtract(*paired(qm.log(a), qm.log(b)))


def parkinson(
    high, low, window, *, annualize=False, interval_ms=None, min_periods=None
):
    """Parkinson volatility from log(high/low)^2/(4 log(2))."""
    return _std(
        _log_ratio(high, low) ** 2 / (4 * np.log(2)),
        window,
        annualize,
        interval_ms,
        min_periods,
    )


def garman_klass(
    open,
    high,
    low,
    close,
    window,
    *,
    annualize=False,
    interval_ms=None,
    min_periods=None,
):
    """Garman–Klass volatility from log-range and log close/open."""
    r, c = paired(_log_ratio(high, low), _log_ratio(close, open))
    return _std(
        0.5 * r * r - (2 * np.log(2) - 1) * c * c,
        window,
        annualize,
        interval_ms,
        min_periods,
    )


def _rs(open, high, low, close):
    if len({np.shape(x) for x in (open, high, low, close)}) != 1:
        raise ValueError("OHLC axes must match")
    return _log_ratio(high, open) * _log_ratio(high, close) + _log_ratio(
        low, open
    ) * _log_ratio(low, close)


def rogers_satchell(
    open,
    high,
    low,
    close,
    window,
    *,
    annualize=False,
    interval_ms=None,
    min_periods=None,
):
    """Rogers–Satchell volatility, with separate high/open, high/close, low/open and low/close terms."""
    return _std(
        _rs(open, high, low, close), window, annualize, interval_ms, min_periods
    )


def yang_zhang(open, high, low, close, window, *, session_ms, annualize=False):
    """Yang–Zhang volatility. Each observation must be one explicit session, e.g. a UTC day."""
    from ._core import bars

    n = bars(window, session_ms)
    if n < 2 or session_ms <= 0:
        raise ValueError("At least two positive-length sessions are required")
    overnight = _log_ratio(open, ts.lag(close))
    close_open = _log_ratio(close, open)
    k = 0.34 / (1.34 + (n + 1) / (n - 1))
    result = (
        ts.rolling_variance(overnight, n)
        + k * ts.rolling_variance(close_open, n)
        + (1 - k) * ts.rolling_mean(_rs(open, high, low, close), n)
    )
    return qm.sqrt(np.maximum(0, result) * _factor(annualize, session_ms))


def semivariance(returns, window, *, side="down", interval_ms=None, min_periods=None):
    """Average squared returns on the selected side of zero; other finite returns contribute zero."""
    a = time_array(returns)
    if side not in {"down", "up"}:
        raise ValueError("side must be down or up")
    piece = np.minimum(a, 0) if side == "down" else np.maximum(a, 0)
    return ts.rolling_mean(
        piece**2, window, interval_ms=interval_ms, min_periods=min_periods
    )


def downside_volatility(
    returns, window, *, annualize=False, interval_ms=None, min_periods=None
):
    """Square root of downside semivariance."""
    return qm.sqrt(
        semivariance(
            returns,
            window,
            side="down",
            interval_ms=interval_ms,
            min_periods=min_periods,
        )
        * _factor(annualize, interval_ms)
    )


def upside_volatility(
    returns, window, *, annualize=False, interval_ms=None, min_periods=None
):
    """Square root of upside semivariance."""
    return qm.sqrt(
        semivariance(
            returns, window, side="up", interval_ms=interval_ms, min_periods=min_periods
        )
        * _factor(annualize, interval_ms)
    )


def volatility_of_volatility(volatility, window, **kwargs):
    """Trailing sample standard deviation of a volatility series."""
    return ts.rolling_std(volatility, window, **kwargs)


def rolling_range(high, low, window, **kwargs):
    """Trailing highest high less lowest low."""
    high, low = paired(high, low)
    return ts.rolling_max(high, window, **kwargs) - ts.rolling_min(
        low, window, **kwargs
    )


def drawdown(equity):
    """Relative drawdown from a causal running peak within each finite run."""
    return qm.safe_divide(equity, ts.expanding(equity, "max")) - 1


def volatility_percentile(volatility, window, **kwargs):
    """Trailing midpoint percentile of the latest volatility."""
    return ts.rolling_percentile(volatility, window, **kwargs)


def trend_strength(price, window, **kwargs):
    """Absolute net movement divided by sum of absolute one-bar movements."""
    return qm.safe_divide(
        np.abs(ts.diff(price, window, interval_ms=kwargs.get("interval_ms"))),
        ts.rolling_sum(np.abs(ts.diff(price)), window, **kwargs),
    )


def classify_regime(score, lower, upper):
    """Classify finite scores as 0 (low), 1 (middle) or 2 (high) using supplied thresholds."""
    x = time_array(score)
    if lower >= upper:
        raise ValueError("Regime thresholds must be increasing")
    return np.where(np.isfinite(x), (x >= lower).astype(int) + (x >= upper), np.nan)


def detect_jumps(returns, window, *, threshold=5.0, **kwargs):
    """Flag returns exceeding a threshold times preceding realized volatility."""
    if threshold <= 0:
        raise ValueError("Threshold must be positive")
    baseline = ts.lag(realized_volatility(returns, window, **kwargs))
    return (
        np.isfinite(baseline)
        & (baseline > 0)
        & (np.abs(time_array(returns)) > threshold * baseline)
    )


__all__ = """realized_volatility ewma_volatility parkinson garman_klass rogers_satchell yang_zhang
semivariance downside_volatility upside_volatility volatility_of_volatility rolling_range drawdown
volatility_percentile trend_strength classify_regime detect_jumps""".split()
