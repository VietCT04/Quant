"""Exposure weights and signed USDT targets. The native engine owns accounting."""

import numpy as np
from scipy.optimize import minimize

from . import cs
from ._core import array, divide, paired

gross_normalize = cs.gross_normalize
long_short_budget = cs.long_short_normalize
dollar_neutral = cs.demean
beta_neutral = cs.beta_neutralize
signal_weights = cs.gross_normalize


def equal_weight(active, *, gross=1.0):
    """Equal long exposure weight over currently active symbols."""
    mask = np.asarray(active)
    if mask.dtype.kind != "b":
        raise ValueError("Active universe must be boolean")
    return gross_normalize(np.where(mask, 1.0, np.nan), gross=gross)


def rank_weight(score, *, gross=1.0, active=None):
    """Gross-normalize centered rank percentile into signed exposure weights."""
    return gross_normalize(cs.percentile(score, active=active) - 0.5, gross=gross)


def inverse_volatility(volatility, *, direction=None, gross=1.0):
    """Inverse-positive-volatility exposure weights with optional signed directions."""
    x = array(volatility)
    d = np.ones_like(x) if direction is None else array(direction)
    if d.shape != x.shape:
        raise ValueError("Directions and volatility must align")
    return gross_normalize(divide(d, np.where(x > 0, x, np.nan)), gross=gross)


def volatility_target(weights, current_volatility, *, target, max_multiplier=3.0):
    """Scale signed exposure weights by target/current risk subject to a leverage multiplier cap."""
    w, vol = array(weights), array(current_volatility)
    if w.ndim != 2 or vol.shape != (len(w),) or target < 0 or max_multiplier <= 0:
        raise ValueError(
            "Expected [time,symbol] weights and one current volatility per row"
        )
    return (
        w
        * np.minimum(max_multiplier, divide(target, np.where(vol > 0, vol, np.nan)))[
            :, None
        ]
    )


def cap_exposures(weights, *, per_symbol, gross=None):
    """Clip signed exposure weights with optional portfolio gross reduction and no redistribution."""
    if per_symbol < 0 or (gross is not None and gross < 0):
        raise ValueError("Exposure caps must be nonnegative")
    x = np.clip(cs._panel(weights), -per_symbol, per_symbol)
    if gross is None:
        return x
    total = np.nansum(np.abs(x), axis=1, keepdims=True)
    return x * np.where(total > gross, divide(gross, total), 1.0)


concentration_limit = cap_exposures


def turnover_rebalance(target, previous, *, max_turnover):
    """Scale requested changes to a maximum one-way gross weight turnover per row."""
    target, previous = paired(target, previous)
    if target.ndim != 2 or max_turnover < 0:
        raise ValueError("Expected aligned panels and a nonnegative turnover allowance")
    difference = target - previous
    total = np.nansum(np.abs(difference), axis=1, keepdims=True)
    return previous + difference * np.where(
        total > max_turnover, divide(max_turnover, total), 1.0
    )


def signed_notionals(
    weights, observable_equity, *, equity_available_ms=None, decision_ms=None
):
    """Convert exposure weights to signed USDT target notionals using contemporaneously observable equity."""
    w, e = array(weights), array(observable_equity)
    if (
        w.ndim != 2
        or e.ndim not in (0, 1)
        or (e.ndim == 1 and len(e) != len(w))
        or np.any(e < 0)
    ):
        raise ValueError(
            "Provide a nonnegative cash budget or aligned observable equity"
        )
    if e.ndim == 1:
        a, b = np.asarray(equity_available_ms), np.asarray(decision_ms)
        if a.shape != e.shape or b.shape != e.shape or np.any(a > b):
            raise ValueError(
                "Every equity observation must be available by its target decision"
            )
        e = e[:, None]
    return w * e


position_size = signed_notionals


def covariance_shrinkage(covariance, *, intensity=0.2):
    """Shrink a supplied covariance matrix toward its diagonal using a fixed intensity."""
    a = _covariance(covariance)
    if not 0 <= intensity <= 1:
        raise ValueError("Shrinkage intensity must be in [0,1]")
    return (1 - intensity) * a + intensity * np.diag(np.diag(a))


