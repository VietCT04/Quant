"""Hand checks, independent references, causal prefixes and SDK integration."""

from dataclasses import replace
from pathlib import Path
import json
import operator

import numpy as np
from numpy.testing import assert_allclose, assert_array_equal
import polars as pl
import pytest
from scipy import stats as scipy_stats

import quant as q
from marketdata.reader import as_millis
from simulator.strategies import Signals


def panel(value, *, start=0, status=None, symbols=None):
    value = np.asarray(value, dtype=float)
    if value.ndim == 1:
        value = value[:, None]
    count, width = value.shape
    symbols = tuple(f"S{i}" for i in range(width)) if symbols is None else symbols
    clock = np.arange(count, dtype="i8") * 5000 + start
    status = (
        np.zeros(value.shape, dtype="u1")
        if status is None
        else np.asarray(status, dtype="u1").reshape(value.shape)
    )
    return q.data.Block(
        clock,
        symbols,
        clock + 5100,
        status,
        {"close": value},
        5000,
        {"close": "USDT/base"},
    )


@pytest.mark.parametrize(
    "name,x,expected",
    [
        ("absolute", [-2, np.nan], [2, np.nan]),
        ("sign", [-2, 0, np.inf], [-1, 0, np.nan]),
        ("sqrt", [4, -1], [2, np.nan]),
        ("log", [1, 0], [0, np.nan]),
        ("exp", [0, 1000], [1, np.nan]),
        ("log1p", [1e-15, -1], [1e-15, np.nan]),
        ("expm1", [1e-15, 0], [1e-15, 0]),
        ("finite", [0, np.nan], [1, 0]),
        ("missing", [0, np.inf], [0, 1]),
    ],
)
def test_transforms(name, x, expected):
    assert_allclose(getattr(q.math, name)(x), expected, atol=1e-25)


def test_math_reductions():
    x = np.array([1.0, 2.0, 3.0, 4.0])
    assert_allclose(q.math.power(x, 2), [1, 4, 9, 16])
    assert_allclose(q.math.minimum(x, 2), [1, 2, 2, 2])
    assert_allclose(q.math.maximum(x, 2), [2, 2, 3, 4])
    assert_allclose(q.math.clip(x, 2, 3), [2, 2, 3, 3])
    assert_allclose(q.math.where(x > 2, x, 0), [0, 0, 3, 4])
    assert_allclose(
        q.math.safe_divide([1, 1e308, 2], [2, 1e-300, 0]), [0.5, np.nan, np.nan]
    )
    assert_allclose(q.math.stable_sum([[1, np.nan], [5, np.inf]]), [6, np.nan])
    assert q.math.weighted_mean(x, [1, 0, 2, np.nan]) == pytest.approx(7 / 3)
    assert q.math.geometric_mean([1, 4]) == pytest.approx(2)
    assert q.math.harmonic_mean([2, 4]) == pytest.approx(8 / 3)
    assert q.math.mean(x) == q.math.median(x) == 2.5
    assert q.math.quantile(x, 0.25) == 1.75
    assert q.math.variance(x) == pytest.approx(5 / 3)
    assert q.math.std(x) == pytest.approx(np.sqrt(5 / 3))
    assert q.math.covariance(x, x + 7) == pytest.approx(5 / 3)
    assert q.math.correlation(x, x[::-1]) == pytest.approx(-1)
    assert q.math.skewness(x) == pytest.approx(0)
    assert q.math.kurtosis(x) == pytest.approx(-1.2)
    assert q.math.robust_summary(x) == {"median": 2.5, "iqr": 1.5, "mad": 1.0}
    with pytest.raises(ValueError):
        q.math.weighted_mean(x, [-1, 0, 1, 1])


@pytest.mark.parametrize(
    "name,expected",
    [
        ("rolling_sum", 9),
        ("rolling_mean", 3),
        ("rolling_median", 3),
        ("rolling_min", 2),
        ("rolling_max", 4),
        ("rolling_argmin", 0),
        ("rolling_argmax", 2),
        ("rolling_variance", 1),
        ("rolling_std", 1),
        ("rolling_rank", 3),
        ("rolling_percentile", 5 / 6),
        ("rolling_skewness", 0),
        ("rolling_mad", 1),
        ("rolling_zscore", 1),
        ("rolling_count", 3),
        ("sma", 3),
        ("wma", 20 / 6),
        ("linear_decay", 20 / 6),
    ],
)
def test_rolling_hand(name, expected):
    observed = getattr(q.ts, name)([3.0, 2.0, 3.0, 4.0], "15s", interval_ms=5000)
    assert observed[-1] == pytest.approx(expected)
    if name != "rolling_count":
        assert np.isnan(observed[0])


