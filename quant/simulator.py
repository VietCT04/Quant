"""Research adapter to the production V1 accounting and execution engine."""

from dataclasses import asdict, dataclass
import json
from pathlib import Path

import numpy as np
import pyarrow.parquet as pq

from simulator.__main__ import _backtest, backtest
from simulator.feed import MarketDataFeed
from simulator.native import Settings
from simulator.strategies import Precomputed

from ._core import array
from .data import Block, Selection, fingerprint
from .validation import HOLDOUT_END, HOLDOUT_START, _check


@dataclass(frozen=True)
class Targets:
    """A causal aligned matrix of signed USDT target notionals and its decision availability."""

    timestamps_ms: np.ndarray
    symbols: tuple
    signed_usdt: np.ndarray
    available_at_ms: np.ndarray


class _TargetStream(Precomputed):
    """Consume aligned target blocks while the native feed advances."""

    def __init__(self, stream, feed):
        self.stream = iter(stream)
        self.feed = feed
        self.pending = None
        self.last = feed.start - feed.step

    def apply_at(self, starts, values):
        while True:
            if self.pending is None:
                bundle = next(self.stream, None)
                if bundle is None:
                    return
                target = _validated(bundle, self.feed)
                if not len(target.starts):
                    continue
                first, last = target.starts[[0, -1]]
                if first <= self.last or first < starts[0]:
                    raise ValueError(
                        "Target streams must be chronological without duplicates or skipped decisions"
                    )
                self.last = last
                self.pending = target
            self.pending.apply_at(starts, values)
            if self.pending.starts[-1] > starts[-1]:
                return
            self.pending = None


@dataclass(frozen=True)
class Result:
    """A completed native simulator report and access to its published Arrow tables."""

    directory: Path
    report: dict
    report_path: Path | None = None

    def table(self, name, *, columns=None):
        """Read one of the native simulator report tables, with optional column projection."""
        path = self._path(name)
        return pq.read_table(path, columns=columns) if path.is_file() else None

    def iter_batches(self, name, *, columns=None, batch_size=65536):
        """Stream batches from a native output table without loading the whole artifact."""
        path = self._path(name)
        if path.is_file():
            yield from pq.ParquetFile(path).iter_batches(
                columns=columns, batch_size=batch_size
            )

    def _path(self, name):
        if name not in {"equity", "orders", "fills", "positions", "funding", "targets"}:
            raise ValueError("Unknown simulator output table")
        return self.directory / f"{name}.parquet"


def targets(block, signed_usdt, *, available_at_ms=None):
    """Bind signed USDT targets to an aligned causal block and optional later availability."""
    if not isinstance(block, Block):
        raise TypeError("USDT targets require the source Block")
    value = array(signed_usdt)
    clock = (
        block.available_at_ms
        if available_at_ms is None
        else np.asarray(available_at_ms, dtype="i8")
    )
    if (
        value.shape != block.status.shape
        or clock.shape != block.available_at_ms.shape
        or np.any(np.isinf(value))
    ):
        raise ValueError(
            "Targets require finite USDT amounts or NaN and the source axes"
        )
    if np.any(clock < block.available_at_ms):
        raise ValueError("Target availability cannot precede source availability")
    return Targets(
        block.timestamps_ms,
        block.symbols,
        np.where(block.status <= 1, value, np.nan),
        clock,
    )


def _feed(data, block_steps, *, release=False):
    if isinstance(data, Selection):
        _check(data.start, data.end, infrastructure=release)
        feed = MarketDataFeed(
            data.dataset, data.symbols, data.start, data.end, block_steps=block_steps
        )
    elif isinstance(data, MarketDataFeed):
        feed = data
        _check(feed.start, feed.end)
        if feed.cursor or np.any(feed.status_counts):
            raise ValueError("Pass a fresh feed to a new simulation")
    else:
        raise TypeError("Expected a data Selection or MarketDataFeed")
    return feed


