"""Trailing scheduled-bar operators on [time] or [time, symbol] arrays."""

import numpy as np
import polars as pl
from scipy.signal import lfilter

from ._core import bars, divide, paired, time_array
from . import math as qm


def _map(x, function):
    x = time_array(x)
    if x.ndim == 1:
        return function(x)
    if not x.shape[1]:
        return x.copy()
    return np.column_stack([function(column) for column in x.T])


def _series(x):
    return pl.Series(np.where(np.isfinite(x), x, np.nan)).fill_nan(None)


def _window(window, minimum, interval):
    window = bars(window, interval)
    minimum = window if minimum is None else bars(minimum)
    if minimum > window:
        raise ValueError("min_periods cannot exceed the window")
    return window, minimum


def lag(x, periods=1, *, interval_ms=None):
    """Shift values backward in information time, with NaN for unavailable history."""
    x, n = time_array(x), bars(periods, interval_ms)
    result = np.full_like(x, np.nan)
    result[n:] = x[:-n]
    return result


def diff(x, periods=1, *, interval_ms=None):
    """Current value less its trailing lag over a fully observed path."""
    x = time_array(x)
    n = bars(periods, interval_ms)
    return np.where(_complete_path(x, n), x - lag(x, n), np.nan)


def pct_change(x, periods=1, *, interval_ms=None):
    """Current divided by lagged value minus one over a fully observed path."""
    x = time_array(x)
    n = bars(periods, interval_ms)
    return np.where(_complete_path(x, n), divide(x, lag(x, n)) - 1, np.nan)


returns = pct_change


def log_returns(x, periods=1, *, interval_ms=None):
    """Difference of natural logarithms of positive prices."""
    return diff(qm.log(x), periods, interval_ms=interval_ms)


def _complete_path(x, periods):
    good = np.zeros(x.shape, dtype=bool)
    if periods == 1:
        good[1:] = np.isfinite(x[1:]) & np.isfinite(x[:-1])
        return good
    if periods < len(x):
        prefix = np.concatenate(
            [
                np.zeros_like(x[:1], dtype="i8"),
                np.cumsum(~np.isfinite(x), axis=0, dtype="i8"),
            ]
        )
        good[periods:] = prefix[periods + 1 :] == prefix[: -(periods + 1)]
    return good


def cumulative_sum(x):
    """Cumulative sum, resetting at nonfinite gaps."""
    return _cum(x, np.cumsum)


def cumulative_product(x):
    """Cumulative product, resetting at nonfinite gaps."""
    return _cum(x, np.cumprod)


def _runs(mask):
    edges = np.flatnonzero(np.diff(np.r_[False, mask, False]))
    return zip(edges[::2], edges[1::2])


def _cum(x, operation):
    def calculate(a):
        out = np.full(len(a), np.nan)
        for lo, hi in _runs(np.isfinite(a)):
            out[lo:hi] = operation(a[lo:hi])
        return out

    return _map(x, calculate)


def expanding(x, statistic="mean", *, min_periods=1):
    """Expanding sum, mean, min, max or sample variance, restarting at gaps."""
    minimum = bars(min_periods)
    ops = {
        "sum": np.cumsum,
        "mean": lambda a: np.cumsum(a) / np.arange(1, len(a) + 1),
        "min": np.minimum.accumulate,
        "max": np.maximum.accumulate,
    }
    if statistic == "variance":

        def variance(a):
            shifted = a - a[0]
            n = np.arange(1, len(a) + 1)
            return divide(
                np.maximum(0, np.cumsum(shifted**2) - np.cumsum(shifted) ** 2 / n),
                n - 1,
            )

        operation = variance
    elif statistic in ops:
        operation = ops[statistic]
    else:
        raise ValueError("Unsupported expanding statistic")

    def warm(a):
        result = operation(a)
        result[: minimum - 1] = np.nan
        return result

    return _cum(x, warm)


def forward_fill(x, *, limit):
    """Carry a previous finite value through at most limit scheduled rows."""
    return _map(x, lambda a: _series(a).forward_fill(limit=bars(limit)).to_numpy())


