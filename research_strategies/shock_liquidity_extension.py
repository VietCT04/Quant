"""Retain part of a recovered shock position for sustained follow-through."""

from dataclasses import replace
from pathlib import Path

import numpy as np

from quant.strategy import Parameter, Plot
from quant.trading import Orders, exit_trigger

from . import shock_liquidity_allocated as allocated


def prepare(p):
    plan = allocated.prepare(p)
    return replace(plan, plots=(*plan.plots, Plot("extension_peak", "Rebound trailing reference", "USDT/base",
                                               pane="price")))


def initialize(p, symbols, rng):
    return allocated.initialize(p, symbols, rng), np.full(len(symbols), np.nan), np.zeros(len(symbols), dtype="i8")


def trade(state, row, account, orders):
    original, peak, started = state
    p, anchor = original[:2]
    spot = row.values["close"][0]
    quantity, entry = account.positions["qty"], account.positions["entry"]
    held = quantity > 0
    peak[~held] = np.nan
    valid = (row.status[0] == 0) & np.isfinite(spot) & (spot > 0)
    recovered = valid & held & np.isnan(peak) & (spot >= anchor) & (spot > entry)
    peak[recovered] = spot[recovered]
    started[recovered] = account.clock_ms
    extending = held & np.isfinite(peak)
    protected = {row.symbols[i] for i in np.flatnonzero(extending)}
    normal = Orders(row.symbols)
    trace = allocated.trade(original, row, account, normal)
    orders.items.extend(order for order in normal.items if order.symbol not in protected)
    for i in np.flatnonzero(extending & valid):
        symbol = row.symbols[i]
        peak[i] = max(peak[i], spot[i])
        expired = account.clock_ms - started[i] >= p["extension_hours"] * 3_600_000
        trail = exit_trigger(spot[i], entry[i], quantity[i], trailing_reference=peak[i],
                             trailing_stop=p["extension_trail"])
        if expired or trail:
            orders.close(symbol)
        elif recovered[i]:
            orders.reduce(symbol, fraction=p["release_fraction"])
    trace["extension_peak"] = peak[None, :].copy()
    if "phase_code" in trace:
        trace["phase_code"][0, extending] = np.nan
    return trace


STRATEGY = replace(
    allocated.STRATEGY, version="4.0.0", prepare=prepare, initialize=initialize, orders=trade,
    sources=allocated.STRATEGY.sources + (Path(allocated.__file__),),
    parameters=allocated.STRATEGY.parameters | {
        "side": Parameter("integer", 1, choices=(1,)),
        "extension_hours": Parameter("integer", 72, 12, 168),
        "extension_trail": Parameter("number", .035, .01, .12),
        "release_fraction": Parameter("number", .35, .10, .80),
    },
)
