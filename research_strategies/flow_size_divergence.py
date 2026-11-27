"""Test continuation when quote size and trade counts disagree on direction."""

import numpy as np

import quant as q
from quant.strategy import Parameter, Plan, Plot, Strategy
from quant.trading import proportional_size


TICKS = 2880
HOUR = 3_600_000
FIELDS = ("close", "quote_volume", "buy_quote_volume", "sell_quote_volume", "buy_trade_count", "sell_trade_count")


def prepare(p):
    e = q.features.expression

    def rate(function, buy, sell):
        return e(f"quant.microstructure.{function}", *(e("quant.ts.rolling_sum", field, window=2, units=unit)
                 for field, unit in (buy, sell)), units="fraction")

    r = e("quant.ts.pct_change", "close", periods=6, units="fraction")
    graph = {
        "dollar_flow": rate("quote_volume_imbalance", ("buy_quote_volume", "USDT"), ("sell_quote_volume", "USDT")),
        "count_flow": rate("trade_count_imbalance", ("buy_trade_count", "count"), ("sell_trade_count", "count")),
        "recent": e("quant.ts.pct_change", "close", periods=2, units="fraction"),
        "prior_day": e("quant.ts.lag", r, periods=2, units="fraction"),
        "turnover": e("quant.ts.rolling_sum", "quote_volume", window=2, units="USDT"),
    }
    return Plan(graph, warmup_bars=9 * TICKS, decision_ticks=TICKS, feature_periods=dict.fromkeys(graph, TICKS),
                plots=(Plot("dollar_flow", "Aggressive quote volume imbalance", "fraction"),
                       Plot("count_flow", "Aggressive constituent count imbalance", "fraction"),
                       Plot("size_signal", "Quote-count divergence less peer median", "fraction")))


def initialize(p, symbols, rng):
    return p, np.full(len(symbols), -(1 << 60), dtype="i8")


def trade(state, row, account, orders):
    p, attempted = state
    close, flow, count, recent, prior, volume = (row.values[key][0] for key in (
        "close", "dollar_flow", "count_flow", "recent", "prior_day", "turnover"))
    qty, entry = (account.positions[name] for name in ("qty", "entry"))
    held = qty != 0
    side = np.sign(qty)
    traded = (row.status[0] == 0) & np.isfinite(close) & (close > 0)
    age = (account.clock_ms - attempted) / HOUR
    change = side * (np.divide(close, entry, out=np.full_like(close, np.nan), where=entry > 0) - 1.)
    exit_now = (age >= p["hold"]) | (change < -p["stop"]) | ((age >= 12) & (side * flow < -p["flow_min"]))
    for i in np.flatnonzero(held & traded & exit_now):
        orders.close(row.symbols[i])
    valid = traded & np.isfinite(flow) & np.isfinite(count) & np.isfinite(prior)
    signed = np.full_like(close, np.nan)
    if np.count_nonzero(valid) >= 20:
        imbalance = flow[valid] - count[valid]
        signed[valid] = imbalance - np.median(imbalance)
    direction = np.sign(signed)
    ready = set(account.ready)
    eligible = (valid & ~held & (np.abs(signed) >= p["size_gap"]) & (direction * flow >= p["flow_min"])
                & (direction * count <= p["count_max"]) & (direction * recent >= -p["recent_limit"])
                & (direction * recent <= p["chase"]) & (direction * prior >= -p["prior_limit"])
                & (volume >= p["min_volume"]) & (age >= p["cooldown"]))
    if p["direction"]:
        eligible &= direction == p["direction"]
    candidates = np.flatnonzero(eligible)
    size = proportional_size(account, p["fraction"])
    margin = account.available_margin
    room = p["slots"] - np.count_nonzero(held)
    if margin is not None and size >= 10.:
        for i in candidates[np.argsort(-np.abs(signed[candidates]), kind="stable")]:
            if room <= 0 or margin < size * 1.01:
                break
            if row.symbols[i] not in ready:
                continue
            action = orders.buy if direction[i] > 0 else orders.sell
            action(row.symbols[i], notional=size)
            attempted[i] = account.clock_ms
            margin -= size * 1.01
            room -= 1
    return {"size_signal": signed[None, :]}


STRATEGY = Strategy("quote_count_flow_divergence", "1.0.0", {
    "size_gap": Parameter("number", .095, .025, .30),
    "flow_min": Parameter("number", .065, .018, .25),
    "count_max": Parameter("number", .02, -.10, .10),
    "recent_limit": Parameter("number", .018, .004, .08),
    "chase": Parameter("number", .075, .018, .22),
    "prior_limit": Parameter("number", .065, .015, .22),
    "min_volume": Parameter("number", 1_500_000., 200_000., 20_000_000.),
    "direction": Parameter("integer", 1, choices=(-1, 0, 1)),
    "hold": Parameter("integer", 36, 12, 96),
    "cooldown": Parameter("integer", 40, 12, 120),
    "stop": Parameter("number", .052, .012, .16),
    "slots": Parameter("integer", 9, 3, 15),
    "fraction": Parameter("number", .075, .025, .15),
}, FIELDS, prepare, initialize, orders=trade)
