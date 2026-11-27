"""Trade recovery in market participation after a broad directional washout."""

import numpy as np

import quant as q
from quant.strategy import Parameter, Plan, Plot, Strategy
from quant.trading import proportional_size


HOUR = 3_600_000
TICKS = 720


def prepare(p):
    e = q.features.expression

    def change(period):
        return e("quant.ts.pct_change", "close", periods=period, units="fraction")

    graph = {
        "washout": e("quant.ts.lag", change(8), periods=1, units="fraction"),
        "bounce": change(1),
        "prior_trend": e("quant.ts.lag", change(24), periods=9, units="fraction"),
        "turnover": e("quant.ts.rolling_sum", "quote_volume", window=6, units="USDT"),
    }
    return Plan(graph, warmup_bars=34 * TICKS, decision_ticks=TICKS,
                feature_periods=dict.fromkeys(graph, TICKS), plots=(
                    Plot("washout", "Eight-hour return before confirmation", "fraction"),
                    Plot("bounce", "Completed confirmation-hour return", "fraction"),
                    Plot("rebound_breadth", "Share of peers rebounding", "fraction"),
                ))


def initialize(p, symbols, rng):
    return p, np.full(len(symbols), -(1 << 60), dtype="i8"), {"event": -(1 << 60)}


def trade(state, row, account, orders):
    p, opened, clock = state
    price, washout, bounce, trend, volume = (row.values[k][0] for k in
                                            ("close", "washout", "bounce", "prior_trend", "turnover"))
    qty, entry = (account.positions[k] for k in ("qty", "entry"))
    active = qty != 0
    now = account.clock_ms
    side = p["side"]
    valid = (row.status[0] == 0) & np.isfinite(price) & (price > 0)
    net_move = side * (np.divide(price, entry, out=np.full_like(price, np.nan), where=entry > 0) - 1.)
    for i in np.flatnonzero(active & valid & ((now-opened >= p["hold"] * HOUR)
                                               | (net_move < -p["stop"]) | (net_move > p["take"]))):
        orders.close(row.symbols[i])

    peers = valid & np.isfinite(washout) & np.isfinite(bounce) & np.isfinite(trend)
    breadth = np.nan
    if np.count_nonzero(peers) >= 20:
        shock = side * washout
        recovery = side * bounce
        breadth = np.mean(recovery[peers] >= p["peer_bounce"])
        event = (now - clock["event"] >= p["event_gap"] * HOUR
                 and np.median(shock[peers]) <= -p["market_shock"]
                 and np.mean(shock[peers] <= -p["market_shock"] * .65) >= p["shock_breadth"]
                 and breadth >= p["recovery_breadth"]
                 and np.median(side * trend[peers]) >= -p["prior_limit"])
        if event:
            candidates = np.flatnonzero(peers & ~active & (volume >= p["min_volume"])
                                        & (shock < -.005) & (recovery >= p["peer_bounce"])
                                        & (recovery <= -.7 * shock) & (side * trend >= -2 * p["prior_limit"]))
            score = recovery[candidates] if p["leaders"] else (-shock-recovery)[candidates]
            order = candidates[np.argsort(-score, kind="stable")]
            ready = set(account.ready)
            margin = account.available_margin
            size = proportional_size(account, p["fraction"])
            room = min(p["event_slots"], p["max_slots"] - np.count_nonzero(active))
            if margin is not None and size >= 10.:
                for i in order:
                    if room <= 0 or margin < 1.01 * size:
                        break
                    if row.symbols[i] not in ready:
                        continue
                    action = orders.buy if side > 0 else orders.sell
                    action(row.symbols[i], notional=size)
                    opened[i] = now
                    clock["event"] = now
                    margin -= 1.01 * size
                    room -= 1
    return {"rebound_breadth": np.full((1, len(price)), breadth)}


STRATEGY = Strategy("market_breadth_rebound", "1.0.0", {
    "side": Parameter("integer", 1, choices=(-1, 1)),
    "market_shock": Parameter("number", .018, .006, .08),
    "shock_breadth": Parameter("number", .65, .5, .98),
    "recovery_breadth": Parameter("number", .60, .50, .95),
    "peer_bounce": Parameter("number", .0025, .0005, .02),
    "prior_limit": Parameter("number", .04, .005, .15),
    "min_volume": Parameter("number", 2_500_000., 100_000., 25_000_000.),
    "leaders": Parameter("boolean", True),
    "hold": Parameter("integer", 16, 4, 48),
    "event_gap": Parameter("integer", 18, 4, 72),
    "event_slots": Parameter("integer", 10, 4, 18),
    "max_slots": Parameter("integer", 16, 10, 25),
    "stop": Parameter("number", .042, .008, .15),
    "take": Parameter("number", .065, .01, .22),
    "fraction": Parameter("number", .055, .01, .10),
}, ("close", "quote_volume"), prepare, initialize, orders=trade)
