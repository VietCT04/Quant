"""Continuous targets from aggressive quote-flow imbalance."""

import numpy as np

import quant as q
from quant.strategy import Decision, Parameter, Plan, Plot, Strategy


def prepare(p):
    flow = q.features.expression("quant.microstructure.quote_volume_imbalance",
                                "buy_quote_volume", "sell_quote_volume", units="fraction")
    average = q.features.expression("quant.ts.ema", flow, span=p["span"], units="fraction")
    return Plan({"imbalance": average}, warmup_bars=p["span"] - 1, plots=(
        Plot("imbalance", "Smoothed aggressive quote imbalance", "fraction", thresholds=(0,)),
        Plot("regime", "Volume threshold passed", "flag", color="#efc276"),
        Plot("target", "Requested target", "USDT", color="#91aafa"),
    ))


def initialize(p, symbols, rng):
    return dict(p)


def signal(state, block):
    return block.values["imbalance"]


def targets(state, block):
    liquid = block.values["quote_volume"] >= state["min_volume"]
    score = signal(state, block)
    target = np.where(np.isfinite(score), np.where(liquid, score * state["notional"], 0.0), np.nan)
    return Decision(target, {"regime": liquid.astype(float)})


STRATEGY = Strategy("flow_imbalance", "1.0.0", {
    "span": Parameter("integer", 6, 1, 17280),
    "notional": Parameter("number", 600, 1, 1_000_000),
    "min_volume": Parameter("number", 1_000, 0),
}, ("buy_quote_volume", "sell_quote_volume", "quote_volume"), prepare, initialize,
    signal=signal, targets=targets)