def test_time_series_definitions():
    t = q.ts
    x = np.array([1.0, 2.0, 4.0, np.nan, 8.0, 16.0])
    assert_allclose(t.lag(x), [np.nan, 1, 2, 4, np.nan, 8])
    assert_allclose(t.diff(x), [np.nan, 1, 2, np.nan, np.nan, 8])
    assert_allclose(t.returns(x), [np.nan, 1, 1, np.nan, np.nan, 1])
    assert np.isnan(t.returns(x, 3)[4])
    assert_allclose(t.log_returns(x), t.returns(x) * np.log(2))
    assert_allclose(t.cumulative_sum(x), [1, 3, 7, np.nan, 8, 24])
    assert_allclose(t.cumulative_product(x), [1, 2, 8, np.nan, 8, 128])
    assert_allclose(t.expanding(x), [1, 1.5, 7 / 3, np.nan, 8, 12])
    assert_allclose(t.forward_fill([1, np.nan, np.nan, 2], limit=1), [1, 1, np.nan, 2])
    assert_allclose(t.rolling_quantile(x, 2, 0.5), [np.nan, 1.5, 3, np.nan, np.nan, 12])
    assert t.rolling_kurtosis([1, 2, 3, 4], 4)[-1] == pytest.approx(-1.2)
    x = np.arange(8.0)
    y = 2 * x + 1
    assert_allclose(t.rolling_covariance(x, y, 3)[2:], 2)
    assert_allclose(t.rolling_correlation(x, y, 3)[2:], 1)
    intercept, beta = t.rolling_regression(y, x, 3)
    assert_allclose(intercept[2:], 1, atol=1e-12)
    assert_allclose(beta[2:], t.rolling_beta(y, x, 3)[2:])
    assert_allclose(t.rolling_residual(y, x, 3)[2:], 0, atol=1e-12)
    assert_allclose(t.wilder([1.0, 4.0, 7.0, 10.0], 3), [np.nan, np.nan, 4, 6])
    assert t.ewm_variance([1.0, 3.0], 3)[-1] == pytest.approx(1)
    assert t.ewm_covariance([1.0, 3.0], [2.0, 6.0], 3)[-1] == pytest.approx(2)
    events = np.array([False, True, True, False])
    assert_allclose(t.time_since_event(events), [np.nan, 0, 0, 1])
    assert_array_equal(t.consecutive_true_count(events), [0, 1, 2, 0])
    assert t.bars_since_high([3, 1, 2], 3)[-1] == 2
    assert t.bars_since_low([3, 1, 2], 3)[-1] == 1
    assert t.rolling_drawdown([2, 4, 3], 3)[-1] == -0.25
    assert t.rolling_drawup([2, 4, 3], 3)[-1] == 0.5
    assert_array_equal(t.crossing_above([1, 3, 0], 2), [False, True, False])
    assert_array_equal(t.crossing_below([1, 3, 0], 2), [False, False, True])
    assert_array_equal(t.breakout([1, 2, 3, 2], 2), [False, False, True, False])
    with pytest.raises(ValueError):
        t.ema(x, "1m", interval_ms=7000)


@pytest.mark.parametrize("gap", ["reset", "skip", "carry"])
def test_ema_reference_causality_and_chunks(gap):
    rng = np.random.default_rng(19)
    a = rng.normal(size=(101, 3)) + 1e8
    a[rng.random(a.shape) < 0.13] = np.nan
    out = np.full_like(a, np.nan)
    for j in range(a.shape[1]):
        prev, count = np.nan, 0
        for i, value in enumerate(a[:, j]):
            if np.isfinite(value):
                prev = value if not np.isfinite(prev) else 0.2 * value + 0.8 * prev
                count += 1
                if count >= 3:
                    out[i, j] = prev
            elif gap == "reset":
                prev, count = np.nan, 0
            elif gap == "carry" and count >= 3:
                out[i, j] = prev
    state = q.ts.EMAState(9, gap=gap, min_periods=3)
    batch = q.ts.ema(a, 9, gap=gap, min_periods=3)
    assert_allclose(batch, out, rtol=1e-14, atol=1e-7)
    assert_allclose(
        np.concatenate([state.update(p) for p in np.split(a, [0, 1, 17, 18, 99])]),
        batch,
        rtol=0,
        atol=1e-7,
    )
    future = a.copy()
    future[60:] = -3e12
    assert_allclose(
        q.ts.ewma(future, 9, gap=gap, min_periods=3)[:60], batch[:60], rtol=0, atol=0
    )


@pytest.mark.parametrize("name", ["ema", "zscore"])
def test_native_reference_strategy_migration(name):
    rng = np.random.default_rng(7)
    data = 100 + np.cumsum(rng.normal(size=(1000, 2)), axis=0)
    flags = np.zeros(data.shape, dtype="u1")
    flags[40:70, 0] = 2
    flags[490:530, 1] = 3
    strategy = Signals(name, ["A", "B"], 100, 3, 0)
    outputs = []
    for lo, hi in [(0, 16), (16, 510), (510, 1000)]:
        values = np.zeros((hi - lo, 2, 5))
        values[:, :, 0] = data[lo:hi]
        values[:, :, 4] = np.nan
        strategy.apply(values, flags[lo:hi])
        outputs.append(values[:, :, 4])
    result = np.concatenate(outputs)
    for j in range(2):
        s = pl.Series(np.where(flags[:, j] < 2, data[:, j], np.nan)).fill_nan(None)
        if name == "ema":
            a, b = (
                s.ewm_mean(span=size, adjust=False, ignore_nulls=True)
                .forward_fill()
                .to_numpy()
                for size in (60, 240)
            )
            expected = np.sign(a - b) * 100
        else:
            z = (s.to_numpy() - s.rolling_mean(240).to_numpy()) / s.rolling_std(
                240
            ).to_numpy()
            expected = np.where(
                z < -2, 100, np.where(z > 2, -100, np.where(np.isfinite(z), 0, np.nan))
            )
        expected[np.arange(len(s)) % 3 != 0] = np.nan
        assert_allclose(result[:, j], expected)


