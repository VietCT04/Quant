"""Directional persistence with an entry hurdle and explicit position exits."""

import numpy as np

import quant as q
from quant.strategy import Parameter, Plan, Plot, Strategy


def prepare(p):
    return Plan(
        {"trend": q.features.expression("quant.ts.pct_change", "close",
                                        periods=p["lookback_hours"], units="fraction")},
        warmup_bars=(p["lookback_hours"] + 2) * 720,
        decision_ticks=720,
        plots=(Plot("trend", "Trailing hourly return", "fraction",
                    thresholds=(-p["entry"], -p["exit"], p["exit"], p["entry"])),),
        feature_periods={"trend": 720},
    )


def initialize(p, symbols, rng):
    return dict(p)


def trade(p, row, account, orders):
    for i, (symbol, actual) in enumerate(zip(row.symbols, account.positions["qty"], strict=True)):
        score = row.values["trend"][0, i]
        if symbol not in account.ready or not np.isfinite(score):
            continue
        if (actual > 0 and score <= p["exit"]) or (actual < 0 and score >= -p["exit"]):
            orders.close(symbol)
        elif actual == 0:
            if score >= p["entry"]:
                orders.buy(symbol, notional=p["size"])
            elif p["allow_short"] and score <= -p["entry"]:
                orders.sell(symbol, notional=p["size"])


STRATEGY = Strategy("slow_directional_persistence", "1.0.0", {
    "lookback_hours": Parameter("integer", 48, 6, 240),
    "entry": Parameter("number", .025, .001, .5),
    "exit": Parameter("number", 0, 0, .05),
    "size": Parameter("number", 1000, 10, 10000),
    "allow_short": Parameter("boolean", False),
}, ("close",), prepare, initialize, orders=trade, capabilities=("quant.ts.pct_change",))