def _rolling(method, description):
    def apply(x, window, *, min_periods=None, interval_ms=None):
        n, minimum = _window(window, min_periods, interval_ms)
        return _map(
            x, lambda a: getattr(_series(a), method)(n, min_samples=minimum).to_numpy()
        )

    apply.__doc__ = description
    return apply


rolling_sum = _rolling("rolling_sum", "Trailing sum of finite observations.")
rolling_mean = _rolling(
    "rolling_mean", "Trailing arithmetic mean of finite observations."
)
sma = rolling_mean
rolling_median = _rolling("rolling_median", "Trailing median of finite observations.")
rolling_min = _rolling("rolling_min", "Trailing finite minimum.")
rolling_max = _rolling("rolling_max", "Trailing finite maximum.")
rolling_variance = _rolling("rolling_var", "Trailing sample variance with ddof=1.")
rolling_std = _rolling("rolling_std", "Trailing sample standard deviation with ddof=1.")


def rolling_quantile(x, window, q, *, min_periods=None, interval_ms=None):
    """Trailing finite quantile with linear interpolation."""
    n, minimum = _window(window, min_periods, interval_ms)
    return _map(
        x,
        lambda a: (
            _series(a).rolling_quantile(q, "linear", n, min_samples=minimum).to_numpy()
        ),
    )


def rolling_count(x, window, *, interval_ms=None):
    """Count available observations even in partially warm windows."""
    return rolling_sum(
        np.isfinite(time_array(x)), window, min_periods=1, interval_ms=interval_ms
    )


def rolling_zscore(x, window, *, min_periods=None, interval_ms=None):
    """Trailing (x - mean) / sample standard deviation, including current observation."""
    options = {"min_periods": min_periods, "interval_ms": interval_ms}
    return divide(
        time_array(x) - rolling_mean(x, window, **options),
        rolling_std(x, window, **options),
    )


def _rolling_pair(x, y, window, minimum, interval, op):
    x, y = paired(x, y)
    n, minimum = _window(window, minimum, interval)

    def one(a, b):
        valid = np.isfinite(a) & np.isfinite(b)
        indices = np.flatnonzero(valid)
        if not len(indices):
            return np.full_like(a, np.nan)
        origin = indices[0]
        frame = pl.DataFrame(
            {
                "a": _series(np.where(valid, a - a[origin], np.nan)),
                "b": _series(np.where(valid, b - b[origin], np.nan)),
            }
        )
        out = (
            frame.select(op("a", "b", window_size=n, min_samples=minimum))
            .to_series()
            .to_numpy()
        )
        return np.where(np.isfinite(out), out, np.nan)

    return (
        one(x, y)
        if x.ndim == 1
        else np.column_stack([one(a, b) for a, b in zip(x.T, y.T)])
    )


def rolling_covariance(x, y, window, *, min_periods=None, interval_ms=None):
    """Trailing sample covariance on pairwise finite inputs."""
    return _rolling_pair(x, y, window, min_periods, interval_ms, pl.rolling_cov)


def rolling_correlation(x, y, window, *, min_periods=None, interval_ms=None):
    """Trailing Pearson correlation on pairwise finite inputs."""
    result = _rolling_pair(x, y, window, min_periods, interval_ms, pl.rolling_corr)
    return np.clip(result, -1, 1)


def rolling_regression(y, x, window, *, min_periods=None, interval_ms=None):
    """Trailing OLS of y on x, returning intercept and slope on paired finite observations."""
    y, x = paired(y, x)
    valid = np.isfinite(y) & np.isfinite(x)
    y, x = np.where(valid, y, np.nan), np.where(valid, x, np.nan)
    kw = {"min_periods": min_periods, "interval_ms": interval_ms}
    beta = divide(
        rolling_covariance(y, x, window, **kw), rolling_variance(x, window, **kw)
    )
    return rolling_mean(y, window, **kw) - beta * rolling_mean(x, window, **kw), beta


def rolling_beta(y, x, window, **kwargs):
    """Slope of trailing paired OLS with an intercept."""
    return rolling_regression(y, x, window, **kwargs)[1]


def rolling_residual(y, x, window, **kwargs):
    """Current y minus current fitted value from trailing OLS."""
    intercept, beta = rolling_regression(y, x, window, **kwargs)
    return time_array(y) - intercept - beta * time_array(x)


