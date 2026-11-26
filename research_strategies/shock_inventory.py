"""Accumulate into observable adverse moves and distribute into subsequent recovery."""

from dataclasses import dataclass

import numpy as np

import quant as q
from quant.strategy import Parameter, Plan, Plot, Strategy


VERSION = "1.0.0"
MECHANISMS = ("velocity", "residual", "flow", "residual_flow")


def prepare(p):
    e = q.features.expression
    close = "close"
    instant = e("quant.ts.pct_change", close, periods=12, units="fraction")
    move = e("quant.ts.pct_change", close, periods=60, units="fraction")
    graph = {
        "fast": instant, "move": move,
        "old_fast": e("quant.ts.lag", instant, periods=12, units="fraction"),
        "prior_vol": e("quant.ts.lag", e("quant.ts.rolling_std", instant, window=120,
                                          units="fraction"), periods=12, units="fraction"),
        "trend": e("quant.ts.pct_change", close, periods=360, units="fraction"),
        "fair": e("quant.ts.lag", e("quant.ts.ema", close, span=240, units="USDT/base"),
                  periods=12, units="USDT/base"),
    }
    if "flow" in p["mechanism"]:
        imbalance = e("quant.microstructure.quote_volume_imbalance", "buy_quote_volume",
                      "sell_quote_volume", units="fraction")
        graph["aggression"] = e("quant.ts.rolling_mean", imbalance, window=12, min_periods=6,
                                units="fraction")
        graph["volume_current"] = e("quant.ts.rolling_mean", "quote_volume", window=12, units="USDT")
        graph["volume_prior"] = e("quant.ts.lag", e("quant.ts.rolling_mean", "quote_volume",
                                  window=120, units="USDT"), periods=12, units="USDT")
    return Plan(graph, warmup_bars=361, decision_ticks=p["cadence"], plots=(
        Plot("fair", "Lagged fair value proxy", "USDT/base", pane="price"),
        Plot("shock_depth", "Signed shock depth", "fraction"),
        Plot("running_extreme", "Observed adverse extreme", "USDT/base", pane="price"),
        Plot("phase_code", "Inventory decision", "code", labels={
            "1": "Seed during shock", "2": "Add on another adverse step", "3": "Distribute on recovery",
            "4": "Close at fair value", "5": "Close on adverse stop", "6": "Close at holding limit",
            "7": "Close on failed recovery",
        }),
    ))


@dataclass
class Inventory:
    extreme: np.ndarray
    anchor: np.ndarray
    entered: np.ndarray
    added_at: np.ndarray
    distributed_at: np.ndarray
    had_position: np.ndarray
    cooldown_until: np.ndarray


def initialize(p, symbols, rng):
    n = len(symbols)
    return p, Inventory(np.full(n, np.nan), np.full(n, np.nan), np.zeros(n, dtype="i8"),
                        np.full(n, np.nan), np.full(n, np.nan), np.zeros(n, bool),
                        np.zeros(n, dtype="i8"))


def shock_depth(p, values, valid):
    side = p["side"]
    fast, move, trend = (values[key][0] for key in ("fast", "move", "trend"))
    depth = -side * move
    significant = depth >= np.maximum(p["depth"], p["sigma"] * values["prior_vol"][0] * np.sqrt(5))
    still_adverse = -side * fast >= p["depth"] * .20
    acceleration = -side * (fast - values["old_fast"][0])
    fair_gap = side * (values["fair"][0] / values["close"][0] - 1)
    if p["mechanism"] == "velocity":
        eligible = significant & still_adverse & (acceleration > 0) & (fair_gap > .0025)
    else:
        median = np.median(move[valid]) if np.count_nonzero(valid) >= 12 else np.nan
        market_ok = side * median > -p["market_limit"]
        residual = -side * (move - median)
        relative = residual >= np.maximum(p["depth"] * .7, values["prior_vol"][0] * np.sqrt(5) * p["sigma"])
        eligible = significant & still_adverse & (fair_gap >= p["depth"] * .5)
        if p["mechanism"] in ("residual", "residual_flow"):
            eligible &= relative & market_ok & (-side * trend < 3 * depth)
        if "flow" in p["mechanism"]:
            aggression = -side * values["aggression"][0]
            volume = values["volume_current"][0]
            normal = values["volume_prior"][0]
            eligible &= (aggression >= .16) & (normal > 0) & (volume >= 1.6 * normal)
            if p["mechanism"] == "residual_flow":
                # A burst of pressure has begun decelerating while price still falls.
                eligible &= (acceleration < 0) & (fair_gap > p["depth"] * .75)
    return depth, eligible & valid


