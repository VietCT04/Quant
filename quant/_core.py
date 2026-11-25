"""Internal rules shared by numerical modules."""

import re

import numpy as np


class MissingCapabilityError(ValueError):
    """A requested computation needs a field or capability that is unavailable."""

    def __init__(self, capability, required=()):
        self.capability, self.required = capability, tuple(required)
        super().__init__(
            f"{capability} requires {', '.join(self.required) or 'an SDK implementation'}"
        )


def array(x):
    """Convert operands, forbidding implicit removal of a research-only label."""
    if getattr(x, "research_only", False):
        raise TypeError("Research-only values require an explicit research operation")
    if np.iscomplexobj(x):
        raise TypeError("Real-valued operands are required")
    return np.asarray(x, dtype=np.float64)


def divide(a, b):
    """Divide aligned or broadcastable real operands; invalid denominators yield NaN."""
    a, b = np.broadcast_arrays(array(a), array(b))
    with np.errstate(over="ignore", under="ignore"):
        result = np.divide(
            a,
            b,
            out=np.full(a.shape, np.nan),
            where=np.isfinite(a) & np.isfinite(b) & (b != 0),
        )
    return np.where(np.isfinite(result), result, np.nan)


def duration(value):
    match = re.fullmatch(r"([1-9][0-9]*)(ms|s|m|h|d)", value)
    if not match:
        raise ValueError(f"Invalid duration: {value!r}")
    count, unit = match.groups()
    return (
        int(count) * {"ms": 1, "s": 1000, "m": 60000, "h": 3600000, "d": 86400000}[unit]
    )


def bars(window, interval_ms=None):
    if isinstance(window, str):
        size = duration(window)
        if (
            not isinstance(interval_ms, (int, np.integer))
            or interval_ms <= 0
            or size % interval_ms
        ):
            raise ValueError(
                "Duration windows require an exact positive sampling interval"
            )
        return size // interval_ms
    if (
        isinstance(window, (bool, np.bool_))
        or not isinstance(window, (int, np.integer))
        or window < 1
    ):
        raise ValueError("Window must be a positive bar count or duration string")
    return int(window)


def time_array(x):
    x = array(x)
    if x.ndim not in (1, 2):
        raise ValueError("Expected [time] or [time, symbol]")
    return x


def paired(a, b):
    a, b = time_array(a), time_array(b)
    if a.shape != b.shape:
        raise ValueError("Operands must share the same time and symbol axes")
    return a, b
