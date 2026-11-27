"""Test reversals after one direction dominates trailing realized variance."""

from collections import deque

import numpy as np

import quant as q
from quant.strategy import Parameter, Plan, Plot, Strategy
from quant.trading import proportional_size


TICKS = 1440
HOUR = 3_600_000


def prepare(p):
    e = q.features.expression
    graph = {
        "move": e("quant.ts.pct_change", "close", periods=1, units="fraction"),
        "turnover": e("quant.ts.rolling_sum", "quote_volume", window=3, units="USDT"),
    }
    return Plan(graph, warmup_bars=4 * TICKS, decision_ticks=TICKS,
                feature_periods=dict.fromkeys(graph, TICKS), plots=(
                    Plot("variance_balance", "Upside minus downside variance share", "ratio"),
                    Plot("relative_balance", "Variance balance less peer median", "ratio"),
                ))


def initialize(p, symbols, rng):
    return p, deque(maxlen=p["history"]), np.full(len(symbols), -(1 << 60), dtype="i8")


def balance(returns):
    upside = np.square(np.maximum(returns, 0.)).sum(axis=0)
    downside = np.square(np.minimum(returns, 0.)).sum(axis=0)
    total = upside + downside
    share = np.divide(upside - downside, total, out=np.full_like(total, np.nan), where=total > 0.)
    return share, np.sqrt(total)


def trade(state, row, account, orders):
    p, history, opened = state
    price, latest, turnover = (row.values[k][0] for k in ("close", "move", "turnover"))
    traded = (row.status[0] == 0) & np.isfinite(price) & (price > 0)
    qty, entry = (account.positions[k] for k in ("qty", "entry"))
    held = qty != 0
    age = (account.clock_ms - opened) / HOUR
    pnl = np.sign(qty) * (np.divide(price, entry, out=np.full_like(price, np.nan), where=entry > 0.) - 1.)
    for i in np.flatnonzero(held & traded & ((age >= p["hold"]) | (pnl <= -p["stop"]) | (pnl >= p["take"]))):
        orders.close(row.symbols[i])
    signed = np.full_like(price, np.nan)
    relative = signed.copy()
    preceding = np.asarray(history) if len(history) == p["history"] else None
    history.append(latest.copy())
    if preceding is not None:
        signed, scale = balance(preceding)
        observed = traded & np.isfinite(signed)
        if np.count_nonzero(observed) >= 20:
            relative[observed] = signed[observed] - np.median(signed[observed])
        cumulative_move = np.prod(1. + preceding, axis=0) - 1.
        side = -np.sign(signed)
        rebound = side * latest
        allowed = (observed & ~held & (age >= p["cooldown"]) & (np.abs(signed) >= p["skew"])
                   & (side * relative <= -p["separation"]) & (scale >= p["min_scale"])
                   & (scale <= p["max_scale"]) & (-side * cumulative_move >= p["min_move"])
                   & (-side * cumulative_move <= p["max_move"]) & (turnover >= p["liquidity"])
                   & (rebound >= p["turn_min"]) & (rebound <= p["chase"]))
        if p["direction"]:
            allowed &= side == p["direction"]
        idx = np.flatnonzero(allowed)
        notional = proportional_size(account, p["fraction"])
        margin = account.available_margin
        room = p["slots"] - np.count_nonzero(held)
        ready = set(account.ready)
        if margin is not None and notional >= 10.:
            for i in idx[np.argsort(-np.abs(relative[idx]), kind="stable")]:
                if room <= 0 or margin < 1.01 * notional:
                    break
                if row.symbols[i] not in ready:
                    continue
                action = orders.buy if side[i] > 0 else orders.sell
                action(row.symbols[i], notional=notional)
                opened[i] = account.clock_ms
                margin -= 1.01 * notional
                room -= 1
    return {"variance_balance": signed[None, :], "relative_balance": relative[None, :]}


STRATEGY = Strategy("signed_variance_reversal", "1.0.0", {
    "history": Parameter("integer", 12, choices=(6, 12, 24)),
    "direction": Parameter("integer", 1, choices=(-1, 0, 1)),
    "skew": Parameter("number", .61, .25, .92),
    "separation": Parameter("number", .24, .05, .70),
    "min_scale": Parameter("number", .025, .010, .10),
    "max_scale": Parameter("number", .14, .06, .35),
    "min_move": Parameter("number", .022, .008, .11),
    "max_move": Parameter("number", .14, .07, .35),
    "turn_min": Parameter("number", .005, .001, .03),
    "chase": Parameter("number", .034, .012, .10),
    "liquidity": Parameter("number", 2_000_000., 200_000., 25_000_000.),
    "hold": Parameter("integer", 20, 6, 80),
    "cooldown": Parameter("integer", 30, 12, 96),
    "stop": Parameter("number", .04, .012, .14),
    "take": Parameter("number", .055, .018, .18),
    "slots": Parameter("integer", 10, 4, 15),
    "fraction": Parameter("number", .062, .02, .10),
}, ("close", "quote_volume"), prepare, initialize, orders=trade)