def _scan_window(function, description):
    def apply(x, window, *, min_periods=None, interval_ms=None):
        n, minimum = _window(window, min_periods, interval_ms)

        def one(a):
            out = np.full(len(a), np.nan)
            for j in range(minimum - 1, len(a)):
                window = a[max(0, j + 1 - n) : j + 1]
                ok = np.isfinite(window)
                if ok.sum() >= minimum:
                    out[j] = function(window, ok)
            return out

        return _map(x, one)

    apply.__doc__ = description
    return apply


rolling_argmin = _scan_window(
    lambda a, m: np.argmin(np.where(m, a, np.inf)),
    "Zero-based offset of first finite minimum from trailing window start.",
)
rolling_argmax = _scan_window(
    lambda a, m: np.argmax(np.where(m, a, -np.inf)),
    "Zero-based offset of first finite maximum from trailing window start.",
)
rolling_rank = _scan_window(
    lambda a, m: (
        1 + (a[m] < a[-1]).sum() + ((a[m] == a[-1]).sum() - 1) / 2 if m[-1] else np.nan
    ),
    "Current observation's one-based average tie rank in its trailing finite window.",
)
rolling_percentile = _scan_window(
    lambda a, m: (
        ((a[m] < a[-1]).sum() + 0.5 * (a[m] == a[-1]).sum()) / m.sum()
        if m[-1]
        else np.nan
    ),
    "Current observation's midpoint percentile in its trailing finite window.",
)
rolling_mad = _scan_window(
    lambda a, m: qm.mad(a[m]), "Trailing raw median absolute deviation."
)
rolling_skewness = _scan_window(
    lambda a, m: qm.skewness(a[m]) if m.sum() >= 3 else np.nan,
    "Trailing adjusted sample skewness; at least three observations.",
)
rolling_kurtosis = _scan_window(
    lambda a, m: qm.kurtosis(a[m]) if m.sum() >= 4 else np.nan,
    "Trailing excess kurtosis; at least four observations.",
)


class EMAState:
    """Independent symbol states with chunk-invariant recursive EMA."""

    def __init__(self, span, *, interval_ms=None, min_periods=1, gap="reset"):
        self.span = bars(span, interval_ms)
        self.alpha = 2 / (self.span + 1)
        self.numerator = [self.alpha]
        self.denominator = [1.0, self.alpha - 1.0]
        self.minimum = bars(min_periods)
        if gap not in {"reset", "skip", "carry"}:
            raise ValueError("gap must be reset, skip or carry")
        self.gap = gap
        self.last = None
        self.seen = None

    def update(self, values):
        """Return the next EMA block while retaining the last state per symbol."""
        x = time_array(values)
        matrix = x[:, None] if x.ndim == 1 else x
        if self.last is None:
            self.last = np.full(matrix.shape[1], np.nan)
            self.seen = np.zeros(matrix.shape[1], dtype=int)
        if matrix.shape[1] != len(self.last):
            raise ValueError("Streaming symbol width changed")
        if not len(matrix):
            return x.copy()
        finite = np.isfinite(matrix)
        if finite.all():
            result, self.last = self._filter(matrix, self.last)
            counts = self.seen[None, :] + np.arange(1, len(matrix) + 1)[:, None]
            self.seen = counts[-1]
            result[counts < self.minimum] = np.nan
            return result[:, 0] if x.ndim == 1 else result
        result = np.full_like(matrix, np.nan)
        for index, column in enumerate(matrix.T):
            good = finite[:, index]
            runs = _runs(good) if self.gap == "reset" else [(0, len(column))]
            last, seen, previous_end = self.last[index], self.seen[index], 0
            for lo, hi in runs:
                if lo == hi:
                    continue
                if lo > previous_end:
                    last, seen = np.nan, 0
                data = column[lo:hi]
                observed = good[lo:hi]
                line = np.full(len(data), np.nan)
                if self.gap == "reset":
                    smoothed, last = self._filter(data, last)
                    line = smoothed
                    counts = seen + np.arange(1, len(data) + 1)
                else:
                    if observed.any():
                        smoothed, last = self._filter(data[observed], last)
                        line[observed] = smoothed
                    counts = seen + np.cumsum(observed)
                    if self.gap == "carry":
                        carried = np.r_[self.last[index], line]
                        indices = np.maximum.accumulate(
                            np.where(np.isfinite(carried), np.arange(len(carried)), 0)
                        )
                        line = carried[indices[1:]]
                seen = counts[-1]
                result[lo:hi, index] = np.where(counts >= self.minimum, line, np.nan)
                previous_end = hi
            if self.gap == "reset" and previous_end < len(column):
                last, seen = np.nan, 0
            self.last[index], self.seen[index] = last, seen
        return result[:, 0] if x.ndim == 1 else result

    def _filter(self, values, previous):
        warmed = np.isfinite(previous)
        weight = 1 - self.alpha
        if np.all(warmed):
            initial = np.expand_dims(weight * previous, axis=0)
            result, _ = lfilter(
                self.numerator, self.denominator, values, axis=0, zi=initial
            )
        else:
            result = np.empty_like(values)
            result[0] = np.where(
                warmed, self.alpha * values[0] + weight * previous, values[0]
            )
            if len(values) > 1:
                initial = np.expand_dims(weight * result[0], axis=0)
                result[1:], _ = lfilter(
                    self.numerator, self.denominator, values[1:], axis=0, zi=initial
                )
        return result, result[-1].copy()


