"""Thin C ABI bindings with no per-bar Python calls."""
from __future__ import annotations

from concurrent.futures import wait

import ctypes as ct
import hashlib
import math
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
from threading import Lock
from dataclasses import dataclass, fields

import numpy as np

_CONFIG_FLOATS = """initial_wallet fee_bps spread_bps slippage_bps impact_bps participation leverage maintenance_rate
                    max_gross_leverage max_symbol max_gross max_adjustment min_adjustment drawdown_limit""".split()
_CONFIG_INTS = "interval lag latency rebalance_ticks max_positions snapshot_ticks trade_mark_fallback logging".split()


@dataclass(frozen=True)
class Settings:
    initial_wallet: float = 100_000
    fee_bps: float = 5
    spread_bps: float = 2
    slippage_bps: float = 1
    impact_bps: float = 0
    participation: float = .05
    leverage: float = 2
    maintenance_rate: float = .005
    max_gross_leverage: float = 1.5
    max_symbol: float = 20_000
    max_gross: float = 150_000
    max_adjustment: float = 20_000
    min_adjustment: float = 50
    drawdown_limit: float = .35
    interval: int = 5000
    lag: int = 100
    latency: int = 50
    rebalance_ticks: int = 720
    max_positions: int = 50
    snapshot_ticks: int = 720
    trade_mark_fallback: int = 1
    logging: int = 1

    def __post_init__(self):
        for f in fields(self):
            value = getattr(self, f.name)
            if not math.isfinite(value) or value < 0:
                raise ValueError(f"Invalid setting {f.name}")
        for name in _CONFIG_INTS:
            if not isinstance(getattr(self, name), int):
                raise ValueError(f"{name} must be an integer")
        if (not 0 <= self.participation <= 1 or not 0 <= self.drawdown_limit <= 1 or
            not self.initial_wallet or self.leverage < 1 or not self.interval or
            not self.rebalance_ticks or not self.snapshot_ticks or self.logging not in (0, 1, 2) or self.fee_bps >= 10000 or
            self.spread_bps / 2 + self.slippage_bps + self.impact_bps >= 10000):
            raise ValueError("Invalid interval, leverage, participation, costs or drawdown settings")


@dataclass(frozen=True)
class OrderSettings(Settings):
    """Versioned next-open research model. Legacy Settings preserves target execution."""
    initial_wallet: float = 10_000
    leverage: float = 1
    participation: float = 0
    max_gross_leverage: float = 0
    max_symbol: float = 0
    max_gross: float = 0
    max_adjustment: float = 0
    min_adjustment: float = 10
    drawdown_limit: float = 0
    lag: int = 0
    latency: int = 0
    rebalance_ticks: int = 1  # Compatibility field. Order scheduling belongs to the strategy.
    max_positions: int = 0
    quantity_step: float = 0.000001

    def __post_init__(self):
        super().__post_init__()
        if self.lag or self.latency % self.interval or not 1e-10 <= self.quantity_step <= 1:
            raise ValueError("Orders require the completed-bar clock, interval-aligned latency and a valid quantity step")


class _Config(ct.Structure):
    _fields_ = [(f, ct.c_double) for f in _CONFIG_FLOATS] + [(f, ct.c_int64) for f in _CONFIG_INTS]


class _ShockConfig(ct.Structure):
    _floats = ("depth", "sigma", "step", "hold_minutes", "order_usdt", "inventory", "stop",
               "retrace", "market_limit", "add_minutes", "patient_minutes", "minimum_gap")
    _fields_ = ([(name, ct.c_double) for name in _floats] +
                [(name, ct.c_int64) for name in ("side", "mechanism", "dynamic", "patient")])


def dtype(ints, floats):
    return np.dtype([(x, "=i8") for x in ints.split()] + [(x, "=f8") for x in floats.split()])


SETTLEMENT = dtype("ms symbol", "rate price")
ORDER = dtype("ms bar_start decision symbol reason",
              "target requested filled reference price fee slippage realized closed closed_net hold_ms after entry")
ORDER_V2 = np.dtype(ORDER.descr + dtype("id kind", "requested_notional rounded buying_power intent_size").descr)
REQUEST = dtype("due id symbol kind decision", "size")
CASHFLOW = dtype("ms symbol", "qty mark rate payment")
TARGET = dtype("ms symbol", "notional")
EQUITY = dtype("ms uncertain_positions maintenance_breach",
               "wallet realized unrealized equity gross net initial maintenance available fees slippage funding turnover risk_drawdown")