def trade(state, row, account, orders):
    p, book = state
    values = row.values
    now = account.clock_ms
    side = p["side"]
    price = values["close"][0]
    qty = side * account.positions["qty"]
    held = qty > 0
    flat_after_close = book.had_position & ~held
    book.cooldown_until[flat_after_close] = now + 15 * 60_000
    book.had_position[:] = held
    book.extreme[~held] = np.nan
    book.distributed_at[~held] = np.nan
    valid = (row.status[0] == 0) & np.isfinite(price) & (price > 0)
    ready = set(account.ready)
    can_enter = valid & np.fromiter((s in ready for s in row.symbols), bool, len(row.symbols))
    depth, active_shock = shock_depth(p, values, can_enter)
    seed = active_shock & ~held & (now >= book.cooldown_until)
    better_extreme = valid & held & (side * price < side * book.extreme)
    book.extreme[better_extreme] = price[better_extreme]
    phase = np.full(price.shape, np.nan)
    margin = account.available_margin
    for i in np.flatnonzero(seed | (held & valid)):
        symbol = row.symbols[i]
        spot = price[i]
        size = p["order_usdt"]
        marked = qty[i] * spot
        is_held = held[i]
        if is_held:
            elapsed = now - book.entered[i]
            fair_now = values["fair"][0, i]
            # The frozen initial anchor prevents a moving average from declaring an instant recovery.
            target = side * min(side * book.anchor[i], side * fair_now) if np.isfinite(fair_now) else book.anchor[i]
            excursion = max(.001, side * (book.anchor[i] - book.extreme[i]) / book.anchor[i])
            retraced = side * (spot - book.extreme[i]) / book.extreme[i]
            acct_entry = account.positions["entry"][i]
            from_entry = side * (spot - acct_entry) / acct_entry
            distributed = np.isfinite(book.distributed_at[i])
            if elapsed >= p["hold_minutes"] * 60_000:
                orders.close(symbol)
                phase[i] = 6
                continue
            if side * (book.anchor[i] - spot) / book.anchor[i] > p["stop"] * p["depth"]:
                orders.close(symbol)
                phase[i] = 5
                continue
            if side * (target - spot) / spot <= .0007 and retraced >= .002:
                orders.close(symbol)
                phase[i] = 4
                continue
            if distributed and side * (book.distributed_at[i] - spot) / spot >= max(.003, .4 * excursion):
                orders.close(symbol)
                phase[i] = 7
                continue
            improved = not distributed or side * (spot - book.distributed_at[i]) / spot >= p["step"]
            recovery_floor = p["retrace"] * excursion + (.0005 if from_entry < 0 else 0)
            if improved and retraced >= max(.0018, recovery_floor):
                if marked <= 2 * size:
                    orders.close(symbol)
                elif side == 1:
                    orders.sell(symbol, notional=size)
                else:
                    orders.buy(symbol, notional=size)
                phase[i] = 3
                book.distributed_at[i] = spot
                continue
            deeper = side * (book.added_at[i] - spot) / spot >= p["step"]
            if distributed or not (better_extreme[i] and deeper and active_shock[i]):
                continue
        if p["dynamic"]:
            size *= min(2.5, max(1, depth[i] / p["depth"]))
        # Strategy inventory budget keeps simultaneous events from consuming the entire account.
        if marked + size > p["inventory"] or margin is None or margin < size * 1.01:
            continue
        if side == 1:
            orders.buy(symbol, notional=size)
        else:
            orders.sell(symbol, notional=size)
        margin -= size * 1.01
        book.added_at[i] = spot
        if not is_held:
            book.entered[i] = now
            book.anchor[i] = values["fair"][0, i]
            book.extreme[i] = spot
        phase[i] = 2 if is_held else 1
    return {"shock_depth": depth[None, :], "running_extreme": book.extreme[None, :],
            "phase_code": phase[None, :]}


STRATEGY = Strategy("shock_inventory_recovery", VERSION, {
    "mechanism": Parameter("string", "velocity", choices=MECHANISMS),
    "side": Parameter("integer", 1, choices=(-1, 1)),
    "cadence": Parameter("integer", 6, 1, 60),
    "order_usdt": Parameter("number", 10, 10, 30),
    "depth": Parameter("number", .009, .002, .10),
    "sigma": Parameter("number", 2.5, 1, 10),
    "step": Parameter("number", .0015, .0002, .05),
    "inventory": Parameter("number", 200, 10, 1000),
    "hold_minutes": Parameter("integer", 180, 15, 2880),
    "stop": Parameter("number", 3.5, 2, 10),
    "retrace": Parameter("number", .25, .05, .9),
    "market_limit": Parameter("number", .006, .001, .05),
    "dynamic": Parameter("boolean", False),
}, ("close", "quote_volume", "buy_quote_volume", "sell_quote_volume"), prepare,
    initialize, orders=trade)
