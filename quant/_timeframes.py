"""Share higher-frame SDK graphs and align only completed values to the base decision clock."""

from collections import deque
from dataclasses import replace

import numpy as np

from . import data, features
from .strategy import _inputs


class _AlignedRows:
    """Align an already computed higher-frame series only for requested base rows."""

    def __init__(self, values, indices, valid):
        self.values = values
        self.indices = indices
        self.valid = valid

    def __getitem__(self, rows):
        result = self.values[self.indices[rows]]
        result[~self.valid[rows]] = np.nan
        return result


class CompletedFeatures:
    def __init__(self, periods, step, memory_bytes, on_error):
        self.groups = {}
        for period, expressions in periods.items():
            if period <= 1:
                continue
            fields = {field for node in expressions.values() for field in _inputs(node)}
            if "vwap" in fields:
                fields.update(("quote_volume", "volume"))
            queue = deque()
            bars = data.resample(iter(queue.popleft, None), period * step)
            computed = features.evaluate(bars, expressions, memory_bytes=memory_bytes // len(periods), on_error=on_error)
            self.groups[period] = (expressions, fields, queue, computed, {"next": None})
        self.step = step

    def advance(self, block, *, lazy=False):
        result = {}
        valid = block.status <= data.EMPTY
        for period, (expressions, fields, queue, computed, held) in self.groups.items():
            size = period * self.step
            queue.append(replace(block, values={name: value for name, value in block.values.items() if name in fields}))
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
                indices = np.searchsorted(clocks, block.available_at_ms, side="right")
                for name in expressions:
                    prior = np.broadcast_to(held.get(name, np.nan), (1, len(block.symbols)))
                    values = np.concatenate([prior, *(piece.values[name] for piece in pieces)])
                    aligned = _AlignedRows(values, indices, valid)
                    result[name] = aligned if lazy else aligned[:]
                    held[name] = values[-1].copy()
            else:
                indices = np.zeros(len(block.timestamps_ms), dtype=np.intp)
                for name in expressions:
                    values = np.broadcast_to(held.get(name, np.nan), (1, len(block.symbols)))
                    aligned = _AlignedRows(values, indices, valid)
                    result[name] = aligned if lazy else aligned[:]
        return result