POSITION = dtype(
    "", "qty entry mark target realized unrealized fees slippage funding turnover long_realized short_realized "
        "long_cost short_cost long_funding short_funding long_fee short_fee")
POSITION = np.dtype(POSITION.descr + dtype("gap_ticks held_unmarked_ticks fallback_ticks orders fills", "").descr)
RISK = dtype("maintenance_ticks uncertain_ticks suspended_ticks", "peak_gross")

_LIB = None
_LOAD_LOCK = Lock()


def build():
    source = Path(__file__).with_name("core.cpp").resolve()
    override = os.environ.get("QUANT_SIM_LIBRARY")
    if override:
        return Path(override).resolve()
    folder = source.parents[1] / ".sim-build"
    folder.mkdir(exist_ok=True)
    compiler = os.environ.get("CXX")
    cmd = shlex.split(compiler) if compiler else (
        [sys.executable, "-m", "ziglang", "c++"] if __import__("importlib.util").util.find_spec("ziglang")
        else [shutil.which("clang++") or shutil.which("g++") or "c++"])
    digest = hashlib.sha256(source.read_bytes() + source.with_name("order_program.hpp").read_bytes()
                             + source.with_name("shock_features.hpp").read_bytes() + repr(cmd).encode()).hexdigest()[:16]
    suffix = ".dll" if os.name == "nt" else ".so"
    lib = folder / f"quant-{digest}{suffix}"
    if not lib.exists():
        staged = folder / f"quant-{digest}-{os.getpid()}.part{suffix}"
        env = os.environ | {"ZIG_GLOBAL_CACHE_DIR": str(folder / "zig-global"),
                            "ZIG_LOCAL_CACHE_DIR": str(folder / "zig-local")}
        flags = ["-std=c++20", "-O3", "-DNDEBUG", "-ffp-contract=off", "-shared"]
        if os.name != "nt":
            flags.append("-fPIC")
        subprocess.run([*cmd, *flags, str(source), "-o", str(staged)], check=True, env=env)
        os.replace(staged, lib)
    return lib


def library():
    with _LOAD_LOCK:
        return _library()


def _library():
    global _LIB
    if _LIB is None:
        lib = ct.CDLL(str(build()))
        ptr, size = ct.c_void_p, ct.c_size_t
        lib.sim_create.argtypes, lib.sim_create.restype = [ct.POINTER(_Config), ptr, size, ptr], ptr
        lib.sim_delete.argtypes, lib.sim_delete.restype = [ptr], None
        lib.sim_run.argtypes, lib.sim_run.restype = [ptr, ptr, ptr, ptr, size, ptr, size], ct.c_int
        if hasattr(lib, "sim_run_shared"):
            lib.sim_run_shared.argtypes = [ptr, ptr, ptr, ptr, size, ptr, size, ptr]
            lib.sim_run_shared.restype = ct.c_int
            lib.sim_risk.argtypes, lib.sim_risk.restype = [ptr, ptr], None
        if hasattr(lib, "sim_run_orders"):
            lib.sim_orders_config.argtypes, lib.sim_orders_config.restype = [ptr, ct.c_double], None
            lib.sim_run_orders.argtypes = [ptr, ptr, ptr, ptr, ptr, size, ptr, size, ptr, size]
            lib.sim_run_orders.restype = ct.c_int
            lib.sim_terminal.argtypes, lib.sim_terminal.restype = [ptr, ct.c_int64, ptr, size], ct.c_int
        if hasattr(lib, "sim_run_program"):
            lib.sim_configure_shock.argtypes = [ptr, ct.POINTER(_ShockConfig)]
            lib.sim_configure_shock.restype = ct.c_int
            lib.sim_run_program.argtypes = [ptr, ptr, ptr, ptr, ptr, size, ptr, size, ptr, ptr, ct.c_int64, ct.c_int64]
            lib.sim_run_program.restype = ct.c_int
        if hasattr(lib, "sim_shock_features_run_shard"):
            lib.sim_shock_features_create.argtypes = [size, ct.c_int, ct.c_int]
            lib.sim_shock_features_create.restype = ptr
            lib.sim_shock_features_delete.argtypes = [ptr]
            lib.sim_shock_features_delete.restype = None
            lib.sim_shock_features_run_shard.argtypes = [
                ptr, ptr, ptr, ptr, ptr, ptr, size, ptr, ptr, size, size, size, size,
            ]
            lib.sim_shock_features_run_shard.restype = ct.c_int
        for f in ("orders", "cashflows", "targets", "curve"):
            func = getattr(lib, "sim_" + f)
            func.argtypes, func.restype = [ptr, ct.POINTER(size)], ptr
        for f in ("equity", "positions"):
            func = getattr(lib, "sim_" + f)
            func.argtypes, func.restype = [ptr, ptr], None
        _LIB = lib
    return _LIB