def test_indicators_and_future_invariance():
    i = q.indicators
    x = np.arange(1.0, 50.0)
    high, low = x + 0.4, x - 0.4
    assert_allclose(i.true_range(high, low, x)[:3], [0.8, 1.4, 1.4])
    assert i.atr(high, low, x, 3)[2] == pytest.approx(1.2)
    assert i.natr(high, low, x, 3)[2] == pytest.approx(40)
    assert i.rsi(x, 3)[3] == 100
    assert i.rsi(np.ones(10), 3)[3] == 50
    assert i.adx(high, low, x, 3)[-1] == pytest.approx(100)
    assert_allclose(i.dmi(high, low, x, 3)[1][3:], 0)
    assert i.aroon(high, low, 3)[0][-1] == 100
    assert i.aroon(high, low, 3)[1][-1] == 0
    assert i.parabolic_sar(high, low)[1] == low[0]
    assert i.donchian(high, low, 3)[0][-1] == high[-1]
    assert i.stochastic(high, low, x, 3)[0][-1] == pytest.approx(100 * 2.4 / 2.8)
    assert np.isnan(i.stochastic_rsi(x, 3)[0][-1])
    assert i.williams_r(high, low, x, 3)[-1] == pytest.approx(-100 * 0.4 / 2.8)
    assert i.roc(x, 1)[-1] == pytest.approx(100 / 48)
    assert i.cci(high, low, x, 3)[-1] == pytest.approx(100)
    assert i.ultimate_oscillator(high, low, x, windows=(2, 3, 5))[-1] == pytest.approx(
        100 / 1.4
    )
    assert i.trix(x, 3)[-1] > 0
    assert i.sma(x, 3)[-1] == 48
    assert i.ema(x, 1)[-1] == 49
    assert i.wma(x, 3)[-1] == pytest.approx(290 / 6)
    assert i.hma(x, 4)[-1] == pytest.approx(49)
    assert i.dema(x, 3)[-1] == pytest.approx(49)
    assert i.tema(x, 3)[-1] == pytest.approx(49)
    assert i.macd(x, fast=3, slow=5, signal=2)[0][-1] == pytest.approx(1)
    assert i.bollinger(x, 3)[0][-1] == 50
    assert i.keltner(high, low, x, 3)[0][-1] == pytest.approx(50.8)
    assert i.obv(x, np.ones_like(x))[-1] == 48
    assert i.mfi(high, low, x, np.ones_like(x), 3)[-1] == 100
    assert i.accumulation_distribution(high, low, x, np.ones_like(x))[
        -1
    ] == pytest.approx(0, abs=1e-12)
    assert i.chaikin_money_flow(high, low, x, np.ones_like(x), 3)[-1] == pytest.approx(
        0
    )
    assert i.vwma(x, np.ones_like(x), 3)[-1] == 48
    assert i.rolling_vwap(2 * x, x, 3)[-1] == 2
    changed = x.copy()
    changed[30:] += 100
    for fn in [i.supertrend, i.parabolic_sar]:
        first = fn(high, low, x, 3) if fn == i.supertrend else fn(high, low)
        second = (
            fn(changed + 0.4, changed - 0.4, changed, 3)
            if fn == i.supertrend
            else fn(changed + 0.4, changed - 0.4)
        )
        assert np.isfinite(first[10:]).all()
        assert_allclose(first[:30], second[:30])


