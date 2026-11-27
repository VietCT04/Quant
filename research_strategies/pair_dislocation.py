"""Trade convergence of dislocations in predefined economic peer pairs."""

from collections import deque

import numpy as np

import quant as q
from quant.strategy import Parameter, Plan, Plot, Strategy
from quant.trading import proportional_size


PAIRS = (
    ("BTCUSDT", "ETHUSDT"), ("SOLUSDT", "AVAXUSDT"), ("ADAUSDT", "SUIUSDT"),
    ("DOGEUSDT", "1000PEPEUSDT"), ("AAVEUSDT", "UNIUSDT"), ("BNBUSDT", "HYPEUSDT"),
    ("LTCUSDT", "BCHUSDT"), ("ZECUSDT", "XMRUSDT"), ("NEARUSDT", "INJUSDT"),
)
TICKS = 1440
HOUR = 3_600_000


def prepare(p):
    e = q.features.expression
    graph = {"turnover": e("quant.ts.rolling_sum", "quote_volume", window=6, units="USDT")}
    return Plan(graph, warmup_bars=6 * TICKS, decision_ticks=TICKS,
                feature_periods=dict.fromkeys(graph, TICKS), plots=(
                    Plot("pair_z", "Signed deviation from trailing peer spread", "ratio"),
                ))


def initialize(p, symbols, rng):
    indices = {name: i for i, name in enumerate(symbols)}
    pairs = [(indices[a], indices[b]) for a, b in PAIRS if a in indices and b in indices]
    return p, pairs, deque(maxlen=p["history"]), {}, {}


def estimate(path, latest, beta_weighted, min_corr):
    returns = np.diff(path, axis=0)
    observed = np.isfinite(returns).all(axis=1)
    if observed.sum() < .9 * len(returns):
        return None
    covariance = np.cov(returns[observed], rowvar=False)
    var_a, var_b = covariance.diagonal()
    if min(var_a, var_b) < 1e-8 or covariance[0, 1] / np.sqrt(var_a * var_b) < min_corr:
        return None
    beta = np.clip(covariance[0, 1] / var_b, .45, 2.) if beta_weighted else 1.
    spread = path[:, 0] - beta * path[:, 1]
    spread = spread[np.isfinite(spread)]
    center = np.median(spread)
    scale = max(1.4826 * np.median(np.abs(spread - center)), .005)
    z = (latest[0] - beta * latest[1] - center) / scale
    return z, beta, center, scale


def trade(state, row, account, orders):
    p, pairs, history, running, closed = state
    price, quote = (row.values[k][0] for k in ("close", "turnover"))
    qty = account.positions["qty"]
    valid = (row.status[0] == 0) & np.isfinite(price) & (price > 0)
    log_close = np.log(price, out=np.full_like(price, np.nan), where=valid)
    observations = None if len(history) < p["history"] else np.asarray(history)
    history.append(log_close)
    scores = np.full(len(price), np.nan)
    choices = []
    ready = set(account.ready)
    now = account.clock_ms
    for a, b in pairs:
        pair = (a, b)
        if pair in running:
            direction, beta, center, scale, opened, exiting = running[pair]
            if qty[a] or qty[b]:
                z = (log_close[a] - beta * log_close[b] - center) / scale
                scores[a], scores[b] = z, -z
                spread_move = direction * z
                if (exiting or not qty[a] or not qty[b] or spread_move >= -p["exit_z"]
                        or spread_move <= -p["stop_z"] or now - opened >= p["hold"] * HOUR):
                    running[pair] = (direction, beta, center, scale, opened, True)
                    for i in pair:
                        if qty[i] and valid[i]:
                            orders.close(row.symbols[i])
                continue
            del running[pair]
            closed[pair] = now
        if pair in closed and now - closed[pair] < p["cooldown"] * HOUR:
            continue
        if (observations is None or not all(valid[i] and row.symbols[i] in ready
                                          and quote[i] >= p["min_turnover"] for i in pair)):
            continue
        result = estimate(observations[:, pair], log_close[list(pair)], p["beta"], p["correlation"])
        if result is None:
            continue
        z, beta, center, scale = result
        scores[a], scores[b] = z, -z
        if p["entry_z"] <= abs(z) < p["stop_z"] and abs(z) * scale >= p["min_gap"]:
            choices.append((abs(z), pair, -np.sign(z), beta, center, scale))
    size = proportional_size(account, p["fraction"])
    margin = account.available_margin
    if margin is not None and size >= 30.:
        for _, pair, direction, beta, center, scale in sorted(choices, key=lambda x: -x[0]):
            if len(running) >= p["slots"] or margin < 1.02 * size:
                break
            a, b = pair
            buy, sell = (a, b) if direction > 0 else (b, a)
            weights = {a: size / (1 + beta), b: size * beta / (1 + beta)}
            orders.buy(row.symbols[buy], notional=weights[buy])
            orders.sell(row.symbols[sell], notional=weights[sell])
            running[pair] = (direction, beta, center, scale, now, False)
            margin -= 1.02 * size
    return {"pair_z": scores[None, :]}


STRATEGY = Strategy("economic_peer_spread_reversion", "1.0.0", {
    "history": Parameter("integer", 120, choices=(60, 120, 240)),
    "entry_z": Parameter("number", 2., 1., 4.),
    "exit_z": Parameter("number", .4, 0., .95),
    "stop_z": Parameter("number", 4.5, 4.1, 8.),
    "correlation": Parameter("number", .64, .3, .94),
    "beta": Parameter("boolean", True),
    "min_gap": Parameter("number", .014, .005, .14),
    "min_turnover": Parameter("number", 1_000_000., 100_000., 50_000_000.),
    "hold": Parameter("integer", 40, 6, 120),
    "cooldown": Parameter("integer", 24, 0, 120),
    "slots": Parameter("integer", 5, 2, 8),
    "fraction": Parameter("number", .135, .04, .24),
}, ("close", "quote_volume"), prepare, initialize, orders=trade)
