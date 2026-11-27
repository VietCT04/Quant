"""Follow a confirmed relative repricing between previously correlated peers."""

from dataclasses import replace
from pathlib import Path

import numpy as np

from quant.strategy import Parameter
from quant.trading import proportional_size

from . import pair_dislocation as base


def trade(state, row, account, orders):
    p, pairs, history, running, closed = state
    price, turnover = (row.values[k][0] for k in ("close", "turnover"))
    qty = account.positions["qty"]
    valid = (row.status[0] == 0) & np.isfinite(price) & (price > 0)
    current = np.log(price, out=np.full_like(price, np.nan), where=valid)
    preceding = np.asarray(history) if len(history) == p["history"] else None
    history.append(current)
    scores = np.full(len(price), np.nan)
    ready = set(account.ready)
    now = account.clock_ms
    possible = []
    for pair in pairs:
        a, b = pair
        if pair in running:
            direction, beta, center, scale, entered, peak, started, closing = running[pair]
            if qty[a] or qty[b]:
                raw = (current[a] - beta * current[b] - center) / scale
                if np.isfinite(raw):
                    scores[a], scores[b] = raw, -raw
                    advance = direction * raw
                    peak = max(peak, advance)
                    closing |= advance <= max(entered - p["risk_z"], peak - p["trail_z"])
                closing |= not qty[a] or not qty[b] or now - started >= p["hold"] * base.HOUR
                running[pair] = direction, beta, center, scale, entered, peak, started, closing
                if closing:
                    for i in pair:
                        if valid[i] and qty[i]:
                            orders.close(row.symbols[i])
                continue
            del running[pair]
            closed[pair] = now
        if (preceding is None or now - closed.get(pair, -(1 << 60)) < p["cooldown"] * base.HOUR
                or not all(valid[i] and row.symbols[i] in ready and turnover[i] >= p["min_turnover"] for i in pair)):
            continue
        prior_path = preceding[:, pair]
        fitted = base.estimate(prior_path, current[list(pair)], p["beta"], p["correlation"])
        if fitted is None:
            continue
        z, beta, center, scale = fitted
        scores[a], scores[b] = z, -z
        previous = (prior_path[-1, 0] - beta * prior_path[-1, 1] - center) / scale
        direction = np.sign(z)
        acceleration = direction * (z - previous)
        if (p["entry_z"] <= abs(z) < p["entry_cap"] and abs(z) * scale >= p["min_gap"]
                and acceleration >= p["extension_z"] and direction * previous >= 0.):
            possible.append((acceleration, pair, direction, beta, center, scale, abs(z)))
    size = proportional_size(account, p["fraction"])
    margin = account.available_margin
    if margin is not None and size >= 30.:
        for _, pair, direction, beta, center, scale, entered in sorted(possible, key=lambda item: -item[0]):
            if len(running) >= p["slots"] or margin < 1.02 * size:
                break
            a, b = pair
            a_notional, b_notional = size / (1 + beta), size * beta / (1 + beta)
            if direction > 0:
                orders.buy(row.symbols[a], notional=a_notional)
                orders.sell(row.symbols[b], notional=b_notional)
            else:
                orders.buy(row.symbols[b], notional=b_notional)
                orders.sell(row.symbols[a], notional=a_notional)
            running[pair] = direction, beta, center, scale, entered, entered, now, False
            margin -= 1.02 * size
    return {"pair_z": scores[None, :]}


STRATEGY = replace(
    base.STRATEGY, id="economic_peer_spread_breakout", orders=trade, sources=(Path(base.__file__),),
    parameters={name: rule for name, rule in base.STRATEGY.parameters.items()
                if name not in {"exit_z", "stop_z"}} | {
        "entry_cap": Parameter("number", 5., 3., 12.),
        "extension_z": Parameter("number", .35, .01, 3.),
        "risk_z": Parameter("number", 1.0, .3, 4.),
        "trail_z": Parameter("number", 1.4, .4, 5.),
    },
)