def test_cross_sectional_and_portfolio():
    c, p = q.cs, q.portfolio
    x = np.array([[4.0, 2.0, 2.0, 99.0], [1.0, 0.0, -1.0, np.nan]])
    active = np.array([[True, True, True, False], [True, True, True, False]])
    assert_allclose(c.rank(x, active=active)[0], [3, 1.5, 1.5, np.nan])
    assert_allclose(c.percentile(x, active=active)[0], [5 / 6, 1 / 3, 1 / 3, np.nan])
    assert_allclose(c.mean(x, active=active), [8 / 3, 0])
    assert c.std(x, active=active)[1] == 1
    assert_allclose(c.zscore(x, active=active)[1], [1, 0, -1, np.nan])
    assert c.robust_zscore(x, active=active)[1, 0] == pytest.approx(0.67448975)
    assert_allclose(c.winsorize(x, active=active, upper=0.5)[0], [2, 2, 2, np.nan])
    assert_allclose(c.quantile_groups(x, 3, active=active)[0], [2, 1, 1, np.nan])
    assert_array_equal(c.top_k(x, 2, active=active)[0], [1, 1, 0, 0])
    assert_array_equal(c.bottom_k(x, 1, active=active)[0], [0, 1, 0, 0])
    assert_allclose(c.group_neutralize([[3.0, 5.0, 8.0]], [1, 1, 2]), [[-1, 1, np.nan]])
    assert_allclose(c.market_neutralize(x, active=active)[1], [1, 0, -1, np.nan])
    assert_allclose(
        c.liquidity_weighted_normalize([[1.0, 2.0]], [[3.0, 1.0]]), [[0.6, 0.4]]
    )
    assert_allclose(
        c.beta_neutralize([[0.0, 2.0, 4.0, 6.0]], [[0.0, 1.0, 2.0, 3.0]]), 0, atol=1e-12
    )
    assert_allclose(c.long_short_normalize(x)[1], [0.5, 0, -0.5, np.nan])
    assert_allclose(p.equal_weight(active)[1], [1 / 3, 1 / 3, 1 / 3, np.nan])
    assert_allclose(p.rank_weight(x, active=active)[1], [0.5, 0, -0.5, np.nan])
    assert_allclose(p.inverse_volatility([[1.0, 2.0]]), [[2 / 3, 1 / 3]])
    assert_allclose(
        p.volatility_target([[0.5, 0.5]], [0.2], target=0.1), [[0.25, 0.25]]
    )
    assert_allclose(
        p.cap_exposures([[0.8, -0.5]], per_symbol=0.4, gross=0.4), [[0.2, -0.2]]
    )
    assert_allclose(
        p.turnover_rebalance([[1.0, -1.0]], [[0.0, 0.0]], max_turnover=0.4),
        [[0.2, -0.2]],
    )
    assert_allclose(
        p.position_size([[0.5, -0.5]], [100], decision_ms=[3], equity_available_ms=[2]),
        [[50, -50]],
    )
    with pytest.raises(ValueError):
        p.signed_notionals([[1.0]], [100], decision_ms=[3], equity_available_ms=[4])
    matrix = np.diag([1.0, 4.0])
    assert_allclose(p.minimum_variance(matrix), [0.8, 0.2], atol=1e-7)
    assert_allclose(p.correlation_aware(matrix), [0.8, 0.2], atol=1e-7)
    assert_allclose(p.risk_parity(matrix), [2 / 3, 1 / 3], atol=1e-7)
    assert_allclose(
        p.covariance_shrinkage([[2, 1], [1, 2]], intensity=0.5), [[2, 0.5], [0.5, 2]]
    )
    assert_allclose(p.constrained_weights(matrix, caps=[0.7, 1]), [0.7, 0.3], atol=1e-7)


def test_volatility_microstructure_and_risk():
    v, m, r = q.volatility, q.microstructure, q.risk
    x = np.array([1.0, 2.0, 3.0, 4.0, 5.0])
    assert v.realized_volatility(x, 2)[-1] == pytest.approx(np.sqrt(20.5))
    assert v.ewma_volatility(x, 1)[-1] == 5
    assert v.parkinson(x * 2, x, 2)[-1] == pytest.approx(np.sqrt(np.log(2)) / 2)
    assert v.garman_klass(x, x * 2, x, x, 2)[-1] == pytest.approx(
        np.log(2) / np.sqrt(2)
    )
    assert v.rogers_satchell(x, x * 2, x, x, 2)[-1] == pytest.approx(np.log(2))
    assert v.yang_zhang(x, x * 2, x, x, 3, session_ms=86400000)[-1] > 0
    assert v.semivariance([-2.0, 3.0], 2)[-1] == 2
    assert v.downside_volatility([-2.0, 3.0], 2)[-1] == pytest.approx(np.sqrt(2))
    assert v.upside_volatility([-2.0, 3.0], 2)[-1] == pytest.approx(np.sqrt(4.5))
    assert v.volatility_of_volatility(x, 2)[-1] == pytest.approx(np.sqrt(0.5))
    assert v.rolling_range(x + 1, x - 1, 2)[-1] == 3
    assert v.volatility_percentile(x, 3)[-1] == pytest.approx(5 / 6)
    assert v.trend_strength(x, 2)[-1] == 1
    assert_allclose(v.classify_regime([1, 2, 3, np.nan], 2, 3), [0, 1, 2, np.nan])
    assert v.detect_jumps([1, 1, 10], 2)[-1]
    for imbalance in (
        m.volume_imbalance,
        m.quote_volume_imbalance,
        m.trade_count_imbalance,
    ):
        assert_allclose(imbalance([3, 0], [1, 0]), [0.5, np.nan])
    assert_allclose(m.cumulative_volume_delta([3, 2], [1, 1]), [2, 3])
    assert m.vwap_deviation([3], [2])[0] == 0.5
    assert m.trade_intensity([4.0], interval_ms=5000)[0] == 0.8
    assert m.average_aggregate_trade_size([4], [2])[0] == 2
    assert m.average_constituent_trade_size([4], [4])[0] == 1
    assert m.large_aggregate_trade_proxy([1], [4])[0] == 0.25
    assert m.volume_acceleration(x)[-1] == 0
    assert m.volume_zscore(x, 3)[-1] == 1
    assert m.relative_volume(x, 2)[-1] == pytest.approx(5 / 3.5)
    assert m.amihud(x, 2 * x, 2)[-1] == 0.5
    assert m.return_per_volume(x, 2 * x)[-1] == 0.5
    assert m.price_impact_proxy(x, 2 * x, 3)[-1] == pytest.approx(0.5)
    assert np.isfinite(m.price_volume_divergence(x, 2 * x, 2)[-1])
    assert m.mark_to_trade_deviation([2], [1])[0] == 1
    assert m.funding_changes([0.001, 0.002])[-1] == 0.001
    with pytest.raises(ValueError, match="dedicated"):
        m.require_observation("l2_depth")
    assert_allclose(r.per_symbol_exposure([0, -2], [np.nan, 5]), [0, -10])
    assert r.gross_exposure([10, -10]) == 20 and r.net_exposure([10, -10]) == 0
    assert r.leverage([10, -10], 5) == 4 and r.concentration([10, -10]) == 0.5
    assert r.historical_var([-0.1, 0, 0.1], confidence=0.75) == pytest.approx(0.05)
    assert r.expected_shortfall([-0.1, 0, 0.1], confidence=0.75) == 0.1
    assert r.portfolio_volatility([0.5, 0.5], np.eye(2)) == pytest.approx(np.sqrt(0.5))
    assert_allclose(r.component_contribution([0.5, 0.5], np.eye(2)), np.sqrt(0.5) / 2)
    assert_allclose(
        r.correlated_exposure([0.5, 0.5], np.eye(2))["fraction"], [0.5, 0.5]
    )
    assert_array_equal(r.drawdown_duration([1, 2, 1, 1, 3]), [0, 0, 1, 2, 0])
    assert_allclose(r.stress_test([100, -100], [[0.05, 0.02]]), [3])


