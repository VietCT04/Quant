"""Durable local experiment registration, audit and provenance."""

from contextlib import closing
from datetime import UTC, datetime
import json
from pathlib import Path
import sqlite3
import subprocess
from time import perf_counter
from uuid import uuid4

import numpy as np

from . import __version__
from .validation import _check


def _plain(x):
    if isinstance(x, np.generic):
        return _plain(x.item())
    if isinstance(x, np.ndarray):
        return _plain(x.tolist())
    if isinstance(x, float) and not np.isfinite(x):
        return None
    if isinstance(x, Path):
        return str(x)
    if isinstance(x, dict):
        return {str(k): _plain(v) for k, v in x.items()}
    if isinstance(x, (list, tuple)):
        return [_plain(v) for v in x]
    return x


def _dump(data):
    return json.dumps(_plain(data), sort_keys=True, allow_nan=False)


def _now():
    return datetime.now(UTC).isoformat()


def _git():
    try:
        repository = Path(__file__).resolve().parent.parent
        prefix = [
            "git",
            "-C",
            str(repository),
            "-c",
            f"safe.directory={repository.as_posix()}",
        ]
        commit = subprocess.check_output(
            [*prefix, "rev-parse", "HEAD"], text=True, stderr=subprocess.DEVNULL
        ).strip()
        dirty = bool(
            subprocess.check_output(
                [*prefix, "status", "--porcelain"], text=True, stderr=subprocess.DEVNULL
            ).strip()
        )
    except (OSError, subprocess.CalledProcessError):
        return {"commit": None, "dirty": None}
    return {"commit": commit, "dirty": dirty}


def connect(path):
    """Open a local experiment registry with schema initialization; caller closes connection."""
    conn = sqlite3.connect(path, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.executescript("""
        PRAGMA journal_mode=WAL;
        CREATE TABLE IF NOT EXISTS experiments (
          id TEXT PRIMARY KEY, created TEXT NOT NULL, status TEXT NOT NULL,
          hypothesis TEXT NOT NULL, specification TEXT NOT NULL, outcome TEXT
        );
        CREATE INDEX IF NOT EXISTS experiments_status ON experiments(status,created);
    """)
    return conn


def register(
    path,
    *,
    hypothesis,
    parameters,
    start,
    end,
    universe,
    dataset_fingerprint,
    seed,
    features,
    evaluation,
    costs,
    candidate_count=1,
):
    """Record a development experiment and complete provenance before inspecting its result."""
    lo, hi = _check(start, end)
    if (
        not hypothesis.strip()
        or not universe
        or not dataset_fingerprint
        or type(seed) is not int
        or candidate_count < 1
    ):
        raise ValueError(
            "Hypothesis, universe, fingerprint, integer seed and trial count are required"
        )
    from pathlib import Path
    from marketdata.model import sha256

    simulator = Path(__file__).resolve().parent.parent / "simulator" / "core.cpp"
    spec = dict(
        parameters=parameters,
        start_ms=lo,
        end_ms=hi,
        universe=universe,
        dataset_fingerprint=dataset_fingerprint,
        seed=seed,
        features=features,
        evaluation=evaluation,
        costs=costs,
        candidate_count=candidate_count,
        sdk_version=__version__,
        simulator_version=sha256(simulator) if simulator.is_file() else None,
        git=_git(),
    )
    experiment_id = uuid4().hex
    with closing(connect(path)) as conn, conn:
        conn.execute(
            "INSERT INTO experiments VALUES (?,?,?,?,?,NULL)",
            (experiment_id, _now(), "pending", hypothesis, _dump(spec)),
        )
    return experiment_id


def finish(
    path,
    experiment_id,
    *,
    metrics=None,
    runtime_seconds,
    warnings=(),
    artifacts=(),
    failure=None,
):
    """Record results or failure once; retain negative and unsuccessful trials."""
    if not np.isfinite(runtime_seconds) or runtime_seconds < 0:
        raise ValueError("Runtime must be nonnegative")
    outcome = dict(
        metrics={} if metrics is None else metrics,
        runtime_seconds=runtime_seconds,
        warnings=warnings,
        artifacts=artifacts,
        failure=failure,
        completed=_now(),
    )
    with closing(connect(path)) as conn, conn:
        result = conn.execute(
            "UPDATE experiments SET status=?,outcome=? WHERE id=? AND status IN ('pending','running')",
            (
                "failed" if failure is not None else "completed",
                _dump(outcome),
                experiment_id,
            ),
        )
        if result.rowcount != 1:
            raise ValueError("Unknown or already finalized experiment")


def _record(row):
    return dict(
        id=row["id"],
        created=row["created"],
        status=row["status"],
        hypothesis=row["hypothesis"],
        specification=json.loads(row["specification"]),
        outcome=json.loads(row["outcome"]) if row["outcome"] is not None else None,
    )


def search(path, *, query="", status=None, limit=100):
    """Filter recorded hypotheses and statuses in reverse chronological order."""
    if not 1 <= limit <= 10_000:
        raise ValueError("Search limit out of range")
    with closing(connect(path)) as conn:
        rows = (
            conn.execute(
                """SELECT * FROM experiments WHERE instr(lower(hypothesis), lower(?))>0
            AND (? IS NULL OR status=?) ORDER BY created DESC,id LIMIT ?""",
                (query, status, status, limit),
            )
            if query
            else (
                conn.execute(
                    """SELECT * FROM experiments WHERE (? IS NULL OR status=?) ORDER BY created DESC,id LIMIT ?""",
                    (status, status, limit),
                )
            )
        )
        return [_record(row) for row in rows]


def reproduce(path, experiment_id):
    """Retrieve a frozen experiment record for caller-controlled reproduction."""
    with closing(connect(path)) as conn:
        row = conn.execute(
            "SELECT * FROM experiments WHERE id=?", (experiment_id,)
        ).fetchone()
    if row is None:
        raise KeyError(experiment_id)
    return _record(row)


def compare(path, ids, *, metrics):
    """Compare selected metric keys for recorded trials without dropping failures."""
    rows = [reproduce(path, key) for key in ids]
    return [
        {
            "id": r["id"],
            "status": r["status"],
            **{
                metric: (r["outcome"] or {}).get("metrics", {}).get(metric)
                for metric in metrics
            },
        }
        for r in rows
    ]


def run(path, experiment_id, execute):
    """Run a registered trial once, finalizing both exceptions and negative results."""
    with closing(connect(path)) as conn, conn:
        changed = conn.execute(
            "UPDATE experiments SET status='running' WHERE id=? AND status='pending'",
            (experiment_id,),
        )
        if changed.rowcount != 1:
            raise ValueError("Only a pending known experiment may be executed")
    specification = reproduce(path, experiment_id)["specification"]
    start = perf_counter()
    try:
        result = execute(specification)
        if not isinstance(result, dict) or "metrics" not in result:
            raise TypeError(
                "Experiment function must return a mapping containing metrics"
            )
        finish(path, experiment_id, runtime_seconds=perf_counter() - start, **result)
        return result
    except BaseException as error:
        finish(
            path,
            experiment_id,
            runtime_seconds=perf_counter() - start,
            failure={"type": type(error).__name__, "message": str(error)},
        )
        raise


__all__ = ["register", "finish", "search", "reproduce", "compare", "run"]
