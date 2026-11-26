"""Independent deterministic accounting fixtures for the next-open contract."""

import numpy as np
import pytest

from simulator.native import Engine, OrderSettings, SETTLEMENT
from quant.explorer_ledger import reconstruct
from simulator.reporting import record
from simulator.feed import EMPTY, MISSING, PRELISTING, QUARANTINED
from test_research import market


def run(engine, opens, *, starts=None, prices=None, status=None, orders=(), funding=(), volume=0):
    rows = np.array(opens, dtype=float).reshape(-1, engine.n)
    marks = np.array(prices, dtype=float).reshape(rows.shape) if prices is not None else rows
    matrix = np.stack((marks, marks * 2, np.full(rows.shape, volume), marks), axis=-1)
    starts = np.arange(len(rows)) * 5000 if starts is None else starts
    flags = np.zeros_like(rows, dtype="u1") if status is None else np.asarray(status).reshape(rows.shape)
    engine.run_orders(starts, matrix, flags, rows, list(funding), list(orders))
    return engine.orders()


def request(stamp, order_id, symbol=0, kind=0, size=100):
    return stamp, order_id, symbol, kind, stamp, size


@pytest.mark.parametrize("rest", [100, 250, 800])
def test_next_open_once_and_terminal_accounting(rest):
    with Engine(OrderSettings(), [[0, 0]]) as engine:
        fills = run(engine, [90, 100, 120], prices=[90, rest, 120],
                    orders=[request(5000, 1)], volume=0)
        buy = fills[0]
        assert (buy["decision"], buy["ms"], buy["bar_start"]) == (5000, 5000, 5000)
        assert (buy["filled"], buy["rounded"], buy["after"]) == (1, 1, 1)
        assert buy["price"] == pytest.approx(100.02)
        assert buy["fee"] == pytest.approx(.05001)
        assert engine.positions()["qty"].tolist() == [1]
        pre_close = engine.equity()["equity"]
        assert pre_close == pytest.approx(10_019.92999)
        engine.terminal(15_000)
        close, = engine.orders()
        assert close["reason"] == 21 and close["filled"] == -1
        assert close["price"] == pytest.approx(119.976)
        assert engine.equity()["equity"] == pytest.approx(10_019.846002)
        positions, _, _ = reconstruct("BTCUSDT", [record(buy), record(close)], [],
                                      record(engine.positions()[0]), 15_000)
        assert len(positions) == 1 and positions[0]["terminal_close"]
        assert positions[0]["net_pnl"] == pytest.approx(19.846002)


def test_rounding_minimum_and_dust_reduction():
    with Engine(OrderSettings(quantity_step=.01), [[0, 1000]]) as engine:
        fills = run(engine, [101, 101], orders=[
            request(0, 1, size=9.99), request(0, 2, size=10),
            request(0, 3, kind=1, size=.005), request(5000, 4, kind=3, size=.5),
            request(5000, 5, kind=2),
        ])
        assert fills["reason"].tolist() == [17, 0, 16, 0, 0]
        np.testing.assert_allclose(fills["filled"], [0, .09, 0, -.04, -.05])
        assert engine.positions()[0]["qty"] == 0


def test_margin_rejects_entire_order_and_reduction_frees_margin():
    with Engine(OrderSettings(initial_wallet=1000, leverage=1), [[0, 0]] * 2) as engine:
        rows = run(engine, [[100, 100]] * 3, orders=[
            request(0, 1, symbol=1, size=900), request(0, 2, symbol=0, size=200),
            request(5000, 3, symbol=1, kind=2), request(5000, 4, symbol=0, size=200),
        ])
        assert rows["id"].tolist() == [1, 2, 3, 4]
        assert rows["reason"].tolist() == [0, 18, 0, 0]
        assert 0 < rows[1]["buying_power"] < 100
        assert rows[1]["filled"] == 0
        assert engine.positions()["qty"].tolist() == [2, 0]


@pytest.mark.parametrize("flag", [EMPTY, MISSING, QUARANTINED, PRELISTING])
def test_next_missing_open_is_rejected_once(flag):
    with Engine(OrderSettings(), [[0, 0]]) as engine:
        rows = run(engine, [90, 100, 110], status=[0, flag, 0],
                   orders=[request(5000, 1)])
        assert rows["reason"].tolist() == [10 + flag]
        assert engine.positions()[0]["qty"] == 0


