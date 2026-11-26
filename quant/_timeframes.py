"""Share higher-frame SDK graphs and align only completed values to the base decision clock."""

from collections import deque

import numpy as np

from . import data, features


class CompletedFeatures:
    def __init__(self, periods, step, memory_bytes, on_error):
        self.groups = {}
        for period, expressions in periods.items():
            if period <= 1:
                continue
            queue = deque()
            bars = data.resample(iter(queue.popleft, None), period * step)
            computed = features.evaluate(bars, expressions, memory_bytes=memory_bytes // len(periods), on_error=on_error)
            self.groups[period] = (expressions, queue, computed, {"next": None})
        self.step = step

    def advance(self, block):
        result = {}
        for period, (expressions, queue, computed, held) in self.groups.items():
            size = period * self.step
            queue.append(block)
            if held["next"] is None:
                held["next"] = ((int(queue[0].timestamps_ms[0]) + size - 1) // size) * size
            head = held["next"]
            end = int(block.timestamps_ms[-1]) + self.step
            if end - head >= size:
                pieces = []
                while held["next"] + size <= end:
                    pieces.append(next(computed))
                    held["next"] = int(pieces[-1].timestamps_ms[-1]) + size
                clocks = np.concatenate([piece.available_at_ms for piece in pieces])
                indices = np.searchsorted(clocks, block.available_at_ms, side="right") - 1
                for name in expressions:
                    values = np.concatenate([piece.values[name] for piece in pieces])
                    aligned = np.where(indices[:, None] >= 0, values[np.maximum(indices, 0)], held.get(name, np.nan))
                    result[name] = np.where(block.status <= 1, aligned, np.nan)
                    held[name] = values[-1].copy()
            else:
                for name in expressions:
                    result[name] = np.where(block.status <= 1, held.get(name, np.nan), np.nan)
        return result
