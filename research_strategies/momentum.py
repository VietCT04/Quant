"""Cross-sectional momentum with SDK rank weights and fixed portfolio gross target."""

import quant as q
from quant.strategy import Decision, Parameter, Plan, Plot, Strategy


def prepare(p):
    return Plan({"momentum": q.features.expression("quant.ts.pct_change", "close", periods=p["periods"], units="fraction")},
                warmup_bars=p["periods"], plots=(
        Plot("momentum", "Trailing return", "fraction"),
        Plot("weight", "Cross-sectional rank weight", "fraction", thresholds=(0,), color="#efc276"),
        Plot("target", "Requested target", "USDT", color="#91aafa"),
    ))


def initialize(p, symbols, rng):
    if len(symbols) < 2:
        raise ValueError("Cross-sectional momentum requires at least two symbols")
    return dict(p)


def signal(state, block):
    return block.values["momentum"]


def targets(state, block):
    weight = q.portfolio.rank_weight(signal(state, block))
    return Decision(weight * state["gross"], {"weight": weight})


STRATEGY = Strategy("cross_sectional_momentum", "1.0.0", {
    "periods": Parameter("integer", 12, 1, 17280),
    "gross": Parameter("number", 1200, 1, 1_000_000),
}, ("close",), prepare, initialize, signal=signal, targets=targets,
    capabilities=("quant.portfolio.rank_weight",))