class Engine:
    """Stateful native simulator. Inputs are contiguous time-major NumPy blocks."""

    def __init__(self, settings, rules, native_target=None):
        self.lib = library()
        self.settings = settings
        rules = np.ascontiguousarray(rules, dtype="=f8")
        if rules.ndim != 2 or rules.shape[1] != 2 or not len(rules) or not np.isfinite(rules).all() or (rules < 0).any():
            raise ValueError("rules must be a nonnegative [symbol, 2] array")
        self.n = len(rules)
        targets = None if native_target is None else np.ascontiguousarray(native_target, dtype="=f8")
        if targets is not None and targets.shape != (self.n,):
            raise ValueError("One native allocation per symbol is required")
        conf = _Config(*(getattr(settings, f) for f in _CONFIG_FLOATS + _CONFIG_INTS))
        self.handle = self.lib.sim_create(ct.byref(conf), rules.ctypes.data, self.n,
                                          None if targets is None else targets.ctypes.data)
        if not self.handle:
            raise MemoryError("Native simulator creation failed")
        self.last_time = None
        if isinstance(settings, OrderSettings):
            if not hasattr(self.lib, "sim_run_orders") or targets is not None:
                self.close()
                raise ValueError("Explicit orders require the V2 native library and no static targets")
            self.lib.sim_orders_config(self.handle, settings.quantity_step)

    def close(self):
        if getattr(self, "handle", None):
            self.lib.sim_delete(self.handle)
            self.handle = None

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()

    def __del__(self):
        self.close()

    def run(self, starts, values, status, settlements=None):
        self._run(starts, values, status, settlements, shared=False)

    def run_shared(self, starts, values, status, settlements=None, *, targets=None):
        """Advance one portfolio with immutable four-column market data and separate targets."""
        if not hasattr(self.lib, "sim_run_shared"):
            raise RuntimeError("The shared simulator requires the current native library")
        self._run(starts, values, status, settlements, shared=True, targets=targets)

    def _run(self, starts, values, status, settlements, *, shared, targets=None):
        if not self.handle:
            raise RuntimeError("Simulator is closed")
        starts = np.ascontiguousarray(starts, dtype="=i8")
        values = np.ascontiguousarray(values, dtype="=f8")
        status = np.ascontiguousarray(status, dtype="u1")
        if starts.ndim != 1 or not len(starts) or values.shape != (len(starts), self.n, 4 if shared else 5) or status.shape != values.shape[:2]:
            raise ValueError("mismatched input block shapes")
        if np.any(np.diff(starts) <= 0) or self.last_time is not None and starts[0] <= self.last_time:
            raise ValueError("Nonchronological bar stream")
        done = starts[-1] + self.settings.interval + self.settings.lag
        funding = np.ascontiguousarray(settlements if settlements is not None else [], dtype=SETTLEMENT)
        if (funding.ndim != 1 or np.any(np.diff(funding["ms"]) < 0)
                or len(funding) and (funding["ms"][-1] > done or self.last_time is not None and
                    funding["ms"][0] <= self.last_time + self.settings.interval + self.settings.lag)
                or np.any((funding["symbol"] < 0) | (funding["symbol"] >= self.n))
                or not np.isfinite(funding["rate"]).all() or not np.isfinite(funding["price"]).all()
                or np.any(funding["price"] <= 0)):
            raise ValueError("Invalid, repeated, unsorted, or future settlement events")
        args = (self.handle, starts.ctypes.data, values.ctypes.data, status.ctypes.data,
                len(starts), funding.ctypes.data, len(funding))
        if shared:
            if targets is not None:
                targets = np.ascontiguousarray(targets, dtype="=f8")
                if targets.shape != status.shape:
                    raise ValueError("Target axes must match the shared input")
            rc = self.lib.sim_run_shared(*args, None if targets is None else targets.ctypes.data)
        else:
            rc = self.lib.sim_run(*args)
        if rc:
            self.close()
            raise RuntimeError("Native simulator rejected the block")
        self.last_time = starts[-1]

    def run_orders(self, starts, values, status, opens, settlements, requests):
        if not isinstance(self.settings, OrderSettings) or not self.handle:
            raise ValueError("An open order-mode engine is required")
        starts = np.ascontiguousarray(starts, dtype="=i8")
        values = np.ascontiguousarray(values, dtype="=f8")
        status = np.ascontiguousarray(status, dtype="u1")
        opens = np.ascontiguousarray(opens, dtype="=f8")
        funding = np.ascontiguousarray(settlements, dtype=SETTLEMENT)
        requests = np.ascontiguousarray(list(requests), dtype=REQUEST)
        if (not len(starts) or values.shape != (len(starts), self.n, 4)
                or status.shape != values.shape[:2] or opens.shape != status.shape):
            raise ValueError("Mismatched order-mode input axes")
        step = self.settings.interval
        if (np.any(np.diff(starts) != step) or np.any(starts % step)
                or self.last_time is not None and starts[0] != self.last_time + step):
            raise ValueError("Orders require contiguous scheduled bars")
        end = starts[-1] + step
        if (funding.ndim != 1 or np.any(np.diff(funding["ms"]) < 0)
                or np.any(funding["ms"] >= end)
                or self.last_time is not None and np.any(funding["ms"] < starts[0])
                or np.any((funding["symbol"] < 0) | (funding["symbol"] >= self.n))
                or not np.isfinite(funding["rate"]).all() or not np.isfinite(funding["price"]).all()
                or np.any(funding["price"] <= 0)):
            raise ValueError("Invalid order-mode funding events")
        due = requests["due"]
        if (np.any(np.diff(due) < 0) or np.any((due < starts[0]) | (due >= end) | (due % step != 0))
                or np.any(requests["decision"] + self.settings.latency != due)
                or np.any((requests["symbol"] < 0) | (requests["symbol"] >= self.n))
                or np.any((requests["kind"] < 0) | (requests["kind"] > 3))):
            raise ValueError("Orders must be sorted and scheduled on a valid bar open")
        rc = self.lib.sim_run_orders(self.handle, starts.ctypes.data, values.ctypes.data, status.ctypes.data,
                                     opens.ctypes.data, len(starts), funding.ctypes.data, len(funding),
                                     requests.ctypes.data, len(requests))
        if rc:
            self.close()
            raise RuntimeError("Native simulator rejected the order block")
        self.last_time = starts[-1]

    def configure_shock(self, parameters, *, patient):
        mechanism = ("velocity", "residual", "flow", "residual_flow").index(parameters["mechanism"])
        config = _ShockConfig(*(parameters.get(name, 0) for name in _ShockConfig._floats),
                              parameters["side"], mechanism, parameters["dynamic"], patient)
        if self.lib.sim_configure_shock(self.handle, ct.byref(config)):
            raise RuntimeError("The native program rejected its configuration")

    def _run_program(self, starts, values, status, opens, funding, columns, readiness, start, cadence):
        if (not self.handle or len(columns) != 11 or len(starts) == 0 or
                status.shape != (len(starts), self.n) or readiness.shape != status.shape):
            raise ValueError("Compiled order axes must match the native block")
        opens = np.ascontiguousarray(opens, dtype="=f8")
        readiness = np.ascontiguousarray(readiness).view("u1")
        columns = [np.ascontiguousarray(x, dtype="=f8") if x is not None else None for x in columns]
        pointers = (ct.c_void_p * len(columns))(*(x.ctypes.data if x is not None else None for x in columns))
        rc = self.lib.sim_run_program(
            self.handle, starts.ctypes.data, values.ctypes.data, status.ctypes.data, opens.ctypes.data,
            len(starts), funding.ctypes.data, len(funding), pointers, readiness.ctypes.data, start, cadence,
        )
        if rc:
            self.close()
            raise RuntimeError("Native strategy rejected the block")
        self.last_time = starts[-1]

    def terminal(self, end, requests=()):
        requests = np.ascontiguousarray(list(requests), dtype=REQUEST)
        if (not self.handle or not isinstance(self.settings, OrderSettings) or self.last_time is None
                or end != self.last_time + self.settings.interval
                or np.any((requests["symbol"] < 0) | (requests["symbol"] >= self.n))):
            raise ValueError("Invalid terminal order-mode call")
        if self.lib.sim_terminal(self.handle, end, requests.ctypes.data, len(requests)):
            self.close()
            raise RuntimeError("Native simulator rejected the terminal event")

    def _batch(self, name, dtype_):
        if not self.handle:
            raise RuntimeError("Simulator is closed")
        size = ct.c_size_t()
        ptr = getattr(self.lib, "sim_" + name)(self.handle, ct.byref(size))
        if not size.value:
            return np.empty(0, dtype=dtype_)
        return np.frombuffer(ct.string_at(ptr, size.value * dtype_.itemsize), dtype=dtype_)

    def orders(self):
        if isinstance(self.settings, OrderSettings):
            return self._batch("orders", ORDER_V2)
        if not hasattr(self.lib, "sim_run_orders"):
            return self._batch("orders", ORDER)
        result = self._batch("orders", ORDER_V2)
        return result[list(ORDER.names)].astype(ORDER, copy=False)

    def cashflows(self):
        return self._batch("cashflows", CASHFLOW)

    def targets(self):
        return self._batch("targets", TARGET)

    def curve(self):
        return self._batch("curve", EQUITY)

    def equity(self):
        if not self.handle:
            raise RuntimeError("Simulator is closed")
        out = np.empty(1, dtype=EQUITY)
        self.lib.sim_equity(self.handle, out.ctypes.data)
        return out[0]

    def positions(self):
        if not self.handle:
            raise RuntimeError("Simulator is closed")
        out = np.empty(self.n, dtype=POSITION)
        self.lib.sim_positions(self.handle, out.ctypes.data)
        return out

    def risk(self):
        if not self.handle or not hasattr(self.lib, "sim_risk"):
            raise RuntimeError("Risk summary requires an open current native library")
        out = np.empty(1, dtype=RISK)
        self.lib.sim_risk(self.handle, out.ctypes.data)
        return out[0]