def ema(x, span, *, interval_ms=None, min_periods=1, gap="reset"):
    """Recursive EMA with alpha=2/(span+1), seeded by first finite observation."""
    return EMAState(
        span, interval_ms=interval_ms, min_periods=min_periods, gap=gap
    ).update(x)


ewma = ema


def wilder(x, window, *, interval_ms=None):
    """Wilder smoothing with simple-average seed after a full finite run and alpha=1/window."""
    return WilderState(window, interval_ms=interval_ms).update(x)


class WilderState:
    """Chunk-invariant Wilder smoothing with one seed sum, count and running value per symbol."""

    def __init__(self, window, *, interval_ms=None):
        self.window = bars(window, interval_ms)
        self.alpha = 1 / self.window
        self.numerator = [self.alpha]
        self.denominator = [1.0, self.alpha - 1.0]
        self.seed = None
        self.count = None
        self.last = None

    def update(self, values):
        """Update Wilder smoothing; the seed is the mean of the first full finite window after each gap."""
        x = time_array(values)
        matrix = x[:, None] if x.ndim == 1 else x
        if self.seed is None:
            self.seed = np.zeros(matrix.shape[1])
            self.count = np.zeros(matrix.shape[1], dtype=int)
            self.last = np.full(matrix.shape[1], np.nan)
        if len(self.seed) != matrix.shape[1]:
            raise ValueError("Streaming symbol width changed")
        out = np.full_like(matrix, np.nan)
        for j, a in enumerate(matrix.T):
            seed, seen, last, end = self.seed[j], self.count[j], self.last[j], 0
            for start, stop in _runs(np.isfinite(a)):
                if start > end:
                    seed, seen, last = 0.0, 0, np.nan
                missing = self.window - seen
                if missing:
                    needed = min(missing, stop - start)
                    seed += a[start : start + needed].sum()
                    start += needed
                    seen += needed
                    if seen == self.window:
                        last = seed / self.window
                        out[start - 1, j] = last
                if start < stop:
                    line, _ = lfilter(
                        self.numerator,
                        self.denominator,
                        a[start:stop],
                        zi=[(1 - self.alpha) * last],
                    )
                    out[start:stop, j] = line
                    last = line[-1]
                end = stop
            if end < len(a):
                seed, seen, last = 0.0, 0, np.nan
            self.seed[j], self.count[j], self.last[j] = seed, seen, last
        return out[:, 0] if x.ndim == 1 else out


def wma(x, window, *, interval_ms=None):
    """Trailing linearly weighted average with weights 1..window and complete finite windows."""
    n = bars(window, interval_ms)

    def one(a):
        result = np.full_like(a, np.nan)
        if len(a) >= n:
            weights = np.arange(1, n + 1, dtype=float)
            result[n - 1 :] = np.convolve(a, (weights / weights.sum())[::-1], "valid")
        return result

    return _map(x, one)


linear_decay = wma
exponential_decay = ema


