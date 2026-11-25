"""Conventional trailing indicators on regular price and flow arrays."""

import numpy as np

from . import ts
from ._core import bars, divide, paired, time_array
from .volatility import realized_volatility as realized_volatility

sma, ema, wma = ts.sma, ts.ema, ts.wma


def dema(x, span, **kwargs):
    """Double EMA, 2*EMA(x) minus EMA(EMA(x))."""
    first = ema(x, span, **kwargs)
    return 2 * first - ema(first, span, **kwargs)


def tema(x, span, **kwargs):
    """Triple EMA, 3*EMA1 - 3*EMA2 + EMA3."""
    first = ema(x, span, **kwargs)
    second = ema(first, span, **kwargs)
    return 3 * (first - second) + ema(second, span, **kwargs)


def hma(x, window, *, interval_ms=None):
    """Hull MA using floor(window/2) and floor(sqrt(window)) for WMA lengths."""
    n = bars(window, interval_ms)
    return wma(2 * wma(x, max(1, n // 2)) - wma(x, n), int(np.sqrt(n)))


def macd(x, *, fast=12, slow=26, signal=9, **kwargs):
    """Return MACD fast-minus-slow EMA, its signal EMA and the histogram."""
    if bars(fast, kwargs.get("interval_ms")) >= bars(slow, kwargs.get("interval_ms")):
        raise ValueError("MACD fast span must be smaller than slow")
    line = ema(x, fast, **kwargs) - ema(x, slow, **kwargs)
    average = ema(line, signal, **kwargs)
    return line, average, line - average


def true_range(high, low, close):
    """Max of high-low and distances to prior close; first observation uses high-low."""
    high, low = paired(high, low)
    high, close = paired(high, close)
    previous = ts.lag(close)
    return np.fmax(
        high - low, np.maximum(np.abs(high - previous), np.abs(low - previous))
    )


def atr(high, low, close, window=14, *, interval_ms=None):
    """Wilder-smoothed true range with a full-window arithmetic seed."""
    return ts.wilder(true_range(high, low, close), window, interval_ms=interval_ms)


def natr(high, low, close, window=14, **kwargs):
    """Normalized ATR as 100*ATR/close."""
    return 100 * divide(atr(high, low, close, window, **kwargs), close)


def dmi(high, low, close, window=14, *, interval_ms=None):
    """Wilder-smoothed +DI and -DI in percentage units, and raw DX."""
    high, low = paired(high, low)
    up, down = ts.diff(high), -ts.diff(low)
    ok = np.isfinite(up) & np.isfinite(down)
    total = atr(high, low, close, window, interval_ms=interval_ms)
    plus = 100 * divide(
        ts.wilder(
            np.where(ok, np.where((up > down) & (up > 0), up, 0), np.nan),
            window,
            interval_ms=interval_ms,
        ),
        total,
    )
    minus = 100 * divide(
        ts.wilder(
            np.where(ok, np.where((down > up) & (down > 0), down, 0), np.nan),
            window,
            interval_ms=interval_ms,
        ),
        total,
    )
    return plus, minus, 100 * divide(np.abs(plus - minus), plus + minus)


def adx(high, low, close, window=14, *, interval_ms=None):
    """Wilder-smoothed directional movement index DX."""
    return ts.wilder(
        dmi(high, low, close, window, interval_ms=interval_ms)[-1],
        window,
        interval_ms=interval_ms,
    )


def aroon(high, low, window=25, *, interval_ms=None):
    """Aroon up and down over window+1 observations; most recent tied extreme wins."""
    high, low = paired(high, low)
    n = bars(window, interval_ms)
    age = ts._scan_window(
        lambda a, m: np.argmax(a[::-1]), "Age of most recent maximum."
    )
    return 100 * (1 - age(high, n + 1) / n), 100 * (1 - age(-low, n + 1) / n)


def donchian(high, low, window=20, **kwargs):
    """Return trailing upper, midpoint and lower Donchian bands including the current bar."""
    high, low = paired(high, low)
    high, low = (
        ts.rolling_max(high, window, **kwargs),
        ts.rolling_min(low, window, **kwargs),
    )
    return high, (high + low) / 2, low


def _stateful_ohlc(high, low, close, outputs, routine):
    high, low = paired(high, low)
    high, close = paired(high, close)
    return (
        np.column_stack(
            [
                routine(a, b, c, d)
                for a, b, c, d in zip(high.T, low.T, close.T, outputs.T)
            ]
        )
        if high.ndim == 2
        else routine(high, low, close, outputs)
    )


def supertrend(high, low, close, window=10, *, multiplier=3.0, interval_ms=None):
    """Trailing ATR bands following price; initial trend is down and gaps restart it."""
    if multiplier <= 0:
        raise ValueError("multiplier must be positive")
    band = multiplier * atr(high, low, close, window, interval_ms=interval_ms)

    def run(h, l, c, r):
        out = np.full_like(c, np.nan)
        for lo, hi in ts._runs(np.isfinite(h + l + c + r)):
            upper, lower = (h[lo] + l[lo]) / 2 + r[lo], (h[lo] + l[lo]) / 2 - r[lo]
            up = False
            out[lo] = upper
            for i in range(lo + 1, hi):
                u, d = (h[i] + l[i]) / 2 + r[i], (h[i] + l[i]) / 2 - r[i]
                upper = u if u < upper or c[i - 1] > upper else upper
                lower = d if d > lower or c[i - 1] < lower else lower
                up = c[i] >= lower if up else c[i] > upper
                out[i] = lower if up else upper
        return out

    return _stateful_ohlc(high, low, close, band, run)


def parabolic_sar(high, low, *, step=0.02, maximum=0.2):
    """Wilder-style stop-and-reverse seeded from the first two midpoint prices."""
    if not 0 < step <= maximum <= 1:
        raise ValueError("Require 0 < step <= maximum <= 1")
    high, low = paired(high, low)
    mid = (high + low) / 2

    def run(h, l, c, _):
        out = np.full_like(c, np.nan)
        for lo, hi in ts._runs(np.isfinite(h + l)):
            if hi - lo < 2:
                continue
            up = c[lo + 1] >= c[lo]
            sar, extreme = (l[lo], h[lo + 1]) if up else (h[lo], l[lo + 1])
            out[lo + 1], factor = sar, step
            for i in range(lo + 2, hi):
                sar += factor * (extreme - sar)
                sar = (
                    min(sar, l[i - 1], l[i - 2]) if up else max(sar, h[i - 1], h[i - 2])
                )
                if (up and l[i] < sar) or (not up and h[i] > sar):
                    sar, extreme, factor = extreme, l[i] if up else h[i], step
                    up = not up
                elif (up and h[i] > extreme) or (not up and l[i] < extreme):
                    extreme = h[i] if up else l[i]
                    factor = min(factor + step, maximum)
                out[i] = sar
        return out

    return _stateful_ohlc(high, low, mid, mid, run)


def rsi(close, window=14, *, interval_ms=None):
    """Wilder RSI; flat fully warmed series returns 50, zero losses alone return 100."""
    change = ts.diff(close)
    up = ts.wilder(np.maximum(change, 0), window, interval_ms=interval_ms)
    down = ts.wilder(np.maximum(-change, 0), window, interval_ms=interval_ms)
    return np.where((up == 0) & (down == 0), 50, 100 * divide(up, up + down))


def stochastic(high, low, close, window=14, *, smooth=3, **kwargs):
    """Raw stochastic %K and its simple moving average %D."""
    highest, _, lowest = donchian(high, low, window, **kwargs)
    close, highest = paired(close, highest)
    k = 100 * divide(close - lowest, highest - lowest)
    return k, sma(k, smooth, interval_ms=kwargs.get("interval_ms"))


def stochastic_rsi(close, window=14, *, smooth=3, interval_ms=None):
    """Stochastic applied to RSI with full trailing oscillator warm-up."""
    oscillator = rsi(close, window, interval_ms=interval_ms)
    return stochastic(
        oscillator,
        oscillator,
        oscillator,
        window,
        smooth=smooth,
        interval_ms=interval_ms,
    )


def roc(x, window=12, **kwargs):
    """Trailing percentage rate of change, in percent."""
    return 100 * ts.pct_change(x, window, **kwargs)


def _typical(high, low, close):
    high, low = paired(high, low)
    high, close = paired(high, close)
    return (high + low + close) / 3


def cci(high, low, close, window=20, **kwargs):
    """Commodity channel index with mean absolute deviation and scale 0.015."""
    price = _typical(high, low, close)
    deviation = ts._scan_window(
        lambda a, m: np.mean(np.abs(a[m] - np.mean(a[m]))),
        "Mean absolute deviation around mean.",
    )
    return divide(
        price - sma(price, window, **kwargs), 0.015 * deviation(price, window, **kwargs)
    )


def williams_r(high, low, close, window=14, **kwargs):
    """Williams %R from trailing high and low, scaled from -100 to 0."""
    return stochastic(high, low, close, window, smooth=1, **kwargs)[0] - 100


def ultimate_oscillator(high, low, close, *, windows=(7, 14, 28), interval_ms=None):
    """4:2:1 weighted sum of buying-pressure/true-range ratios."""
    high, low = paired(high, low)
    high, close = paired(high, close)
    previous = ts.lag(close)
    bottom = np.minimum(low, previous)
    pressure = close - bottom
    spread = np.maximum(high, previous) - bottom
    ratios = [
        divide(
            ts.rolling_sum(pressure, n, interval_ms=interval_ms),
            ts.rolling_sum(spread, n, interval_ms=interval_ms),
        )
        for n in windows
    ]
    if len(ratios) != 3:
        raise ValueError("Ultimate oscillator requires three windows")
    return (4 * ratios[0] + 2 * ratios[1] + ratios[2]) * 100 / 7


def trix(close, span=15, **kwargs):
    """One-bar percentage change of the third cascaded EMA."""
    result = time_array(close)
    for _ in range(3):
        result = ema(result, span, **kwargs)
    return roc(result, 1)


def bollinger(close, window=20, *, multiplier=2.0, **kwargs):
    """Trailing sample-standard-deviation bands, returned as upper, mean, lower."""
    average = sma(close, window, **kwargs)
    band = multiplier * ts.rolling_std(close, window, **kwargs)
    return average + band, average, average - band


def keltner(high, low, close, window=20, *, multiplier=2.0, interval_ms=None):
    """EMA of close plus or minus multiplier times Wilder ATR."""
    average = ema(close, window, interval_ms=interval_ms)
    band = multiplier * atr(high, low, close, window, interval_ms=interval_ms)
    return average + band, average, average - band


def obv(close, volume):
    """On-balance volume; first signed flow is zero and unknown gaps reset."""
    close, volume = paired(close, volume)
    delta = ts.diff(close)
    flow = np.sign(delta) * volume
    flow[:1] = np.where(np.isfinite(close[:1] + volume[:1]), 0, np.nan)
    return ts.cumulative_sum(flow)


def mfi(high, low, close, volume, window=14, **kwargs):
    """Money Flow Index, positive flow divided by positive plus negative flow, times 100."""
    price, volume = paired(_typical(high, low, close), volume)
    flow = price * volume
    delta = ts.diff(price)
    up = ts.rolling_sum(
        np.where(delta > 0, flow, np.where(delta <= 0, 0, np.nan)), window, **kwargs
    )
    down = ts.rolling_sum(
        np.where(delta < 0, flow, np.where(delta >= 0, 0, np.nan)), window, **kwargs
    )
    return 100 * divide(up, up + down)


def _ad_flow(high, low, close, volume):
    high, low = paired(high, low)
    close, volume = paired(close, volume)
    high, close = paired(high, close)
    width = high - low
    return (
        np.where(
            (width == 0) & np.isfinite(close), 0, divide(2 * close - high - low, width)
        )
        * volume
    )


def accumulation_distribution(high, low, close, volume):
    """Cumulative money flow volume with zero multiplier on flat finite bars."""
    return ts.cumulative_sum(_ad_flow(high, low, close, volume))


def chaikin_money_flow(high, low, close, volume, window=20, **kwargs):
    """Trailing money flow volume divided by trailing volume."""
    return divide(
        ts.rolling_sum(_ad_flow(high, low, close, volume), window, **kwargs),
        ts.rolling_sum(volume, window, **kwargs),
    )


def rolling_vwap(quote_volume, volume, window, **kwargs):
    """Rolling VWAP from actual quote turnover divided by base volume."""
    quote, volume = paired(quote_volume, volume)
    valid = np.isfinite(quote) & np.isfinite(volume)
    return divide(
        ts.rolling_sum(np.where(valid, quote, np.nan), window, **kwargs),
        ts.rolling_sum(np.where(valid, volume, np.nan), window, **kwargs),
    )


def vwma(close, volume, window, **kwargs):
    """Volume-weighted mean close; exact VWAP requires quote turnover."""
    close, volume = paired(close, volume)
    return rolling_vwap(close * volume, volume, window, **kwargs)


__all__ = """sma ema dema tema wma hma macd adx dmi aroon supertrend donchian parabolic_sar rsi stochastic
stochastic_rsi roc cci williams_r ultimate_oscillator trix true_range atr natr bollinger keltner
realized_volatility obv mfi accumulation_distribution chaikin_money_flow vwma rolling_vwap""".split()
