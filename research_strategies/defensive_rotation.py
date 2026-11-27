"""Trade independent relative demand after a preceding broad BTC drawdown."""

import numpy as np

import quant as q
from quant.strategy import Parameter, Plan, Plot, Strategy
from quant.trading import proportional_size


HOUR = 3_600_000
TICKS = 1440


def prepare(p):
    e = q.features.expression

    def change(n):
        return e("quant.ts.pct_change", "close", periods=n, units="fraction")

    def prior(node, n=1):
        return e("quant.ts.lag", node, periods=n, units=node.units)

    graph = {
        "week": prior(change(84)),
        "relative_day": prior(change(12)),
        "continuation": change(1),
        "upper": prior(e("quant.ts.rolling_max", "high", window=6, units="USDT/base")),
        "volume_2h": e("quant.ts.rolling_sum", "quote_volume", window=1, units="USDT"),
        "volume_baseline": prior(e("quant.ts.rolling_mean", "quote_volume", window=24, units="USDT")),
    }
    return Plan(graph, warmup_bars=86 * TICKS, decision_ticks=TICKS,
                feature_periods=dict.fromkeys(graph, TICKS), plots=(
                    Plot("relative_day", "Preceding 24-hour return", "fraction"),
                    Plot("resilient_score", "Preceding return less peer median", "fraction"),
                ))


def initialize(p, symbols, rng):
    btc = symbols.index("BTCUSDT") if "BTCUSDT" in symbols else None
    return p, btc, np.full(len(symbols), -(1 << 60), dtype="i8"), np.full(len(symbols), np.nan)


def trade(state, row, account, orders):
    p, btc, attempted, peak = state
    close, week, own, now_move, upper, volume, baseline = (row.values[k][0] for k in (
        "close", "week", "relative_day", "continuation", "upper", "volume_2h", "volume_baseline"))
    qty, entry = (account.positions[k] for k in ("qty", "entry"))
    held = qty != 0
    age = (account.clock_ms - attempted) / HOUR
    traded = (row.status[0] == 0) & np.isfinite(close) & (close > 0)
    active = held & traded
    peak[active] = np.maximum(peak[active], close[active])
    peak[~held] = np.nan
    for i in np.flatnonzero(active & ((age >= p["hold"]) | (close <= entry * (1 - p["stop"]))
                                     | (close <= peak * (1 - p["trail"])))):
        orders.close(row.symbols[i])

    peers = traded & np.isfinite(own)
    alpha = np.full_like(close, np.nan)
    if btc is None or np.count_nonzero(peers) < 20 or not np.isfinite(week[btc]) or week[btc] > -p["btc_drop"]:
        return {"resilient_score": alpha[None, :]}
    alpha[peers] = own[peers] - np.median(own[peers])
    burst = np.divide(volume, baseline, out=np.full_like(close, np.nan), where=baseline > 0)
    eligible = (peers & ~held & (alpha >= p["relative_min"]) & (own >= -p["own_loss"])
                & (now_move >= p["move"]) & (now_move <= p["chase"])
                & (volume >= p["volume_min"]) & (burst >= p["activity"]) & (age >= p["cooldown"]))
    if p["breakout"]:
        eligible &= close > upper
    eligible[btc] = False
    candidates = np.flatnonzero(eligible)
    score = alpha[candidates] + .5 * now_move[candidates]
    room = p["slots"] - np.count_nonzero(held)
    size = proportional_size(account, p["fraction"])
    margin = account.available_margin
    ready = set(account.ready)
    if margin is not None and size >= 10.:
        for i in candidates[np.argsort(-score, kind="stable")]:
            if room <= 0 or margin < 1.01 * size:
                break
            if row.symbols[i] not in ready:
                continue
            orders.buy(row.symbols[i], notional=size)
            attempted[i], peak[i] = account.clock_ms, close[i]
            margin -= 1.01 * size
            room -= 1
    return {"resilient_score": alpha[None, :]}


STRATEGY = Strategy("defensive_demand_rotation", "1.0.0", {
    "btc_drop": Parameter("number", .045, .018, .14),
    "relative_min": Parameter("number", .018, .003, .12),
    "own_loss": Parameter("number", .03, .005, .10),
    "move": Parameter("number", .004, .001, .025),
    "chase": Parameter("number", .048, .018, .15),
    "volume_min": Parameter("number", 700_000., 150_000., 10_000_000.),
    "activity": Parameter("number", 1.2, .7, 5.),
    "breakout": Parameter("boolean", True),
    "hold": Parameter("integer", 26, 6, 80),
    "cooldown": Parameter("integer", 30, 8, 100),
    "stop": Parameter("number", .035, .01, .12),
    "trail": Parameter("number", .045, .015, .13),
    "slots": Parameter("integer", 10, 4, 16),
    "fraction": Parameter("number", .065, .02, .10),
}, ("close", "high", "quote_volume"), prepare, initialize, orders=trade)
