"""Require a positive prior directional context for downside inventory seeds."""

from dataclasses import replace
from pathlib import Path

import quant as q
from quant.strategy import Parameter, Plot

from . import shock_liquidity


def prepare(p):
    plan = shock_liquidity.prepare(p)
    period = 720
    past = q.features.expression("quant.ts.pct_change", "close", periods=p["context_hours"], units="fraction")
    context = q.features.expression("quant.ts.lag", past, periods=1, units="fraction")
    return replace(
        plan, warmup_bars=(p["context_hours"] + 2) * period,
        features=plan.features | {"context_return": context},
        feature_periods={"context_return": period},
        plots=(*plan.plots, Plot("context_return", "Pre-shock hourly trend", "fraction",
                                 thresholds=(p["context_min"],))),
    )


def trade(state, row, account, orders):
    trend = row.values["context_return"][0]
    allowed = {symbol for symbol, value in zip(row.symbols, trend, strict=True)
               if state[0]["side"] * value >= state[0]["context_min"]}
    return shock_liquidity.trade(state, row, replace(account, ready=tuple(s for s in account.ready if s in allowed)), orders)


STRATEGY = replace(
    shock_liquidity.STRATEGY, version="2.0.0", prepare=prepare, orders=trade,
    sources=(Path(__file__).with_name("shock_liquidity.py"),),
    parameters=shock_liquidity.STRATEGY.parameters | {
        "context_hours": Parameter("integer", 24, 6, 48),
        "context_min": Parameter("number", .03, -.02, .12),
    },
)