def test_signal_and_performance():
    s, p = q.signal, q.performance
    x = np.array([0.0, 2.0, 1.0, -2.0, 0.0])
    assert_allclose(s.threshold(x, 1), [0, 1, 0, -1, 0])
    assert_array_equal(s.crossover(x, 1), [0, 1, 0, -1, 0])
    assert_allclose(s.deadband(x, np.zeros(len(x)), width=1), [0, 2, 0, -2, 0])
    assert_allclose(s.regime_filter(x, x > 0), [0, 2, 1, 0, 0])
    assert_allclose(s.persistence(x), [0, 1, 2, -1, 0])
    assert_allclose(s.confirmation(x, 2), [0, 0, 1, 0, 0])
    assert_array_equal(s.cooldown(x != 0, 1), [False, True, False, True, False])
    assert_allclose(s.hysteresis(x, entry=2, exit=0.5), [0, 1, 1, -1, 0])
    assert_array_equal(s.entry_exit(x == 2, x < 0), [0, 1, 1, 0, 0])
    assert_array_equal(s.time_exit(x == 2, 2), [0, 0, 0, 1, 0])
    assert_array_equal(
        s.trailing_stop([1, 2, 3, 2, 1], x == 2, fraction=0.2), [0, 0, 0, 1, 0]
    )
    assert_allclose(s.combine([x, x], [1, -1]), 0)
    metrics = p.equity_metrics([100, 110, 99, 120], periods_per_year=3)
    assert metrics["total_return"] == pytest.approx(0.2)
    assert metrics["annualized_return"] == pytest.approx(0.2)
    assert metrics["maximum_drawdown"] == pytest.approx(0.1)
    assert metrics["calmar"] == pytest.approx(2)
    traded = p.trade_statistics([3, 2, -2, 0], top=1)
    assert traded["profit_factor"] == 2.5 and traded["win_rate"] == 0.5
    assert (
        traded["average_win"] == 2.5
        and traded["average_loss"] == -2
        and traded["expected_payoff"] == 0.75
    )
    assert (
        p.cost_attribution(net_pnl=10, fees=3, spread_slippage_impact=2, funding=-1)[
            "gross_pnl"
        ]
        == 16
    )
    assert p.turnover(20, 100) == 0.2 and p.exposure_adjusted_return(0.2, 2) == 0.1
    assert p.sharpe([0.1, 0.2], periods_per_year=1) == pytest.approx(
        0.15 / np.std([0.1, 0.2], ddof=1)
    )
    assert p.sortino([0.2, -0.1], periods_per_year=1) == pytest.approx(
        0.05 / np.sqrt(0.005)
    )