def _covariance(covariance):
    a = array(covariance)
    if (
        a.ndim != 2
        or not len(a)
        or a.shape[0] != a.shape[1]
        or not np.isfinite(a).all()
        or not np.allclose(a, a.T)
        or np.linalg.eigvalsh(a)[0] < -max(1e-14, 1e-10 * np.abs(a).max())
    ):
        raise ValueError("Expected a finite symmetric positive-semidefinite covariance")
    return a


def constrained_weights(
    covariance,
    *,
    expected_returns=None,
    aversion=1.0,
    caps=1.0,
    floor=0.0,
    exposures=None,
    exposure_targets=None,
):
    """Single-date fully invested mean-variance exposure weights with box and linear equality constraints."""
    a = _covariance(covariance)
    n = len(a)
    c = np.zeros(n) if expected_returns is None else array(expected_returns)
    lower, upper = np.broadcast_to(floor, (n,)), np.broadcast_to(caps, (n,))
    if (
        c.shape != (n,)
        or not np.isfinite(c).all()
        or aversion <= 0
        or not np.isfinite(np.r_[lower, upper]).all()
        or np.any(upper < lower)
        or lower.sum() > 1
        or upper.sum() < 1
    ):
        raise ValueError("Invalid expected returns, aversion or budget bounds")
    matrix, rhs = np.ones((1, n)), np.array([1.0])
    if exposures is not None:
        f, b = array(exposures), array(exposure_targets)
        if (
            f.ndim != 2
            or f.shape[1] != n
            or b.shape != (len(f),)
            or not np.isfinite(f).all()
            or not np.isfinite(b).all()
        ):
            raise ValueError("Linear exposure constraints must align")
        matrix, rhs = np.vstack([matrix, f]), np.r_[rhs, b]
    scale = max(1e-15, np.abs(aversion * a).max(), np.abs(c).max())
    result = minimize(
        lambda w: (0.5 * aversion * (w @ a @ w) - c @ w) / scale,
        np.clip(np.full(n, 1 / n), lower, upper),
        jac=lambda w: (aversion * (a @ w) - c) / scale,
        bounds=list(zip(lower, upper)),
        constraints={
            "type": "eq",
            "fun": lambda w: matrix @ w - rhs,
            "jac": lambda w: matrix,
        },
        method="SLSQP",
        options={"ftol": 1e-11, "maxiter": 500},
    )
    if not result.success or np.max(np.abs(matrix @ result.x - rhs)) > 1e-7:
        raise ValueError(f"Portfolio optimization failed: {result.message}")
    return result.x


def minimum_variance(covariance, **kwargs):
    """Fully invested minimum-variance exposure weights using the constrained solver."""
    return constrained_weights(covariance, **kwargs)


correlation_aware = minimum_variance


def risk_parity(covariance, *, budgets=None):
    """Long-only exposure weights targeting positive fractional volatility risk contributions."""
    a = _covariance(covariance)
    n = len(a)
    b = np.full(n, 1 / n) if budgets is None else array(budgets)
    if (
        b.shape != (n,)
        or not np.isfinite(b).all()
        or np.any(b <= 0)
        or np.linalg.eigvalsh(a)[0] <= 0
    ):
        raise ValueError(
            "Risk parity requires positive budgets and positive-definite covariance"
        )
    b = b / b.sum()
    a = a / np.diag(a).max()
    result = minimize(
        lambda w: 0.5 * w @ a @ w - b @ np.log(w),
        np.ones(n),
        jac=lambda w: a @ w - b / w,
        method="L-BFGS-B",
        bounds=[(1e-12, None)] * n,
        options={"gtol": 1e-10, "ftol": 1e-14},
    )
    contribution = result.x * (a @ result.x)
    if not result.success or not np.allclose(
        contribution / contribution.sum(), b, rtol=1e-4, atol=1e-6
    ):
        raise ValueError(f"Risk parity solver failed: {result.message}")
    return result.x / result.x.sum()


__all__ = """gross_normalize long_short_budget dollar_neutral beta_neutral signal_weights equal_weight rank_weight
inverse_volatility volatility_target cap_exposures concentration_limit turnover_rebalance signed_notionals
position_size covariance_shrinkage constrained_weights minimum_variance correlation_aware risk_parity""".split()
