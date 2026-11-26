"""Registered counterpart of the fixed EMA crossover fixture."""

import numpy as np

import quant as q
from quant.strategy import Decision, Parameter, Plan, Plot, Strategy


def prepare(p):
    if p["fast"] >= p["slow"]:
        raise ValueError("The fast EMA span must be smaller than the slow span")
    graph = {name: q.features.expression("quant.ts.ema", "close", span=p[name], units="USDT/base")
             for name in ("fast", "slow")}
    return Plan(graph, decision_ticks=p["decision_ticks"], plots=(
        Plot("fast", f"Fast EMA {p['fast']}", "USDT/base", "price", "#efc276"),
        Plot("slow", f"Slow EMA {p['slow']}", "USDT/base", "price", "#91aafa"),
        Plot("spread", "EMA spread", "USDT/base", thresholds=(0,)),
        Plot("target", "Requested target", "USDT", color="#aaa2ed"),
    ))


def initialize(parameters, symbols, rng):
    return dict(parameters)


def signal(state, block):
    return q.math.safe_divide(block.values["fast"] - block.values["slow"], block.values["close"])


def targets(state, block):
    spread = block.values["fast"] - block.values["slow"]
    signed = np.sign(spread)
    if state["side"] == "long":
        signed = np.maximum(signed, 0)
    elif state["side"] == "short":
        signed = np.minimum(signed, 0)
    return Decision(state["notional"] * signed, {"spread": spread})


STRATEGY = Strategy("ema_crossover", "1.0.0", {
    "fast": Parameter("integer", 10, 1, 100_000),
    "slow": Parameter("integer", 30, 2, 100_000),
    "notional": Parameter("number", 300, 1, 1_000_000),
    "side": Parameter("string", "both", choices=("long", "short", "both")),
    "decision_ticks": Parameter("integer", 1, 1, 86400),
}, ("close",), prepare, initialize, signal=signal, targets=targets)
