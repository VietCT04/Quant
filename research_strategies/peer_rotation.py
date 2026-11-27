"""Trade persistent changes in relative price strength with rank hysteresis."""

import numpy as np

import quant as q
from quant.strategy import Parameter, Plan, Plot, Strategy
from quant.trading import proportional_size


HOUR = 3_600_000
TICKS = 2 * 720


def prepare(p):
    e = q.features.expression
    def change(periods):
        return e("quant.ts.pct_change", "close", periods=periods, units="fraction")
    vol = e("quant.ts.rolling_std", change(1), window=24, units="fraction")
    graph = {
        "recent": change(3),
        "slow": change(12),
        "previous": e("quant.ts.lag", change(36), periods=3, units="fraction"),
        "vol": e("quant.ts.lag", vol, periods=1, units="fraction"),
    }
    return Plan(graph, warmup_bars=40 * TICKS, decision_ticks=TICKS,
                feature_periods=dict.fromkeys(graph, TICKS), plots=(
                    Plot("peer_score", "Relative entry score", "ratio"),
                    Plot("relative_rank", "Peer rank percentile", "fraction"),
                ))


def initialize(p, symbols, rng):
    return p, np.zeros(len(symbols), dtype="i8")


def peer_scores(mode, recent, slow, previous, vol, valid, cap):
    score = np.full(len(valid), np.nan)
    if np.count_nonzero(valid) < 20:
        return score
    recent, slow, previous, vol = (x[valid] for x in (recent, slow, previous, vol))
    relative = recent - np.median(recent)
    slow = slow - np.median(slow)
    previous = previous - np.median(previous)
    scale = np.maximum(vol, .003)
    if mode == "momentum":
        raw = (slow + .4 * previous) / scale
        directional_gate = np.abs(relative) <= cap
    elif mode == "reversal":
        raw = -relative / scale
        directional_gate = (np.abs(slow) < .10) & (np.abs(relative) <= cap)
    else:
        raw = (.65 * previous - relative) / scale
        directional_gate = (previous * relative < 0) & (np.abs(relative) <= cap)
    score[valid] = np.where(directional_gate, raw, np.nan)
    return score


def trade(state, row, account, orders):
    p, submitted = state
    spot, recent, slow, previous, vol = (row.values[k][0] for k in ("close", "recent", "slow", "previous", "vol"))
    traded = (row.status[0] == 0) & np.isfinite(spot) & (spot > 0)
    valid = traded & np.isfinite(recent) & np.isfinite(slow) & np.isfinite(previous) & np.isfinite(vol)
    score = peer_scores(p["mode"], recent, slow, previous, vol, valid, p["shock_cap"])
    # Keep a peer's rank undefined when its own inputs are unavailable.
    rank = np.full(len(spot), np.nan)
    ranked = np.flatnonzero(np.isfinite(score))
    ranked = ranked[np.argsort(score[ranked], kind="stable")]
    if len(ranked) >= 2:
        rank[ranked] = np.linspace(0., 1., len(ranked))
    qty, entry = account.positions["qty"], account.positions["entry"]
    hold = account.clock_ms - submitted
    for i in np.flatnonzero((qty != 0) & traded):
        side = np.sign(qty[i])
        rr = rank[i] if side > 0 else 1 - rank[i]
        contrary = np.isfinite(rr) and rr < p["keep"]
        invalidated = entry[i] > 0 and side * (spot[i] / entry[i] - 1) <= -p["stop"]
        if hold[i] >= p["hold"] * HOUR or invalidated or (contrary and hold[i] >= 6 * HOUR):
            orders.close(row.symbols[i])

    ready = set(account.ready)
    margin = account.available_margin
    notional = proportional_size(account, p["fraction"])
    if margin is not None and notional >= 10. and len(ranked) >= 8:
        for side in (1, -1) if p["short"] else (1,):
            room = p["slots"] - np.count_nonzero(side * qty > 0)
            for i in ranked[::-1] if side > 0 else ranked:
                if room <= 0 or margin < 1.01 * notional:
                    break
                if side * score[i] <= p["minimum_score"]:
                    break
                rr = rank[i] if side > 0 else 1 - rank[i]
                if qty[i] or rr < p["enter"] or row.symbols[i] not in ready:
                    continue
                if side > 0:
                    orders.buy(row.symbols[i], notional=notional)
                else:
                    orders.sell(row.symbols[i], notional=notional)
                submitted[i] = account.clock_ms
                room -= 1
                margin -= 1.01 * notional
    return {"peer_score": score[None, :], "relative_rank": rank[None, :]}


STRATEGY = Strategy("peer_rotation_persistence", "1.0.0", {
    "mode": Parameter("string", "momentum", choices=("momentum", "reversal", "pullback")),
    "short": Parameter("boolean", False),
    "fraction": Parameter("number", .10, .025, .20),
    "slots": Parameter("integer", 6, 2, 10),
    "enter": Parameter("number", .86, .55, .98),
    "keep": Parameter("number", .60, .45, .80),
    "minimum_score": Parameter("number", 1., .05, 10.),
    "hold": Parameter("integer", 48, 12, 168),
    "stop": Parameter("number", .08, .025, .20),
    "shock_cap": Parameter("number", .12, .02, .50),
}, ("close",), prepare, initialize, orders=trade)
