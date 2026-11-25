"""Portfolio exposures and research risk estimates on supplied observations."""

import numpy as np

from . import math as qm, portfolio, ts
from ._core import array, divide
from .portfolio import _covariance
from .volatility import drawdown as underwater

rolling_beta, rolling_correlation = ts.rolling_beta, ts.rolling_correlation
risk_budget = portfolio.risk_parity


def per_symbol_exposure(quantity, mark):
    """Signed linear-futures exposure in USDT using aligned quantity and mark arrays."""
    q, m = array(quantity), array(mark)
    if q.shape != m.shape:
        raise ValueError("Quantity and marks must align")
    return np.where(q == 0, 0, q * m)


def gross_exposure(notional):
    """Sum of absolute symbol exposures; missing positions propagate."""
    return np.sum(np.abs(array(notional)), axis=-1)


def net_exposure(notional):
    """Sum of signed symbol exposures; missing positions propagate."""
    return np.sum(array(notional), axis=-1)


def leverage(notional, equity):
    """Gross marked exposure divided by positive marked equity."""
    e = array(equity)
    return divide(gross_exposure(notional), np.where(e > 0, e, np.nan))


def concentration(notional):
    """Herfindahl index of absolute exposure shares per timestamp."""
    x = array(notional)
    values = divide(np.abs(x), np.expand_dims(gross_exposure(x), axis=-1))
    return np.sum(values**2, axis=-1)


def historical_var(returns, *, confidence=0.95):
    """Positive-loss convention historical VaR from the linearly interpolated lower return quantile."""
    if not 0 < confidence < 1:
        raise ValueError("Confidence must be in (0,1)")
    return -qm.quantile(returns, 1 - confidence, axis=0)


def expected_shortfall(returns, *, confidence=0.95):
    """Mean realized loss among observations at or below the historical VaR threshold."""
    x = array(returns)
    return -qm.mean(
        np.where(x <= -historical_var(x, confidence=confidence), x, np.nan), axis=0
    )


def portfolio_volatility(weights, covariance):
    """Square root of w'Cw for each exposure-weight vector."""
    w, c = array(weights), _covariance(covariance)
    if w.shape[-1] != len(c):
        raise ValueError("Covariance and weight order must match")
    return np.sqrt(np.maximum(0, np.einsum("...i,ij,...j->...", w, c, w)))


def marginal_contribution(weights, covariance):
    """Derivative of portfolio volatility with respect to each exposure weight."""
    w, c = array(weights), _covariance(covariance)
    return divide(w @ c, np.expand_dims(portfolio_volatility(w, c), axis=-1))


def component_contribution(weights, covariance):
    """Exposure weight times its marginal risk contribution."""
    return array(weights) * marginal_contribution(weights, covariance)


def drawdown_duration(equity):
    """Consecutive underwater observations after each causal peak."""
    return ts.consecutive_true_count(underwater(equity) < 0)


def scenario_pnl(notional, shocks):
    """Linear mark-to-market PnL under supplied fractional price shocks."""
    n, s = array(notional), array(shocks)
    if n.ndim != 1 or s.shape[-1] != len(n):
        raise ValueError("Scenarios and portfolio must share the symbol axis")
    return s @ n


stress_test = scenario_pnl


def correlated_exposure(weights, covariance):
    """Portfolio volatility and asset component and fractional contributions."""
    risk = portfolio_volatility(weights, covariance)
    part = component_contribution(weights, covariance)
    return {
        "volatility": risk,
        "component": part,
        "fraction": divide(part, np.expand_dims(risk, axis=-1)),
    }


__all__ = """per_symbol_exposure gross_exposure net_exposure leverage concentration historical_var expected_shortfall
portfolio_volatility marginal_contribution component_contribution rolling_beta rolling_correlation underwater
drawdown_duration scenario_pnl stress_test correlated_exposure risk_budget""".split()
