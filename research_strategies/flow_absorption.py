"""Test whether directional taker flow that fails to move price precedes reversal."""

import numpy as np

import quant as q
from quant.strategy import Parameter, Plan, Plot, Strategy
from quant.trading import proportional_size


HOUR = 3_600_000
TICKS = 1440


def prepare(p):
    e = q.features.expression

    def change(periods):
        return e("quant.ts.pct_change", "close", periods=periods, units="fraction")

    buy = e("quant.ts.rolling_sum", "buy_quote_volume", window=2, units="USDT")
    sell = e("quant.ts.rolling_sum", "sell_quote_volume", window=2, units="USDT")
    graph = {
        "flow": e("quant.microstructure.quote_volume_imbalance", buy, sell, units="fraction"),
        "volume_4h": e("quant.ts.rolling_sum", "quote_volume", window=2, units="USDT"),
        "prior_volume": e("quant.ts.lag",
                          e("quant.ts.rolling_mean", "quote_volume", window=24, units="USDT"),
                          periods=2, units="USDT"),
        "move": change(2),
        "trend": e("quant.ts.lag", change(24), periods=2, units="fraction"),
        "recent": change(1),
    }
    return Plan(graph, warmup_bars=27 * TICKS, decision_ticks=TICKS,
                feature_periods=dict.fromkeys(graph, TICKS), plots=(
                    Plot("flow", "Four-hour taker quote imbalance", "fraction"),
                    Plot("residual_move", "Four-hour return less peer median", "fraction"),
                    Plot("pressure_ratio", "Turnover relative to preceding average", "ratio"),
                ))


def initialize(p, symbols, rng):
    return p, np.full(len(symbols), -(1 << 60), dtype="i8")


def trade(state, row, account, orders):
    p, attempted = state
    price, flow, volume, prior, move, trend, recent = (row.values[k][0] for k in (
        "close", "flow", "volume_4h", "prior_volume", "move", "trend", "recent"))
    qty, entry = account.positions["qty"], account.positions["entry"]
    held = qty != 0
    valid = (row.status[0] == 0) & np.isfinite(price) & (price > 0)
    side = np.sign(qty)
    marked_return = np.divide(price, entry, out=np.full_like(price, np.nan), where=entry > 0) - 1
    age = account.clock_ms - attempted
    exit_now = (age >= p["hold"] * HOUR) | (side * marked_return <= -p["stop"])
    for i in np.flatnonzero(held & valid & exit_now):
        orders.close(row.symbols[i])

    relative = np.full_like(move, np.nan)
    activity = np.divide(volume, 2 * prior, out=np.full_like(volume, np.nan), where=prior > 0)
    peers = valid & np.isfinite(move) & np.isfinite(trend)
    if np.count_nonzero(peers) >= 20:
        relative[peers] = move[peers] - np.median(move[peers])
        trend_center = np.median(trend[peers])
        intended = -np.sign(flow)
        relative_aligned = intended * relative
        supported = (relative_aligned >= -p["slip"]) & (relative_aligned <= p["chase"])
        eligible = (peers & ~held & supported & (age >= p["cooldown"] * HOUR)
                    & (np.abs(flow) >= p["imbalance"]) & (activity >= p["activity"])
                    & (volume >= p["min_turnover"]) & (intended * recent >= -p["recent_limit"])
                    & (intended * trend >= -p["trend_limit"])
                    & (intended * trend_center >= -p["market_limit"]))
        if p["direction"]:
            eligible &= intended == p["direction"]
        indices = np.flatnonzero(eligible)
        score = np.abs(flow[indices]) * np.sqrt(np.minimum(activity[indices], 5))
        size = proportional_size(account, p["fraction"])
        margin = account.available_margin
        room = p["slots"] - np.count_nonzero(held)
        ready = set(account.ready)
        if margin is not None and size >= 10.:
            for i in indices[np.argsort(-score, kind="stable")]:
                if room <= 0 or margin < 1.01 * size:
                    break
                symbol = row.symbols[i]
                if symbol not in ready:
                    continue
                action = orders.buy if intended[i] > 0 else orders.sell
                action(symbol, notional=size)
                attempted[i] = account.clock_ms
                room -= 1
                margin -= 1.01 * size
    return {"residual_move": relative[None, :], "pressure_ratio": activity[None, :]}


STRATEGY = Strategy("taker_flow_absorption", "1.0.0", {
    "direction": Parameter("integer", 1, choices=(-1, 0, 1)),
    "imbalance": Parameter("number", .14, .015, .50),
    "activity": Parameter("number", 1.3, .5, 5.),
    "min_turnover": Parameter("number", 1_000_000., 50_000., 30_000_000.),
    "slip": Parameter("number", .005, 0., .04),
    "chase": Parameter("number", .026, .01, .10),
    "recent_limit": Parameter("number", .012, .001, .06),
    "trend_limit": Parameter("number", .12, .03, .40),
    "market_limit": Parameter("number", .065, .01, .30),
    "hold": Parameter("integer", 18, 4, 72),
    "stop": Parameter("number", .042, .008, .16),
    "cooldown": Parameter("integer", 24, 4, 96),
    "slots": Parameter("integer", 8, 2, 14),
    "fraction": Parameter("number", .085, .015, .20),
}, ("close", "buy_quote_volume", "sell_quote_volume", "quote_volume"), prepare, initialize, orders=trade)