def test_inference_against_libraries():
    s = q.stats
    rng = np.random.default_rng(12)
    x = rng.normal(size=400)
    y = 3 * x + 1 + rng.normal(size=len(x)) * 0.01
    assert s.pearson(x, y) > 0.999
    assert s.spearman(x, y) == pytest.approx(scipy_stats.spearmanr(x, y).statistic)
    assert s.kendall(x, y) == pytest.approx(scipy_stats.kendalltau(x, y).statistic)
    assert s.autocorrelation(x, 3)[0] == 1
    assert s.partial_autocorrelation(x, 3)[1] == pytest.approx(
        s.autocorrelation(x, 3)[1]
    )
    assert s.cross_correlation(x, y, 3)[0] > 0.99
    fit = s.multiple_regression(y, np.column_stack([x, x * x]), hac_lags=3)
    assert_allclose(fit.coefficients, [1, 3, 0], atol=0.005)
    assert fit.covariance_type == "HAC"
    assert s.weighted_regression(y, x, weights=np.ones(len(x))).coefficients[
        1
    ] == pytest.approx(s.beta(y, x))
    assert abs(s.residuals(y, x).mean()) < 1e-10
    assert s.distribution_summary(x)["n"] == 400
    assert_array_equal(s.empirical_cdf([2, 1, 2])[0], [1, 2])
    assert s.quantiles([1, 2], 0.5) == 1.5
    assert s.adf(x)["pvalue"] < 0.01
    assert 0 <= s.kpss(np.cumsum(x))["pvalue"] <= 1
    assert (
        s.cointegration(np.cumsum(x) + rng.normal(size=len(x)), np.cumsum(x))["pvalue"]
        < 0.05
    )
    assert 0 < s.hurst(np.cumsum(x), estimator="increments") < 1
    assert s.hurst(x, estimator="rescaled_range") > 0
    lo, hi = s.confidence_interval(x, hac_lags=4)
    assert lo < x.mean() < hi
    assert_array_equal(
        s.block_bootstrap(x, block_size=8, repetitions=8, seed=7),
        s.bootstrap(x, block_size=8, repetitions=8, seed=7),
    )
    assert_allclose(s.adjust_pvalues([0.01, 0.04, 0.06]), [0.03, 0.08, 0.08])
    with pytest.raises(ValueError, match="complete"):
        s.regression(y, np.where(x > 0, x, np.nan), hac_lags=2)


def test_blocks_resampling_and_labels():
    value = np.array([[10, 20], [12, np.nan], [12, 22], [13, 23], [14, 24], [15, 30]])
    block = panel(value, status=[[0, 0], [0, 3], [1, 0], [0, 0], [0, 0], [0, 0]])
    from quant.data import _slice

    original = list(q.data.resample([block], "10s"))
    cut = list(
        q.data.resample(
            [_slice(block, 0, 1), _slice(block, 1, 3), _slice(block, 3, 6)], "10s"
        )
    )
    assert_allclose(
        np.concatenate([b.values["close"] for b in cut]), original[0].values["close"]
    )
    assert original[0].status[0, 1] == 3
    assert original[0].available_at_ms[0] == 10100
    assert_allclose(original[0].values["close"][:, 0], [12, 13, 15])
    labels = q.evaluate.forward_returns(block, 2)
    expected = [
        [0.2, np.nan],
        [1 / 12, np.nan],
        [1 / 6, 1 / 11],
        [2 / 13, 7 / 23],
        [np.nan, np.nan],
        [np.nan, np.nan],
    ]
    assert_allclose(q.evaluate.research_values(labels), expected)
    with pytest.raises(TypeError, match="Research-only"):
        q.simulator.targets(block, labels)
    with pytest.raises(TypeError):
        np.asarray(labels)
    with pytest.raises(ValueError, match="axes"):
        q.data.align(block, replace(block, symbols=("B", "A")))
    with pytest.raises(ValueError, match="availability"):
        q.simulator.targets(block, value, available_at_ms=block.timestamps_ms)
    with pytest.raises(PermissionError):
        q.evaluate.forward_returns(panel(value, start=as_millis("2026-08-25")), 1)


def test_resample_ohlcv_and_unknown():
    t = np.array([0, 5000, 10000, 15000])
    values = {
        "open": [9, 10, 12, 12],
        "high": [9, 14, 12, 15],
        "low": [9, 10, 12, 11],
        "close": [9, 12, 12, 14],
        "volume": [0, 2, 0, 3],
        "quote_volume": [0, 24, 0, 39],
        "vwap": [np.nan, 12, np.nan, 13],
        "trade_count": [0, 5, 0, 7],
    }
    block = q.data.Block(
        t,
        ("A",),
        t + 5100,
        np.array([[1], [0], [1], [0]], dtype="u1"),
        {k: np.array(v)[:, None] for k, v in values.items()},
        5000,
        {},
    )
    result = next(q.data.resample([block], "10s"))
    assert_allclose(result.values["open"].ravel(), [10, 12])
    assert_allclose(result.values["low"].ravel(), [10, 11])
    assert_allclose(result.values["high"].ravel(), [14, 15])
    assert_allclose(result.values["vwap"].ravel(), [12, 13])
    assert_array_equal(result.values["trade_count"].ravel(), [5, 7])
    for kind in (2, 3, 4):
        altered = panel([np.nan, 10, 10, 12], status=[kind, 0, 1, 0])
        reduced = next(q.data.resample([altered], 10000))
        assert np.isnan(reduced.values["close"][0, 0])
        assert reduced.status[0, 0] == (3 if kind == 3 else 2)


