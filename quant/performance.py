"""Reusable portfolio metrics using marked equity and realized reductions separately."""

import numpy as np

from . import math as qm, risk, stats, ts
from ._core import array, divide

return_distribution = stats.distribution_summary


def _equity(equity):
    e = array(equity)
    if e.ndim != 1 or len(e) < 2 or not np.isfinite(e).all() or e[0] <= 0:
        raise ValueError(
            "Supply a finite marked equity series including a positive initial equity"
        )
    return e


def total_return(equity):
    """Terminal marked equity divided by initial equity minus one."""
    e = _equity(equity)
    return e[-1] / e[0] - 1


def annualized_return(equity, *, periods_per_year=365):
    """Geometric return from equally spaced marked equity observations."""
    e = _equity(equity)
    if periods_per_year <= 0:
        raise ValueError("Periods per year must be positive")
    return (
        (e[-1] / e[0]) ** (periods_per_year / (len(e) - 1)) - 1 if e[-1] > 0 else np.nan
    )


def annualized_volatility(returns, *, periods_per_year=365):
    """Sample return standard deviation times square root of 24/7 periods per year."""
    if periods_per_year <= 0:
        raise ValueError("Periods per year must be positive")
    return qm.std(returns) * np.sqrt(periods_per_year)


def sharpe(returns, *, periods_per_year=365, risk_free_per_period=0.0):
    """Mean excess arithmetic return divided by sample deviation, annualized."""
    x = array(returns) - risk_free_per_period
    return divide(
        qm.mean(x) * periods_per_year,
        annualized_volatility(x, periods_per_year=periods_per_year),
    )


def sortino(returns, *, periods_per_year=365, target_per_period=0.0):
    """Annualized mean excess return divided by downside RMS, including zeros on up periods."""
    if periods_per_year <= 0:
        raise ValueError("Periods per year must be positive")
    x = array(returns) - target_per_period
    return np.sqrt(periods_per_year) * divide(
        qm.mean(x), qm.sqrt(qm.mean(np.minimum(x, 0) ** 2))
    )


def maximum_drawdown(equity):
    """Largest percentage drop from a marked equity peak, expressed as positive magnitude."""
    return float(-np.min(risk.underwater(_equity(equity))))


def calmar(equity, *, periods_per_year=365):
    """Annualized return divided by maximum drawdown."""
    return divide(
        annualized_return(equity, periods_per_year=periods_per_year),
        maximum_drawdown(equity),
    )


def trade_statistics(pnl, *, top=5):
    """Realized-reduction statistics with zero PnL included in the win-rate denominator."""
    x = array(pnl)
    if x.ndim != 1 or top < 1:
        raise ValueError("Supply a PnL vector and positive top-winner count")
    x = x[np.isfinite(x)]
    win, loss = x[x > 0], x[x < 0]
    winners = np.sort(win)[-top:].sum()
    return {
        "observations": len(x),
        "profit_factor": divide(win.sum(), -loss.sum()),
        "win_rate": divide(len(win), len(x)),
        "expected_payoff": x.mean() if len(x) else np.nan,
        "average_win": win.mean() if len(win) else np.nan,
        "average_loss": loss.mean() if len(loss) else np.nan,
        "pnl_skewness": qm.skewness(x) if len(x) >= 3 and np.ptp(x) > 0 else np.nan,
        "winner_concentration": divide(winners, win.sum()),
        "pnl_without_top_winners": x.sum() - winners,
    }


def tail_ratio(returns, *, quantile=0.95):
    """Absolute upper/lower tail quantile ratio."""
    if not 0.5 < quantile < 1:
        raise ValueError("Upper quantile must exceed the median")
    return np.abs(
        divide(qm.quantile(returns, quantile), qm.quantile(returns, 1 - quantile))
    )


def turnover(traded_notional, observable_equity):
    """Gross traded USDT divided by positive observable equity."""
    return divide(
        np.abs(array(traded_notional)),
        np.where(array(observable_equity) > 0, observable_equity, np.nan),
    )


def cost_attribution(*, net_pnl, fees, spread_slippage_impact, funding):
    """Attribute native cash costs and compute gross PnL before execution costs and funding."""
    return {
        "net_pnl": net_pnl,
        "fees": fees,
        "spread_slippage_impact": spread_slippage_impact,
        "funding": funding,
        "gross_pnl": net_pnl + fees + spread_slippage_impact - funding,
    }


def exposure_adjusted_return(returns, gross_leverage):
    """Per-period return per unit of positive gross leverage."""
    return divide(returns, np.where(array(gross_leverage) > 0, gross_leverage, np.nan))


def equity_metrics(equity, *, periods_per_year=365):
    """Summarize marked equity using supplied fixed return sampling conventions."""
    ret = ts.pct_change(_equity(equity))[1:]
    return {
        "total_return": total_return(equity),
        "annualized_return": annualized_return(
            equity, periods_per_year=periods_per_year
        ),
        "annualized_volatility": annualized_volatility(
            ret, periods_per_year=periods_per_year
        ),
        "sharpe": sharpe(ret, periods_per_year=periods_per_year),
        "sortino": sortino(ret, periods_per_year=periods_per_year),
        "maximum_drawdown": maximum_drawdown(equity),
        "calmar": calmar(equity, periods_per_year=periods_per_year),
        "tail_ratio": tail_ratio(ret),
        "periods_per_year": periods_per_year,
    }


__all__ = """total_return annualized_return annualized_volatility sharpe sortino maximum_drawdown calmar
trade_statistics tail_ratio turnover cost_attribution exposure_adjusted_return equity_metrics return_distribution""".split()
