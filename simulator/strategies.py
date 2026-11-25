"""Deterministic reference strategies. Each block only uses trailing data."""
from __future__ import annotations

import numpy as np
import polars as pl

NAMES = ("buy_hold", "scheduled", "ema", "zscore", "random")


class Precomputed:
    """Signed USDT values indexed by bar start. NaN leaves an existing target alone."""
    native_target = None

    def __init__(self, starts, targets):
        self.starts = np.asarray(starts, dtype="i8")
        self.targets = np.asarray(targets)
        if (self.starts.ndim != 1 or self.targets.ndim != 2 or len(self.targets) != len(self.starts)
                or np.any(np.diff(self.starts) <= 0)):
            raise ValueError("Precomputed signals must have unique ascending starts and a [time, symbol] matrix")

    def apply_at(self, starts, values):
        if self.targets.shape[1] != values.shape[1]:
            raise ValueError("Precomputed target columns must follow the feed's symbol order")
        lo, hi = np.searchsorted(self.starts, [starts[0], starts[-1]], side="left")
        hi += hi < len(self.starts) and self.starts[hi] == starts[-1]
        index = np.searchsorted(starts, self.starts[lo:hi])
        if not np.array_equal(starts[index], self.starts[lo:hi]):
            raise ValueError("Signal timestamps do not align to market bars")
        values[index, :, 4] = self.targets[lo:hi]


class Signals:
    def __init__(self, name, symbols, notional, stride, seed):
        if name not in NAMES or notional < 0:
            raise ValueError("Unknown strategy or negative notional")
        self.name, self.budget, self.stride = name, notional, stride
        self.generator = np.random.default_rng(seed)
        self.n = len(symbols)
        self.native_target = np.full(self.n, notional) if name == "buy_hold" else None
        self.tick = 0
        self.fast = np.full(self.n, np.nan)
        self.slow = self.fast.copy()
        self.history = [np.empty(0) for _ in symbols]

    def apply(self, values, flags):
        rows, n, _ = values.shape
        due = np.arange((-self.tick) % self.stride, rows, self.stride)
        target = values[:, :, 4]
        if self.name == "scheduled":
            cycle = ((self.tick + due) // self.stride) % 4
            target[due, :] = self.budget * np.array([1.0, 0.5, -1.0, 0.0])[cycle, None]
        elif self.name == "random":
            target[due] = self.generator.integers(-1, 2, (len(due), n)) * self.budget
        elif self.name in ("ema", "zscore"):
            for i in range(n):
                close = np.where(flags[:, i] <= 1, values[:, i, 0], np.nan)
                if self.name == "ema":
                    lines = []
                    for state, span in ((self.fast, 60), (self.slow, 240)):
                        x = pl.Series(np.r_[state[i], close]).fill_nan(None)
                        line = x.ewm_mean(span=span, adjust=False, ignore_nulls=True).forward_fill().to_numpy()[1:]
                        state[i] = line[-1]
                        lines.append(line)
                    a, b = lines
                    target[due, i] = np.where(np.isfinite(a[due]) & np.isfinite(b[due]),
                                              np.sign(a[due] - b[due]) * self.budget, np.nan)
                else:
                    previous = self.history[i]
                    series = pl.Series(np.r_[previous, close]).fill_nan(None)
                    mean = series.rolling_mean(240).to_numpy()[len(previous):]
                    deviation = series.rolling_std(240).to_numpy()[len(previous):]
                    z = np.divide(close - mean, deviation, out=np.full(rows, np.nan), where=deviation > 0)
                    target[due, i] = np.where(z[due] < -2, self.budget,
                        np.where(z[due] > 2, -self.budget, np.where(np.isfinite(z[due]), 0, np.nan)))
                    self.history[i] = np.r_[previous, close][-239:]
        self.tick += rows