def test_predictive_diagnostics_and_validation(tmp_path):
    e, v = q.evaluate, q.validation
    block = panel(np.cumprod(np.tile([1.01, 1.02, 1.03, 1.04, 1.05], (45, 1)), axis=0))
    labels = e.forward_returns(block, 4)
    score = np.tile(np.arange(5.0), (45, 1))
    assert_allclose(e.information_coefficient(score, labels)[:41], 1)
    assert_allclose(e.cross_sectional_ic(score, labels)[:41], 1)
    assert e.quantile_spread(score, labels)[0] > 0
    assert e.quantile_returns(score, labels)[1][0].tolist() == [1, 1, 1, 1, 1]
    assert (
        e.long_short_asymmetry(score - 2, labels)["long"]["mean"]
        > e.long_short_asymmetry(score - 2, labels)["short"]["mean"]
    )
    assert e.event_study(score > 3, block, [1])[1]["observations"] == 44
    assert e.alpha_decay(score, block, [1])[1] == pytest.approx(1)
    assert e.regime_performance(score, labels, np.ones(45))["1.0"]["observations"] == 41
    assert e.monthly_stability(score, labels)
    assert e.rolling_ic(score, labels, 2)[1] == pytest.approx(1)
    assert np.isnan(e.icir(np.ones(4)))
    assert e.signal_coverage(score) == 1
    assert_allclose(e.signal_persistence(score)[1:], 1)
    with pytest.raises(ValueError, match="overlap"):
        e.dependent_mean_test(np.arange(9.0), hac_lags=1, overlap_bars=4)
    assert (
        0
        <= e.dependent_mean_test(np.arange(20.0), hac_lags=4, overlap_bars=4)["pvalue"]
        <= 1
    )
    assert_array_equal(
        v.purge([1, 2, 4, 7], [2, 4, 4, 9], 3, 6, embargo_ms=2), [1, 0, 0, 0]
    )
    windows = list(
        v.walk_forward(0, 60000, "20s", "10s", expanding=True, embargo_ms=1000)
    )
    assert len(windows) == 4 and windows[1].train == (0, 29000)
    assert v.chronological_split(0, 10, 20, 30).test == (20, 30)
    with pytest.raises(PermissionError):
        v.chronological_split("2026-07-01", "2026-07-02", "2026-08-25", "2026-11-25")
    with pytest.raises(ValueError):
        v.record_final_release(
            tmp_path / "release.json", owner_authorization="", frozen_spec={}
        )


def test_lazy_features_cache_and_chunk_invariance(tmp_path):
    from quant.data import _slice

    f = q.features
    block = panel([1, 2, 3, np.nan, 8, 16, 16, 2], status=[0, 0, 0, 2, 0, 0, 0, 0])
    lag = f.expression("quant.ts.lag", "close", periods=1, units="USDT/base")
    ema = f.expression("quant.ts.ema", "close", span=4, units="USDT/base")
    roll = f.expression("quant.ts.rolling_mean", lag, window="10s", units="USDT/base")
    expressions = {"a": ema, "b": roll, "c": roll}
    cache = f.Cache(directory=tmp_path, memory_bytes=128, disk_bytes=1 << 20)
    opts = {
        "cache": cache,
        "fingerprint": "audited-fixture",
        "context": {"split": "fixture"},
    }
    complete = list(f.evaluate([block], expressions, **opts))
    parts = [_slice(block, 0, 1), _slice(block, 1, 4), _slice(block, 4, 8)]
    chunked = list(f.evaluate(parts, expressions, **opts))
    assert cache.hits == 0
    repeat = list(f.evaluate(parts, expressions, **opts))
    assert cache.hits > 0
    for field in expressions:
        x = np.concatenate([b.values[field] for b in chunked])
        assert_allclose(complete[0].values[field], x, atol=1e-12)
        assert_allclose(np.concatenate([b.values[field] for b in repeat]), x)
    changed = f.cache_key(
        function="a",
        version="1",
        fingerprint="x",
        parameters={},
        missing_policy="reset",
        configuration={},
    )
    assert changed != f.cache_key(
        function="a",
        version="1",
        fingerprint="x",
        parameters={},
        missing_policy="carry",
        configuration={},
    )
    with pytest.raises(ValueError):
        f.expression(
            "quant.evaluate.forward_returns", "close", units="return", horizon=1
        )


