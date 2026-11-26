"""Durable, local dispatch of registered experiments to trusted batch handlers."""

from contextlib import closing
from hashlib import sha256
import json
import socket
from pathlib import Path
from time import perf_counter
from uuid import uuid4

from marketdata.state import writer_lock

from . import experiment, requests
from ._resources import hardware as hardware

_SCHEMA = """
CREATE TABLE IF NOT EXISTS research_jobs (
  id TEXT PRIMARY KEY REFERENCES experiments(id),
  identity TEXT UNIQUE NOT NULL,
  kind TEXT NOT NULL,
  dependencies TEXT NOT NULL,
  capabilities TEXT NOT NULL,
  attempts INTEGER NOT NULL DEFAULT 0,
  max_attempts INTEGER NOT NULL,
  cancel_requested INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS research_job_options (
  id TEXT PRIMARY KEY REFERENCES research_jobs(id),
  priority INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS research_attempts (
  job TEXT NOT NULL REFERENCES research_jobs(id),
  attempt INTEGER NOT NULL,
  started TEXT NOT NULL,
  ended TEXT,
  outcome TEXT,
  PRIMARY KEY(job,attempt)
);
"""


def _db(path):
    conn = experiment.connect(path)
    conn.execute("PRAGMA foreign_keys=ON")
    conn.executescript(_SCHEMA)
    return conn


def submit(
    path,
    jobs,
    *,
    hypothesis,
    start,
    end,
    universe,
    dataset_fingerprint,
    seed,
    features,
    evaluation,
    costs,
    max_attempts=2,
):
    """Atomically register and deduplicate jobs with experiment provenance and optional prerequisites."""
    jobs = list(jobs)
    if not jobs or type(max_attempts) is not int or max_attempts < 1:
        raise ValueError("Submit at least one job with a positive retry limit")
    common = experiment._spec(
        hypothesis,
        {},
        start,
        end,
        universe,
        dataset_fingerprint,
        seed,
        features,
        evaluation,
        costs,
        len(jobs),
    )
    with closing(_db(path)) as conn, conn:
        conn.execute("BEGIN IMMEDIATE")
        ids = _submit(conn, jobs, hypothesis, common, max_attempts)
    _notify(path)
    return ids


def _notify(path):
    """Best-effort local wakeup after commit. Registry state remains authoritative."""
    try:
        port = int(Path(str(Path(path).resolve()) + ".wake").read_text())
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as wake:
            wake.sendto(b"wake", ("127.0.0.1", port))
    except (OSError, ValueError, OverflowError):
        pass


def _submit(conn, jobs, hypothesis, common, max_attempts, *, namespace=None):
    ids = []
    for job in jobs:
        kind = job["kind"]
        if not isinstance(kind, str) or not kind.strip():
            raise ValueError("Job handlers need a registered kind")
        dependencies = sorted(set(job.get("depends_on", ())))
        for dep in dependencies:
            if (
                conn.execute(
                    "SELECT id FROM research_jobs WHERE id=?", (dep,)
                ).fetchone()
                is None
            ):
                raise KeyError(f"Unknown dependency {dep}")
        capabilities = job.get("capabilities", [])
        specification = common | {
            "parameters": job.get("parameters", {}),
            "job_kind": kind,
            "dependencies": dependencies,
            "capabilities": capabilities,
            **({"strategy": job["strategy"]} if "strategy" in job else {}),
        }
        fingerprint = {
            k: v for k, v in specification.items() if k != "candidate_count"
        }
        definition = experiment._dump(
            {
                "hypothesis": hypothesis,
                "specification": fingerprint,
                "max_attempts": max_attempts,
                **({"namespace": namespace} if namespace else {}),
            }
        )
        identity = sha256(definition.encode()).hexdigest()
        existing = conn.execute(
            "SELECT id FROM research_jobs WHERE identity=?", (identity,)
        ).fetchone()
        if existing:
            ids.append(existing[0])
            continue
        key = uuid4().hex
        conn.execute(
            "INSERT INTO experiments VALUES (?,?,?,?,?,NULL)",
            (
                key,
                experiment._now(),
                "queued",
                hypothesis,
                experiment._dump(specification),
            ),
        )
        conn.execute(
            """INSERT INTO research_jobs
            (id,identity,kind,dependencies,capabilities,max_attempts) VALUES (?,?,?,?,?,?)""",
            (
                key,
                identity,
                kind,
                experiment._dump(dependencies),
                experiment._dump(capabilities),
                max_attempts,
            ),
        )
        ids.append(key)
    return ids


def cancel(path, job_id):
    """Cancel a queued job or record a cancellation request for the running handler."""
    with closing(_db(path)) as db, db:
        db.execute("BEGIN IMMEDIATE")
        _cancel(db, job_id)
    _notify(path)