def test_scale_reverse_and_funding_at_open():
    settings = OrderSettings(spread_bps=0, slippage_bps=0, fee_bps=0)
    with Engine(settings, [[0, 0]]) as engine:
        result = run(engine, [100, 120, 130, 140], orders=[
            request(0, 1, kind=1, size=2), request(5000, 2, kind=1, size=2),
            request(10000, 3, kind=3, size=.25), request(15000, 4, kind=1, size=-4),
        ], funding=[(5000, 0, .001, 120), (15000, 0, .001, 140)])
        assert result["closed"].tolist() == [0, 0, 1, 3]
        assert result["entry"].tolist() == [100, 110, 110, 140]
        assert engine.cashflows()["qty"].tolist() == [2, 3]
        assert engine.equity()["funding"] == pytest.approx(-.66)
        pos = engine.positions()[0]
        assert pos["realized"] == 110 and pos["qty"] == -1
        rows, _, _ = reconstruct("BTCUSDT", [record(x) for x in result],
                                 [record(x) for x in engine.cashflows()], record(pos), 20_000)
        assert [r["state"] for r in rows] == ["closed", "open"]
        assert rows[0]["net_pnl"] == pytest.approx(109.34)


def test_insolvency_and_optional_drawdown_suspend_new_orders():
    for wallet, leverage, stop in [(1000, 2, 0), (1000, 1, .05)]:
        with Engine(OrderSettings(initial_wallet=wallet, leverage=leverage, drawdown_limit=stop),
                    [[0, 0]]) as engine:
            events = run(engine, [100, 1, 100], prices=[100, 1, 100],
                         orders=[request(0, 1, size=wallet * leverage * .9), request(10000, 2)])
            assert events["reason"].tolist() == [0, 22, 18]
            assert engine.positions()[0]["qty"] == 0


def test_higher_features_observe_only_completed_frames():
    import quant as q
    from quant._timeframes import CompletedFeatures
    from quant.data import Block, _slice

    timestamps = np.arange(12) * 5000
    close = np.arange(12, dtype=float)[:, None] + 10
    block = Block(timestamps, ("BTCUSDT",), timestamps + 5000, np.zeros_like(close, dtype="u1"),
                  {"close": close}, 5000, {"close": "USDT/base"})
    expression = q.features.expression("quant.ts.lag", "close", periods=1, units="USDT/base")
    def stream(source):
        sampler = CompletedFeatures({3: {"prior": expression}, 2: {"other": expression}}, 5000, 2**20, None)
        return np.concatenate([sampler.advance(_slice(source, i, i + 2))["prior"]
                               for i in range(0, len(close), 2)]).ravel()
    baseline = stream(block)
    np.testing.assert_allclose(baseline, [np.nan] * 5 + [12] * 3 + [15] * 3 + [18])
    close[8:] = 10000
    np.testing.assert_equal(stream(block)[:8], baseline[:8])



def test_order_trial_warmup_state_isolation_and_callback_feedback(market, tmp_path):
    from dataclasses import replace
    from quant._batch import Candidate, run_batch
    from quant.strategy import Plan, Runner, Strategy

    selection = replace(market, start=10_000)
    def initialize(parameters, symbols, rng):
        return {"history": 0, "calls": 0}
    def warm(state, block):
        state["history"] += len(block.timestamps_ms)
    def trade(state, row, portfolio, orders):
        state["calls"] += 1
        if state["calls"] == 1:
            assert state["history"] == 2
            assert portfolio.equity == 10_000 and portfolio.positions[1]["qty"] == 0
            orders.buy(row.symbols[1], notional=100)
        elif state["calls"] == 2:
            assert portfolio.positions[1]["qty"] > 0
            assert len(portfolio.outcomes) == 1
            assert portfolio.outcomes[0]["decision"] == portfolio.clock_ms - 5000
            assert portfolio.outcomes[0]["ms"] == portfolio.clock_ms - 5000
            orders.reduce(row.symbols[1], fraction=.5)
    plan = Plan(warmup_bars=2)
    registered = Strategy("fixture", "2.0.0", {}, ("close",), lambda p: plan,
                          initialize, orders=trade, warm=warm)
    resolved = registered, plan, {}, {"decision_ticks": 1}
    runners = [Runner(resolved, selection.symbols, 1) for _ in range(2)]
    cases = [Candidate(str(i), run, config=OrderSettings(), fields=run.fields,
                       feedback=True, warmup_bars=2) for i, run in enumerate(runners)]
    result = run_batch(selection, cases, out=tmp_path / "independent", workers=2, block_steps=3)
    assert not result.failures
    a, b = [result.results[str(i)].report for i in range(2)]
    assert a == b
    assert a["execution_diagnostics"]["filled"] == 2
    assert a["execution_diagnostics"]["terminal"] == 1
    assert a["completed_positions"] == 1
    assert a["bar_range_ms"] == [10_000, market.end]
    assert a["sharpe"] is None