def _validated(targets, feed):
    if not isinstance(targets, Targets):
        raise TypeError(
            "Use quant.simulator.targets to construct causal signed USDT targets"
        )
    starts = np.asarray(targets.timestamps_ms, dtype="i8")
    clock = np.asarray(targets.available_at_ms, dtype="i8")
    value = array(targets.signed_usdt)
    if (
        targets.symbols != feed.symbols
        or starts.ndim != 1
        or clock.shape != starts.shape
        or value.shape != (len(starts), len(feed.symbols))
        or np.any(np.isinf(value))
        or np.any(starts % feed.step)
        or np.any(starts < feed.start)
        or np.any(starts >= feed.end)
        or np.any(clock < starts + feed.step)
        or np.any(clock > starts + feed.step + feed.lag)
    ):
        raise ValueError(
            "Targets must use the feed axes and be available by the corresponding decision"
        )
    if feed.end in (HOLDOUT_START, HOLDOUT_END):
        value = np.where(clock[:, None] < feed.end, value, np.nan)
    return Precomputed(starts, value)


def _settings(config, feed):
    settings = Settings(interval=feed.step, lag=feed.lag) if config is None else config
    if not isinstance(settings, Settings):
        raise TypeError("Expected existing native Settings")
    return settings


def run(data, targets, config=None, *, out, block_steps=4096):
    """Replay causal signed USDT targets or a chronological stream of target blocks through V1."""
    feed = _feed(data, block_steps)
    settings = _settings(config, feed)
    strategy = (
        _validated(targets, feed)
        if isinstance(targets, Targets)
        else _TargetStream(targets, feed)
    )
    destination = Path(out)
    report = backtest(feed, settings, strategy, destination)
    return Result(destination, report)


def target_digest(targets):
    """Hash the exact symbol ordering, signed quantities and availability of frozen targets."""
    from .features import _digest
    import hashlib

    if not isinstance(targets, Targets):
        raise TypeError("Require one frozen Targets snapshot")
    digest = _digest(
        (
            np.asarray(targets.timestamps_ms, dtype="<i8"),
            np.asarray(targets.available_at_ms, dtype="<i8"),
            np.asarray(targets.signed_usdt, dtype="<f8"),
        )
    )
    return hashlib.sha256(json.dumps([targets.symbols, digest]).encode()).hexdigest()


def final_run(data, targets, config, *, out, release_path, block_steps=4096):
    """Run one authorized frozen holdout evaluation and embed its release receipt in the native report."""
    from .experiment import _git
    from .validation import _verify_release

    if not isinstance(data, Selection) or (data.start, data.end) != (
        HOLDOUT_START,
        HOLDOUT_END,
    ):
        raise ValueError("Final evaluation must use the full reserved period")
    release = _verify_release(release_path)
    spec = release["specification"]
    git = _git()
    if (
        git["dirty"] is not False
        or not git["commit"]
        or spec["git_commit"] != git["commit"]
        or spec["universe"] != list(data.symbols)
        or spec["dataset_fingerprint"] != fingerprint(data)
        or spec["target_digest"] != target_digest(targets)
        or not isinstance(config, Settings)
        or spec["cost_model"] != asdict(config)
    ):
        raise PermissionError(
            "Release does not match the clean commit, dataset, targets, universe and settings"
        )
    feed = _feed(data, block_steps, release=True)
    strategy = _validated(targets, feed)
    destination = Path(out)
    used = Path(release_path).with_suffix(".used.json")
    with used.open("x", encoding="utf-8") as handle:
        json.dump(
            {"release_id": release["release_id"], "report": str(destination)}, handle
        )
    report = _backtest(feed, config, strategy, destination, release=release)
    return Result(destination, report)


from ._batch import (
    BatchResult as BatchResult,
    Candidate as Candidate,
    run_batch as run_batch,
)

__all__ = ["targets", "run", "target_digest", "final_run", "run_batch"]
