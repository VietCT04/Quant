"""Fit a small causal peer-return model from already observed price changes."""

from collections import deque
from dataclasses import dataclass

import numpy as np

import quant as q
from quant.strategy import Parameter, Plan, Plot, Strategy
from quant.trading import proportional_size


STEP = 7_200_000
TICKS = 1440
FEATURES = ("short_move", "daily_move", "multi_day", "flow", "vol")


def prepare(p):
    e = q.features.expression

    def change(periods):
        return e("quant.ts.pct_change", "close", periods=periods, units="fraction")

    flow = e("quant.microstructure.quote_volume_imbalance", "buy_quote_volume", "sell_quote_volume",
             units="fraction")
    graph = {
        "short_move": change(3), "daily_move": change(12), "multi_day": change(36),
        "flow": e("quant.ts.rolling_mean", flow, window=4, units="fraction"),
        "vol": e("quant.ts.rolling_std", change(1), window=24, units="fraction"),
    }
    return Plan(graph, warmup_bars=38 * TICKS, decision_ticks=TICKS,
                feature_periods=dict.fromkeys(graph, TICKS),
                plots=(Plot("forecast", "Causal peer forecast", "fraction"),))


@dataclass
class Model:
    history: deque
    gram: np.ndarray
    cross: np.ndarray
    decay: float
    opened: np.ndarray
    updates: int = 0


def initialize(p, symbols, rng):
    width = len(FEATURES)
    return p, Model(deque(maxlen=p["horizon"]), np.zeros((width, width)), np.zeros(width),
                    np.exp(-1 / (p["decay_days"] * 12)), np.zeros(len(symbols), dtype="i8"))


def update(p, model, now, matrix, valid, prices):
    model.gram *= model.decay
    model.cross *= model.decay
    if len(model.history) == p["horizon"]:
        prior, predictors, mask, past_price = model.history[0]
        matured = mask & valid
        for _, _, continuous, _ in model.history:
            matured &= continuous
        if now - prior == p["horizon"] * STEP and np.count_nonzero(matured) >= 16:
            x = predictors[matured]
            observed = np.clip(prices[matured] / past_price[matured] - 1, -.12, .12)
            y = observed - np.median(observed)
            model.gram += x.T @ x / len(y)
            model.cross += x.T @ y / len(y)
            model.updates += 1
    model.history.append((now, matrix, valid, prices.copy()))


def trade(state, row, account, orders):
    p, model = state
    clock = account.clock_ms
    price = row.values["close"][0]
    values = np.column_stack([row.values[k][0] for k in FEATURES])
    traded = (row.status[0] == 0) & np.isfinite(price) & (price > 0)
    valid = traded & np.isfinite(values).all(axis=1)
    predictors = np.zeros_like(values)
    if np.count_nonzero(valid) >= 20:
        observed = values[valid]
        center = np.median(observed, axis=0)
        lower, upper = np.percentile(observed, (25, 75), axis=0)
        predictors[valid] = np.clip((observed - center) / np.maximum(upper - lower, .002), -3., 3.)
    else:
        valid[:] = False
    update(p, model, clock, predictors, valid, price)

    forecast = np.full(len(price), np.nan)
    if model.updates >= 96 and valid.any():
        coefficients = np.linalg.solve(model.gram + p["ridge"] * np.eye(len(FEATURES)), model.cross)
        forecast[valid] = predictors[valid] @ coefficients
    qty = account.positions["qty"]
    held = qty != 0
    age = clock - model.opened
    for i in np.flatnonzero(held & traded):
        signed = np.sign(qty[i]) * forecast[i]
        if age[i] >= p["hold"] * STEP or (age[i] >= 3 * STEP and signed < -p["edge"] * .5):
            orders.close(row.symbols[i])

    ready = set(account.ready)
    margin = account.available_margin
    size = proportional_size(account, p["fraction"])
    if margin is not None and size >= 10.:
        direction = np.abs(forecast) if p["short"] else forecast
        candidates = np.flatnonzero(~held & (direction >= p["edge"]))
        room = p["slots"] - np.count_nonzero(held)
        for i in candidates[np.argsort(-direction[candidates], kind="stable")]:
            if room <= 0 or margin < 1.01 * size:
                break
            symbol = row.symbols[i]
            if symbol not in ready:
                continue
            action = orders.buy if forecast[i] > 0 else orders.sell
            action(symbol, notional=size)
            model.opened[i] = clock
            room -= 1
            margin -= 1.01 * size
    return {"forecast": forecast[None, :]}


STRATEGY = Strategy("adaptive_peer_spread", "1.0.0", {
    "horizon": Parameter("integer", 6, choices=(3, 6, 12)),
    "hold": Parameter("integer", 10, 3, 36),
    "decay_days": Parameter("integer", 24, 7, 90),
    "ridge": Parameter("number", .4, .01, 20.),
    "edge": Parameter("number", .004, .0015, .04),
    "fraction": Parameter("number", .08, .025, .20),
    "slots": Parameter("integer", 9, 2, 10),
    "short": Parameter("boolean", False),
}, ("close", "buy_quote_volume", "sell_quote_volume"), prepare, initialize, orders=trade)
