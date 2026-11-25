"""Direct aggregate-trade measurements and explicitly labeled microstructure proxies."""

import numpy as np

from . import ts
from ._core import MissingCapabilityError, divide, paired, time_array
from .indicators import rolling_vwap as rolling_vwap


def _imbalance(buy, sell):
    buy, sell = paired(buy, sell)
    return divide(buy - sell, buy + sell)


def volume_imbalance(buy_volume, sell_volume):
    """Observed aggressive base-volume imbalance (buy-sell)/(buy+sell)."""
    return _imbalance(buy_volume, sell_volume)


def quote_volume_imbalance(buy_quote_volume, sell_quote_volume):
    """Observed aggressive USDT-value imbalance."""
    return _imbalance(buy_quote_volume, sell_quote_volume)


def trade_count_imbalance(buy_trade_count, sell_trade_count):
    """Observed constituent-count imbalance using available archive counts."""
    return _imbalance(buy_trade_count, sell_trade_count)


def cumulative_volume_delta(buy_volume, sell_volume):
    """Cumulative observed signed aggressive volume, resetting across unknown intervals."""
    return ts.cumulative_sum(np.subtract(*paired(buy_volume, sell_volume)))


def vwap_deviation(close, vwap):
    """Close divided by aligned VWAP minus one."""
    return divide(*paired(close, vwap)) - 1


def trade_intensity(trade_count, *, interval_ms):
    """Constituent trades per second on scheduled bars."""
    if interval_ms <= 0:
        raise ValueError("Sampling interval must be positive")
    return time_array(trade_count) / (interval_ms / 1000)


def average_aggregate_trade_size(volume, agg_trade_count):
    """Base volume per observed aggregate trade."""
    return divide(*paired(volume, agg_trade_count))


def average_constituent_trade_size(volume, trade_count):
    """Implied mean constituent fill size from total base volume and reported constituent count."""
    return divide(*paired(volume, trade_count))


def large_aggregate_trade_proxy(max_trade_qty, volume):
    """Share of base volume in the largest observed aggregate, not the largest underlying fill."""
    return divide(*paired(max_trade_qty, volume))


def volume_acceleration(volume, periods=1, **kwargs):
    """Second difference of volume over trailing periods."""
    return ts.diff(ts.diff(volume, periods, **kwargs), periods, **kwargs)


def volume_zscore(volume, window, **kwargs):
    """Trailing sample z-score of observed volume."""
    return ts.rolling_zscore(volume, window, **kwargs)


def relative_volume(volume, window, **kwargs):
    """Volume divided by mean volume over the preceding window, excluding current volume."""
    return divide(volume, ts.lag(ts.rolling_mean(volume, window, **kwargs)))


def return_per_volume(returns, quote_volume):
    """Signed price return divided by observed USDT turnover."""
    return divide(*paired(returns, quote_volume))


def amihud(returns, quote_volume, window, **kwargs):
    """Trailing mean absolute return per USDT turnover, an illiquidity proxy."""
    return ts.rolling_mean(
        np.abs(return_per_volume(returns, quote_volume)), window, **kwargs
    )


def price_impact_proxy(returns, signed_quote_volume, window, **kwargs):
    """Trailing OLS slope of return on signed taker USDT flow; descriptive association."""
    return ts.rolling_beta(returns, signed_quote_volume, window, **kwargs)


def price_volume_divergence(close, volume, window, **kwargs):
    """Price momentum z-score minus observed volume z-score."""
    close, volume = paired(close, volume)
    return volume_zscore(ts.log_returns(close), window, **kwargs) - volume_zscore(
        volume, window, **kwargs
    )


def mark_to_trade_deviation(mark, close):
    """Aligned observable mark divided by trade close minus one."""
    return divide(*paired(mark, close)) - 1


def funding_changes(rate):
    """Change in funding rate per observed settlement; pass event-time samples."""
    return ts.diff(rate)


def require_observation(field):
    """Raise a structured capability error for unsupported bar-level venue observations."""
    from .data import UNITS

    if field not in {*UNITS, "mark", "funding_rate"}:
        raise MissingCapabilityError(field, ("dedicated historical source",))
    return field


__all__ = """volume_imbalance quote_volume_imbalance trade_count_imbalance cumulative_volume_delta rolling_vwap
vwap_deviation trade_intensity average_aggregate_trade_size average_constituent_trade_size
large_aggregate_trade_proxy volume_acceleration volume_zscore relative_volume amihud price_impact_proxy
return_per_volume price_volume_divergence mark_to_trade_deviation funding_changes require_observation""".split()
