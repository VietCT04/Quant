"""Patient inventory variants motivated by the observed short-hold churn."""

from dataclasses import replace
from pathlib import Path

import numpy as np

import quant as q
from quant.strategy import Parameter, Plot

from . import shock_inventory as base


def prepare(p):
    plan = base.prepare(p)
    e = q.features.expression
    bound = "max" if p["side"] == 1 else "min"
    extreme = e(f"quant.ts.rolling_{bound}", "close", window=180, units="USDT/base")
    graph = plan.features | {"prior_range_edge": e("quant.ts.lag", extreme, periods=12, units="USDT/base")}
    return replace(plan, features=graph, plots=(*plan.plots,
        Plot("prior_range_edge", "Lagged range reference", "USDT/base", pane="price")))


def initialize(p, symbols, rng):
    p, book = base.initialize(p, symbols, rng)
    n = len(symbols)
    return p, book, np.zeros(n), np.zeros(n, dtype="u1"), np.full(n, np.nan)


def trade(state, row, account, orders):
    p, book, previous_qty, pending, signal_price = state
    now = account.clock_ms
    v = row.values
    side = p["side"]
    price = v["close"][0]
    qty = side * account.positions["qty"]
    held = qty > 0
    incremental_entry = (pending == 2) & (qty > previous_qty)
    reduction = (pending == 3) & (qty < previous_qty)
    book.added_at[incremental_entry] = signal_price[incremental_entry]
    book.distributed_at[reduction] = signal_price[reduction]
    previous_qty[:] = qty
    pending[:] = 0

    book.cooldown_until[book.had_position & ~held] = now + 30 * 60_000
    book.had_position[:] = held
    book.extreme[~held] = np.nan
    book.distributed_at[~held] = np.nan
    valid = (row.status[0] == 0) & np.isfinite(price) & (price > 0)
    ready = set(account.ready)
    tradable = valid & np.fromiter((s in ready for s in row.symbols), bool, len(row.symbols))
    depth, shock = base.shock_depth(p, v, tradable)
    edge = side * (v["prior_range_edge"][0] - price) / price
    gap = side * (v["fair"][0] - price) / price
    shock &= (edge >= p["depth"]) & (gap >= p["minimum_gap"])
    seed = shock & ~held & (now >= book.cooldown_until)
    extreme = held & valid & (side * price < side * book.extreme)
    book.extreme[extreme] = price[extreme]
    phase = np.full(price.shape, np.nan)
    margin = account.available_margin

    for i in np.flatnonzero(seed | (held & valid)):
        symbol, spot = row.symbols[i], price[i]
        if held[i]:
            anchor, low = book.anchor[i], book.extreme[i]
            elapsed = (now - book.entered[i]) / 60_000
            # Use the frozen entry reference for expiry and fair-value decisions.
            width = max(p["minimum_gap"], side * (anchor - low) / low)
            bounce = side * (spot - low) / low
            remaining = side * (anchor - spot) / spot
            trimmed = np.isfinite(book.distributed_at[i])
            reason = 0
            if remaining > p["stop"] * p["depth"]:
                reason = 5
            elif elapsed >= p["hold_minutes"]:
                reason = 6
            elif remaining <= .001 and bounce >= p["minimum_gap"] * .6:
                reason = 4
            elif trimmed and side * (book.distributed_at[i] - spot) / spot >= max(.003, width * .40):
                reason = 7
            if reason:
                orders.close(symbol)
                phase[i] = reason
                continue

            from_entry = side * (spot - account.positions["entry"][i]) / account.positions["entry"][i]
            enough = bounce >= max(.0035, p["retrace"] * width) + (.0005 if from_entry < 0 else 0)
            more = not trimmed or side * (spot - book.distributed_at[i]) / spot >= p["step"]
            if elapsed >= p["patient_minutes"] and enough and more:
                portion = min(1., p["order_usdt"] / (qty[i] * spot))
                orders.reduce(symbol, fraction=portion)
                pending[i], signal_price[i], phase[i] = 3, spot, 3
                continue

            severity = max(1., min(2.5, depth[i] / p["depth"]))
            distance = side * (book.added_at[i] - spot) / spot
            # Deepening adverse moves eventually weaken the temporary-shock hypothesis.
            # Stop adding before the stop-out band implicated by the first replay.
            if trimmed or remaining >= 1.9 * p["depth"] or not (
                shock[i] and extreme[i] and distance >= p["step"] / severity and elapsed <= p["add_minutes"]
            ):
                continue

        size = p["order_usdt"]
        if p["dynamic"]:
            size *= min(2., max(1., depth[i] / p["depth"]))
        if qty[i] * spot + size > p["inventory"] or margin is None or margin < size * 1.01:
            continue
        if side == 1:
            orders.buy(symbol, notional=size)
        else:
            orders.sell(symbol, notional=size)
        margin -= size * 1.01
        if held[i]:
            pending[i], signal_price[i], phase[i] = 2, spot, 2
        else:
            book.entered[i], book.anchor[i], book.extreme[i], book.added_at[i] = now, v["fair"][0, i], spot, spot
            phase[i] = 1

    return {"shock_depth": depth[None, :], "running_extreme": book.extreme[None, :],
            "phase_code": phase[None, :]}


STRATEGY = replace(base.STRATEGY, version="2.0.0", prepare=prepare, initialize=initialize, orders=trade,
                   sources=(Path(__file__).with_name("shock_inventory.py"),),
                   parameters=base.STRATEGY.parameters | {
                       "patient_minutes": Parameter("integer", 8, 1, 30),
                       "add_minutes": Parameter("integer", 45, 5, 180),
                       "minimum_gap": Parameter("number", .007, .003, .04),
                   })