class ShockFeatureStream:
    def __init__(self, symbols, *, flow, price_range):
        self.lib = library()
        self.position = 0
        self.handle = self.lib.sim_shock_features_create(symbols, flow, price_range)
        if not self.handle:
            raise MemoryError("Native shared feature state could not be allocated")

    def close(self):
        if self.handle:
            self.lib.sim_shock_features_delete(self.handle)
            self.handle = None

    def advance(self, block):
        return self.advance_ready(block)[0]

    def advance_ready(self, block, *, warmup=0, pool=None, workers=1):
        arrays = [np.ascontiguousarray(block.values[key], dtype="=f8") for key in
                  ("close", "quote_volume", "buy_quote_volume", "sell_quote_volume")]
        rows, symbols = block.status.shape
        result = np.empty((11, rows, symbols), dtype="=f8")
        ready = np.empty((rows, symbols), dtype=bool)
        args = (self.handle, *(arr.ctypes.data for arr in arrays), block.status.ctypes.data,
                rows, result.ctypes.data, ready.ctypes.data, warmup, self.position)

        def run(first, last):
            return self.lib.sim_shock_features_run_shard(*args, first, last)

        if pool is not None and workers > 1 and rows >= 512:
            workers = min(workers, symbols)
            tasks = [pool.submit(run, symbols * i // workers, symbols * (i+1) // workers)
                     for i in range(workers)]
            wait(tasks)
            failures = [task.result() for task in tasks]
        else:
            failures = [run(0, symbols)]
        if any(failures):
            raise RuntimeError("Native shared feature computation failed")
        self.position += rows
        return result, ready


class _OrderTape:
    """Borrow validated block axes once for trusted block-major order callbacks."""

    def __init__(self, engine, starts, market, flags, opens, funding):
        self.engine = engine
        opens = np.ascontiguousarray(opens, dtype="=f8")
        self.owners = (starts, market, flags, opens, funding)
        self.addresses = tuple(x.ctypes.data for x in self.owners)
        self.strides = tuple(x.strides[0] if len(x) else x.dtype.itemsize for x in self.owners)
        self.end_funding = np.searchsorted(funding["ms"], starts + engine.settings.interval)

    def run(self, lo, hi, funds_lo, funds_hi, requests):
        sim = self.engine
        lr = len(requests)
        data = np.asarray(requests, dtype=REQUEST) if lr else None
        starts, market, status, opens, funding = (
            addr + pos * stride for addr, pos, stride in zip(
                self.addresses, (lo, lo, lo, lo, funds_lo), self.strides, strict=True
            )
        )
        rc = sim.lib.sim_run_orders(sim.handle, starts, market, status, opens, hi-lo, funding,
                                    funds_hi-funds_lo, data.ctypes.data if lr else None, lr)
        if rc:
            sim.close()
            raise RuntimeError("Native simulator rejected the order slice")
        sim.last_time = self.owners[0][hi-1]