def _cancel(db, job_id):
    if not db.execute(
        "UPDATE research_jobs SET cancel_requested=1 WHERE id=? AND EXISTS (SELECT 1 FROM experiments WHERE experiments.id=research_jobs.id AND status IN ('queued','running'))", (job_id,)
    ).rowcount and not db.execute("SELECT 1 FROM research_jobs WHERE id=?", (job_id,)).fetchone():
        raise KeyError(job_id)
    db.execute(
        """UPDATE experiments SET status='cancelled',outcome=?
                  WHERE id=? AND status='queued'""",
        (
            experiment._dump(
                {"failure": {"type": "Cancelled"}, "completed": experiment._now()}
            ),
            job_id,
        ),
    )


def verify_selection(jobs, selection):
    """Bind a trusted handler's selection to the registered ranges, symbol order, and dataset identity."""
    from .data import fingerprint

    source = fingerprint(selection)
    for job in jobs:
        spec = job["specification"]
        if (
            spec["start_ms"] != selection.start
            or spec["end_ms"] != selection.end
            or tuple(spec["universe"]) != selection.symbols
            or spec["dataset_fingerprint"] != source
        ):
            raise ValueError(
                f"Job {job['id']} does not match the handler's market selection"
            )


def status(path, *, kind=None, ids=None):
    """Summarize queue states, committed throughput, and remaining time under observed throughput."""
    with closing(_db(path)) as db:
        records = db.execute(
            """SELECT e.*,j.kind,j.attempts,j.max_attempts,j.cancel_requested
          FROM research_jobs j JOIN experiments e USING(id)
          WHERE (? IS NULL OR j.kind=?) ORDER BY e.created,e.id""",
            (kind, kind),
        )
        wanted = None if ids is None else set(ids)
        rows = [
            (
                experiment._record(row)
                | {
                    k: row[k]
                    for k in ("kind", "attempts", "max_attempts", "cancel_requested")
                }
            )
            for row in records
            if wanted is None or row["id"] in wanted
        ]
        attempts = db.execute(
            "SELECT * FROM research_attempts ORDER BY started"
        ).fetchall()
    counts = dict.fromkeys(("queued", "running", "completed", "failed", "cancelled"), 0)
    for row in rows:
        counts[row["status"]] += 1
    index = {r["id"] for r in rows}
    ends = [r for r in attempts if r["ended"] and r["job"] in index]
    from datetime import datetime

    elapsed = 0
    if ends:
        intervals = sorted(
            (
                datetime.fromisoformat(row["started"]),
                datetime.fromisoformat(row["ended"]),
            )
            for row in ends
        )
        start, end = intervals[0]
        for a, b in intervals[1:]:
            if a > end:
                elapsed += (end - start).total_seconds()
                start, end = a, b
            else:
                end = max(end, b)
        elapsed += (end - start).total_seconds()
    rate = counts["completed"] / elapsed if elapsed else None
    return {
        "counts": counts,
        "completed_per_second": rate,
        "eta_seconds": (counts["queued"] + counts["running"]) / rate if rate else None,
        "jobs": rows,
        "attempt_history": [
            dict(row)
            | {"outcome": json.loads(row["outcome"]) if row["outcome"] else None}
            for row in attempts
            if row["job"] in index
        ],
    }


def _complete(db, job, result, seconds, *, retry=True, force_cancel=False):
    row = db.execute("SELECT * FROM research_jobs WHERE id=?", (job,)).fetchone()
    error = result.get("failure")
    state = (
        "cancelled"
        if force_cancel or row["cancel_requested"]
        else "completed"
        if error is None
        else "queued"
        if retry and row["attempts"] < row["max_attempts"]
        else "failed"
    )
    if state == "cancelled":
        result = {"failure": {"type": "Cancelled"}}
        error = result["failure"]
    now = experiment._now()
    outcome = dict(
        metrics=result.get("metrics", {}),
        runtime_seconds=seconds,
        artifacts=result.get("artifacts", ()),
        warnings=result.get("warnings", ()),
        failure=error,
        **({"execution": result["execution"]} if "execution" in result else {}),
        completed=now,
    )
    report = experiment._dump(outcome)
    db.execute(
        "UPDATE experiments SET status=?,outcome=? WHERE id=?",
        (state, None if state == "queued" else report, job),
    )
    db.execute(
        "UPDATE research_attempts SET ended=?,outcome=? WHERE job=? AND attempt=?",
        (now, report, job, row["attempts"]),
    )


def _failure(error):
    return {"failure": {"type": type(error).__name__, "message": str(error)}}


