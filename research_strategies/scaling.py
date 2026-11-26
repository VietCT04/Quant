"""Feedback-driven long-only entry, pyramid, partial exit, and flattening."""

import numpy as np

import quant as q
from quant.strategy import Decision, Parameter, Plan, Plot, Strategy


def prepare(p):
    mean = q.features.expression("quant.ts.ema", "close", span=20, units="USDT/base")
    return Plan({"basis": mean}, warmup_bars=19, decision_ticks=1, plots=(
        Plot("basis", "EMA basis", "USDT/base", "price"),
        Plot("deviation", "Price relative to basis", "fraction", thresholds=(0, p["entry"], p["pyramid"]), color="#efc276"),
        Plot("reason", "Decision reason", "code", labels={1: "Entry", 2: "Pyramid", 3: "Partial exit", 4: "Flatten"}),
        Plot("target", "Requested target", "USDT", color="#91aafa"),
    ))


def initialize(p, symbols, rng):
    n = len(symbols)
    return dict(p), np.zeros(n), np.zeros(n)


def deviation(block):
    close, basis = (block.values[name] for name in ("close", "basis"))
    return q.math.safe_divide(close - basis, basis)


def signal(state, block):
    return deviation(block)


def decide(state, block, portfolio):
    p, peak, initial = state
    qty = portfolio.positions["qty"]
    flat = qty == 0
    peak[flat] = 0
    initial[flat] = 0
    new = (qty > 0) & (initial == 0)
    initial[new] = qty[new]
    peak[:] = np.maximum(qty, peak)
    move = deviation(block)[0]
    close = block.values["close"][0]
    risk_sized = min(p["unit"], portfolio.equity * p["equity_fraction"])
    open_ = flat & (move >= p["entry"])
    expanded = peak > initial * 1.2
    add = (qty > 0) & ~expanded & (move >= p["pyramid"])
    trim = (qty > 0) & expanded & (qty >= peak * .8) & (move <= p["entry"])
    exit_ = (qty > 0) & (move <= 0)
    target = np.select([exit_, trim, add, open_], [0, peak * close * .5, 2 * risk_sized, risk_sized], default=np.nan)
    reason = np.select([exit_, trim, add, open_], [4., 3., 2., 1.], default=np.nan)
    return Decision(target[None], {"deviation": move[None], "reason": reason[None]})


STRATEGY = Strategy("stateful_scaling", "1.0.0", {
    "unit": Parameter("number", 300, 1, 1_000_000),
    "equity_fraction": Parameter("number", .003, .00001, 1),
    "entry": Parameter("number", .00012, .00001, .01),
    "pyramid": Parameter("number", .0003, .00001, .1),
}, ("close",), prepare, initialize, signal=signal, decide=decide)