def test_experiments_optimization_and_feature_queue(tmp_path):
    e, o, r = q.experiment, q.optimize, q.requests
    db = tmp_path / "research.sqlite"
    kwargs = dict(
        hypothesis="Fixture",
        parameters={"span": 2},
        start=0,
        end=100,
        universe=["A"],
        dataset_fingerprint="fixture",
        seed=2,
        features=["quant.ts.ema"],
        evaluation={},
        costs={},
        candidate_count=2,
    )
    a = e.register(db, **kwargs)
    b = e.register(db, **kwargs)
    assert (
        e.run(db, a, lambda p: {"metrics": {"return": -0.02}})["metrics"]["return"]
        == -0.02
    )
    with pytest.raises(ZeroDivisionError):
        e.run(db, b, lambda p: operator.truediv(1, 0))
    assert e.reproduce(db, b)["outcome"]["failure"]["type"] == "ZeroDivisionError"
    assert e.search(db, status="failed")[0]["id"] == b
    assert e.compare(db, [a, b], metrics=["return"])[1]["return"] is None
    assert list(
        o.grid_search({"x": [1, 2], "y": [2, 3]}, constraint=lambda p: p["x"] == p["y"])
    ) == [{"x": 2, "y": 2}]
    assert list(o.random_search({"x": [1, 2]}, trials=6, seed=1)) == list(
        o.random_search({"x": [1, 2]}, trials=6, seed=1)
    )
    assert list(o.parallel(abs, [-1, -2], workers=2, backend="thread")) == [1, 2]
    data = [[1, 1], [2, 0], [0, 0], [np.nan, 1]]
    assert_array_equal(o.pareto_frontier(data), [0, 1])
    assert_allclose(o.multi_objective_rank(data), [1, 1, 2, np.nan])
    assert o.parameter_stability([{"x": 1}, {"x": 1}], [1, 3])["x"][1]["mean"] == 2
    request = r.Request(
        "l2_depth",
        "quant.microstructure",
        "Test a missing input",
        ("depth",),
        {},
        True,
        True,
        ("Verify source",),
    )
    request_id = r.submit(db, request)
    assert r.submit(db, replace(request, experiment_id=a)) == request_id
    assert r.check("quant.ts.ema", streaming=True, causal=True)
    r.transition(
        db, request_id, "accepted", reviewer="fixture", findings="Test acceptance"
    )
    assert r.search(db, status="accepted")[0]["id"] == request_id
    with pytest.raises(ValueError, match="matching"):
        r.transition(
            db, request_id, "implemented", reviewer="fixture", findings="No code"
        )
    r.transition(db, request_id, "rejected", reviewer="fixture", findings="No source")
    assert r.search(db, status="rejected")[0]["review"]["findings"] == "No source"


def test_registry_inventory_and_determinism(tmp_path):
    names = q.registry.list_functions()
    assert len(names) == len(set(names))
    assert "quant.ts.ema" in q.registry.list_by_category("ts")
    assert "quant.cs.beta_neutralize" in q.registry.search("beta neutralize")
    assert "quant.cs.beta_neutralize" in q.registry.search(
        "cross-sectional neutralization"
    )
    assert (
        q.registry.describe("quant.evaluate.forward_returns")["causality"]
        == "research-only"
    )
    one = q.registry.generate_catalog(tmp_path / "one.json")
    two = q.registry.generate_catalog(tmp_path / "two.json")
    assert one.read_bytes() == two.read_bytes()
    catalog = json.loads(one.read_text(encoding="utf-8"))
    for entry in catalog:
        assert (
            entry["description"] and entry["units"] and entry["mathematical_definition"]
        )
        assert Path(entry["tests"][0]).is_file()
        assert callable(q.registry.resolve(entry["name"]))
    assert q.registry.generate_api(tmp_path / "api.md").is_file()


def test_discovery_cli(tmp_path, monkeypatch, capsys):
    import sys

    from quant.__main__ import main

    monkeypatch.setattr(sys, "argv", ["quant", "functions", "--category", "ts"])
    main()
    assert "quant.ts.ema" in capsys.readouterr().out.splitlines()
    monkeypatch.setattr(sys, "argv", ["quant", "describe", "quant.ts.ema"])
    main()
    assert json.loads(capsys.readouterr().out)["streaming"] == "recursive-state"
    monkeypatch.setattr(
        sys, "argv", ["quant", "search", "cross-sectional neutralization"]
    )
    main()
    assert "quant.cs.beta_neutralize" in capsys.readouterr().out
    monkeypatch.setattr(sys, "argv", ["quant", "describe", "quant.ts.unimplemented"])
    with pytest.raises(SystemExit) as raised:
        main()
    assert raised.value.code == 2
    catalog, reference = tmp_path / "catalog.json", tmp_path / "reference.md"
    monkeypatch.setattr(
        sys,
        "argv",
        ["quant", "catalog", "--out", str(catalog), "--markdown", str(reference)],
    )
    main()
    assert catalog.is_file() and reference.is_file()


def test_incremental_diagnostics_and_exposure_constraint():
    rng = np.random.default_rng(314)
    scores = rng.normal(size=(120, 8))
    controls = rng.normal(size=(120, 8))
    market = panel(
        np.exp(np.cumsum(np.vstack([np.zeros(8), 0.01 * scores[:-1]]), axis=0))
    )
    labels = q.evaluate.forward_returns(market, 1)
    assert (
        np.nanmean(q.evaluate.incremental_ic(scores + 2 * controls, controls, labels))
        > 0.75
    )
    assert_allclose(q.evaluate.alpha_correlation(scores, scores), 1)
    assert q.evaluate.per_symbol_ic(scores, labels)[0] > 0.99
    assert q.evaluate.signal_turnover(scores)[1] == pytest.approx(
        np.abs(scores[1] - scores[0]).sum()
    )
    assert np.all(np.abs(q.evaluate.signal_autocorrelation(scores)) < 0.3)
    constraints = np.array([[1.0, 0.0, 0.0]])
    weights = q.portfolio.constrained_weights(
        np.eye(3), exposures=constraints, exposure_targets=[0.5]
    )
    assert_allclose(weights, [0.5, 0.25, 0.25])