def run(
    path, handlers, *, claim_size=64, max_batches=None, stop=None,
    batch_key=None, wait=None, paused=None, on_change=None,
):
    """Recover orphaned attempts and execute bounded same-kind batches through trusted local handlers."""
    if (
        type(claim_size) is not int
        or claim_size < 1
        or (max_batches is not None and max_batches < 1)
    ):
        raise ValueError("Batch sizes and run limits must be positive")
    path = Path(path).resolve()
    lock = path.parent / f".{path.name}.dispatch"
    with writer_lock(lock), closing(_db(path)) as db:
        with db:
            db.execute("BEGIN IMMEDIATE")
            for orphan in db.execute(
                "SELECT e.id FROM experiments e JOIN research_jobs j USING(id) WHERE status='running'"
            ).fetchall():
                _complete(db, orphan[0], {"failure": {"type": "InterruptedProcess"}}, 0)
        if on_change is not None:
            on_change()
        batches = 0
        while (max_batches is None or batches < max_batches) and (
            stop is None or not stop.is_set()
        ):
            if paused is not None and paused():
                if wait is None:
                    break
                wait()
                continue
            jobs = []
            chosen = None
            chosen_key = None
            dependency_changed = False
            with db:
                db.execute("BEGIN IMMEDIATE")
                queue = db.execute("""SELECT e.*,j.kind,j.dependencies,j.capabilities,j.attempts
                      FROM experiments e JOIN research_jobs j USING(id)
                      LEFT JOIN research_job_options o USING(id)
                      WHERE e.status='queued' ORDER BY coalesce(o.priority,0) DESC,e.created,e.id""").fetchall()
                for row in queue:
                    kind = row["kind"]
                    if kind not in handlers or chosen is not None and kind != chosen:
                        continue
                    states = [
                        db.execute(
                            "SELECT status FROM experiments WHERE id=?", (dep,)
                        ).fetchone()[0]
                        for dep in json.loads(row["dependencies"])
                    ]
                    if any(s in {"failed", "cancelled"} for s in states):
                        _complete(
                            db,
                            row["id"],
                            {"failure": {"type": "DependencyFailed"}},
                            0,
                            retry=False,
                        )
                        dependency_changed = True
                        continue
                    if any(s != "completed" for s in states):
                        continue
                    job = experiment._record(row) | {"attempt": row["attempts"] + 1}
                    key = batch_key(job) if batch_key is not None else None
                    if chosen is not None and key != chosen_key:
                        continue
                    chosen, chosen_key = kind, key
                    db.execute(
                        "UPDATE experiments SET status='running' WHERE id=?",
                        (row["id"],),
                    )
                    db.execute(
                        "UPDATE research_jobs SET attempts=attempts+1 WHERE id=?",
                        (row["id"],),
                    )
                    db.execute(
                        "INSERT INTO research_attempts VALUES (?,?,?,NULL,NULL)",
                        (row["id"], row["attempts"] + 1, experiment._now()),
                    )
                    jobs.append(job)
                    if len(jobs) >= claim_size:
                        break
            if jobs or dependency_changed:
                if on_change is not None:
                    on_change()
            if not jobs:
                if dependency_changed:
                    continue
                if wait is None:
                    break
                wait()
                continue
            ready, results = [], {}
            tick = perf_counter()
            try:
                for job in jobs:
                    for cap in job["specification"]["capabilities"]:
                        if requests.check(
                            cap["name"],
                            streaming=cap.get("streaming", False),
                            causal=cap.get("causal"),
                        ):
                            continue
                        request = cap.get("request")
                        request_id = None
                        if request is not None:
                            request_id = requests.submit(
                                path,
                                requests.Request(
                                    **(request | {"experiment_id": job["id"]})
                                ),
                            )
                        results[job["id"]] = {
                            "failure": {
                                "type": "MissingCapability",
                                "name": cap["name"],
                                "request_id": request_id,
                            }
                        }
                        break
                    else:
                        ready.append(job)
                if ready:
                    results.update(handlers[chosen](ready))
            except Exception as error:
                results.update(
                    {
                        job["id"]: _failure(error)
                        for job in jobs
                        if job["id"] not in results
                    }
                )
            except BaseException as error:
                with db:
                    db.execute("BEGIN IMMEDIATE")
                    for job in jobs:
                        _complete(db, job["id"], _failure(error), perf_counter() - tick)
                raise
            seconds = perf_counter() - tick
            with db:
                db.execute("BEGIN IMMEDIATE")
                for job in jobs:
                    result = results.get(job["id"])
                    if not isinstance(result, dict) or not (
                        {"metrics", "failure"} & result.keys()
                    ):
                        result = {"failure": {"type": "MissingHandlerResult"}}
                    elif (
                        result.get("failure") is not None
                        and not isinstance(result["failure"], dict)
                        or result.get("metrics") is not None
                        and not isinstance(result["metrics"], dict)
                    ):
                        result = {"failure": {"type": "InvalidHandlerResult"}}
                    else:
                        try:
                            experiment._dump(result)
                        except (TypeError, ValueError, OverflowError) as error:
                            result = _failure(error)
                    retry = (result.get("failure") or {}).get(
                        "type"
                    ) != "MissingCapability"
                    _complete(db, job["id"], result, seconds, retry=retry)
            batches += 1
            if on_change is not None:
                on_change()
    return status(path) if wait is None else None


__all__ = ["hardware", "submit", "cancel", "status", "verify_selection", "run"]
