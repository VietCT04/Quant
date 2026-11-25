"""Causal score transformations and explicitly stateful event rules."""

import numpy as np

from . import cs, math as qm, ts
from ._core import array, bars, paired, time_array

sign, clip = qm.sign, qm.clip
normalize, rank = cs.gross_normalize, cs.rank
smooth, decay = ts.ema, ts.exponential_decay


def threshold(score, level=0.0):
    """Return signed direction outside a symmetric threshold; preserve missing scores."""
    if level < 0:
        raise ValueError("Threshold must be nonnegative")
    x = time_array(score)
    return np.where(np.isfinite(x), np.where(np.abs(x) > level, np.sign(x), 0), np.nan)


def crossover(fast, slow):
    """Signed crossing events: +1 up, -1 down, 0 without a crossing."""
    return ts.crossing_above(fast, slow).astype(int) - ts.crossing_below(
        fast, slow
    ).astype(int)


def deadband(target, previous, *, width):
    """Keep the previous target when the proposed change is within width."""
    target, previous = paired(target, previous)
    if width < 0:
        raise ValueError("Deadband must be nonnegative")
    return np.where(np.abs(target - previous) <= width, previous, target)


def conditional_activate(score, condition, *, inactive=0.0):
    """Use the score only where a boolean condition is true, preserving missing scores."""
    x = time_array(score)
    condition = ts._events(condition)
    if condition.shape != x.shape:
        raise ValueError("Condition must share score axes")
    return np.where(np.isfinite(x), np.where(condition, x, inactive), np.nan)


regime_filter = conditional_activate


def persistence(direction):
    """Length of the current nonzero directional run, with signed output."""
    x = time_array(direction)
    return ts.consecutive_true_count(x > 0) - ts.consecutive_true_count(x < 0)


def confirmation(direction, window, *, interval_ms=None):
    """Emit a direction after window consecutive same-direction observations."""
    x = time_array(direction)
    return np.where(
        np.isfinite(x),
        np.where(np.abs(persistence(x)) >= bars(window, interval_ms), np.sign(x), 0),
        np.nan,
    )


def cooldown(event, window, *, interval_ms=None):
    """Allow the first event and suppress events for the next window scheduled bars."""
    n = bars(window, interval_ms)
    event = ts._events(event)

    def apply(a):
        result = np.zeros(len(a), dtype=bool)
        next_ = 0
        for idx in np.flatnonzero(a):
            if idx >= next_:
                result[idx] = True
                next_ = idx + n + 1
        return result

    return ts._map(event, apply).astype(bool)


def hysteresis(score, *, entry, exit=0.0):
    """Enter at ±entry; release to zero at ±exit; unknown scores clear state."""
    if not 0 <= exit < entry:
        raise ValueError("Require 0 <= exit < entry")

    def apply(a):
        out = np.full(len(a), np.nan)
        state = 0
        for i, x in enumerate(a):
            if not np.isfinite(x):
                state = 0
                continue
            if abs(x) >= entry:
                state = int(np.sign(x))
            elif state * x <= exit:
                state = 0
            out[i] = state
        return out

    return ts._map(score, apply)


def entry_exit(entry, exit):
    """Create a flat/long state from boolean events; an exit has priority on ties."""
    entry, exit = ts._events(entry), ts._events(exit)
    if entry.shape != exit.shape:
        raise ValueError("Events must align")
    return ts.time_since_event(entry) < np.where(
        np.isfinite(ts.time_since_event(exit)), ts.time_since_event(exit), np.inf
    )


def time_exit(entry, window, *, interval_ms=None):
    """Emit exit on the window-th scheduled bar since the most recent entry."""
    return ts.time_since_event(entry) == bars(window, interval_ms)


def trailing_stop(price, entry, *, fraction, side=1):
    """Emit one stop per entry using observed trailing peaks for long positions or lows for shorts."""
    price = time_array(price)
    entry = ts._events(entry)
    if entry.shape != price.shape or not 0 < fraction < 1 or side not in {-1, 1}:
        raise ValueError("Invalid entry axes, stop distance or side")

    def apply(x, events):
        extreme = np.nan
        result = np.zeros(len(x), dtype=bool)
        for i, (value, event) in enumerate(zip(x, events)):
            if not np.isfinite(value) or value <= 0:
                continue
            if event:
                extreme = value
            elif np.isfinite(extreme):
                extreme = max(extreme, value) if side == 1 else min(extreme, value)
                if side * (value / extreme - 1) <= -fraction:
                    result[i] = True
                    extreme = np.nan
        return result

    return (
        apply(price, entry)
        if price.ndim == 1
        else np.column_stack([apply(a, b) for a, b in zip(price.T, entry.T)])
    )


def combine(scores, weights=None):
    """Linear combination of aligned scores using explicit coefficients."""
    scores = [time_array(s) for s in scores]
    if not scores or any(a.shape != scores[0].shape for a in scores):
        raise ValueError("Provide aligned score arrays")
    weights = np.ones(len(scores)) if weights is None else array(weights)
    if weights.shape != (len(scores),) or not np.isfinite(weights).all():
        raise ValueError("Supply one finite coefficient per score")
    return np.tensordot(weights, np.stack(scores), axes=1)


__all__ = """sign clip normalize rank smooth decay threshold crossover deadband conditional_activate
regime_filter persistence confirmation cooldown hysteresis entry_exit time_exit trailing_stop combine""".split()