def ewm_covariance(x, y, span, **kwargs):
    """Exponentially weighted population covariance using paired observations."""
    x, y = paired(x, y)
    valid = np.isfinite(x) & np.isfinite(y)
    x, y = np.where(valid, x, np.nan), np.where(valid, y, np.nan)
    baseline_x, baseline_y = x.copy(), y.copy()
    # Use a fixed origin to reduce cancellation in raw second moments.
    for a, b in (
        zip(baseline_x.reshape(len(x), -1).T, baseline_y.reshape(len(y), -1).T)
        if len(x)
        else ()
    ):
        first = np.flatnonzero(np.isfinite(a))
        if len(first):
            a -= a[first[0]]
            b -= b[first[0]]
    return ema(baseline_x * baseline_y, span, **kwargs) - ema(
        baseline_x, span, **kwargs
    ) * ema(baseline_y, span, **kwargs)


def ewm_variance(x, span, **kwargs):
    """Exponentially weighted population variance."""
    return np.maximum(0, ewm_covariance(x, x, span, **kwargs))


def _events(x):
    if getattr(x, "research_only", False) or np.asarray(x).dtype.kind != "b":
        raise TypeError("Expected causal boolean events")
    return np.asarray(x)


def time_since_event(event):
    """Number of scheduled bars since last true, NaN before first occurrence."""
    event = _events(event)
    index = np.arange(len(event)).reshape((-1,) + (1,) * (event.ndim - 1))
    last = np.maximum.accumulate(np.where(event, index, -1), axis=0)
    return np.where(last < 0, np.nan, index - last)


def consecutive_true_count(event):
    """Length of current run of true observations."""
    event = _events(event)
    index = np.arange(len(event)).reshape((-1,) + (1,) * (event.ndim - 1))
    last = np.maximum.accumulate(np.where(event, -1, index), axis=0)
    return index - last


def bars_since_high(x, window, **kwargs):
    """Age of the first trailing maximum, in scheduled bars."""
    n = bars(window, kwargs.get("interval_ms"))
    age = (np.minimum(np.arange(len(x)) + 1, n) - 1).reshape(
        (-1,) + (1,) * (np.ndim(x) - 1)
    )
    return age - rolling_argmax(x, window, **kwargs)


def bars_since_low(x, window, **kwargs):
    """Age of the first trailing minimum, in scheduled bars."""
    return bars_since_high(-time_array(x), window, **kwargs)


def rolling_drawdown(x, window, **kwargs):
    """Current value relative to its trailing maximum, minus one."""
    return divide(x, rolling_max(x, window, **kwargs)) - 1


def rolling_drawup(x, window, **kwargs):
    """Current value relative to its trailing minimum, minus one."""
    return divide(x, rolling_min(x, window, **kwargs)) - 1


def crossing_above(x, y):
    """True where current x>y and the prior pair satisfied x<=y."""
    from ._core import array

    delta = time_array(x) - array(y) if np.ndim(y) == 0 else np.subtract(*paired(x, y))
    return (delta > 0) & (lag(delta) <= 0)


def crossing_below(x, y):
    """True where current x<y and the prior pair satisfied x>=y."""
    from ._core import array

    return crossing_above(-time_array(x), -array(y))


def breakout(x, window, *, direction="above", **kwargs):
    """Break of the preceding window's maximum or minimum, excluding the current bar."""
    if direction == "above":
        return time_array(x) > lag(rolling_max(x, window, **kwargs))
    if direction == "below":
        return time_array(x) < lag(rolling_min(x, window, **kwargs))
    raise ValueError("direction must be above or below")


__all__ = """lag diff pct_change returns log_returns cumulative_sum cumulative_product expanding forward_fill
rolling_sum rolling_mean rolling_median rolling_min rolling_max rolling_argmin rolling_argmax rolling_variance
rolling_std rolling_covariance rolling_correlation rolling_quantile rolling_rank rolling_percentile rolling_skewness
rolling_kurtosis rolling_zscore rolling_mad rolling_count rolling_regression rolling_beta rolling_residual
sma ema ewma wma wilder ewm_variance ewm_covariance linear_decay exponential_decay time_since_event
consecutive_true_count bars_since_high bars_since_low rolling_drawdown rolling_drawup crossing_above crossing_below
breakout""".split()
