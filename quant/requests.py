"""SQLite capability requests for the future researcher–engineer review workflow."""

from contextlib import closing
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
import hashlib
import json
import re
import sqlite3

from . import registry


@dataclass(frozen=True)
class Request:
    """A validated missing-capability specification and its engineering acceptance criteria."""

    name: str
    module: str
    purpose: str
    required_inputs: tuple
    parameters: dict
    causal: bool
    streaming_required: bool
    acceptance_criteria: tuple
    experiment_id: str | None = None
    specification: str = ""
    output_units: str = ""
    missing_data: str = ""
    warmup: str = ""


def _connect(path):
    database = sqlite3.connect(path, timeout=30)
    database.row_factory = sqlite3.Row
    database.executescript("""CREATE TABLE IF NOT EXISTS requests (
        id TEXT PRIMARY KEY, status TEXT NOT NULL, created TEXT NOT NULL,
        request TEXT NOT NULL, review TEXT);
        CREATE TABLE IF NOT EXISTS request_origins (
            request_id TEXT NOT NULL, experiment_id TEXT NOT NULL, PRIMARY KEY(request_id,experiment_id));
        CREATE TABLE IF NOT EXISTS request_reviews (
            seq INTEGER PRIMARY KEY, request_id TEXT NOT NULL, status TEXT NOT NULL,
            created TEXT NOT NULL, review TEXT NOT NULL);
        CREATE INDEX IF NOT EXISTS request_review_history ON request_reviews(request_id,seq);
    """)
    return database


def check(name, *, streaming=False, causal=None):
    """Return whether the implemented catalog satisfies requested capability traits."""
    try:
        metadata = registry.describe(name)
    except KeyError:
        return False
    return (not streaming or bool(metadata["streaming"])) and (
        causal is None or (metadata["causality"] == "causal") == causal
    )


def submit(path, request):
    """Validate and deduplicate a missing-capability request by its canonical specification."""
    if (
        not isinstance(request, Request)
        or not re.fullmatch(r"[a-z][a-z0-9_]*", request.name)
        or not re.fullmatch(r"quant\.[a-z][a-z0-9_]*", request.module)
        or not request.purpose.strip()
        or type(request.causal) is not bool
        or type(request.streaming_required) is not bool
        or not isinstance(request.parameters, dict)
        or not request.acceptance_criteria
        or any(
            not isinstance(x, str) or not x.strip()
            for x in (*request.acceptance_criteria, *request.required_inputs)
        )
    ):
        raise ValueError("Invalid feature request")
    if check(
        f"{request.module}.{request.name}",
        streaming=request.streaming_required,
        causal=request.causal,
    ):
        raise ValueError("The registered capability already satisfies this request")
    payload = asdict(request)
    payload["required_inputs"] = sorted(set(request.required_inputs))
    identity = {
        k: v
        for k, v in payload.items()
        if k not in {"experiment_id", "purpose", "acceptance_criteria"}
    }
    request_id = hashlib.sha256(
        json.dumps(identity, sort_keys=True, allow_nan=False).encode()
    ).hexdigest()
    with closing(_connect(path)) as conn, conn:
        conn.execute(
            "INSERT OR IGNORE INTO requests VALUES (?,?,?,?,NULL)",
            (
                request_id,
                "pending",
                datetime.now(UTC).isoformat(),
                json.dumps(payload, sort_keys=True, allow_nan=False),
            ),
        )
        if request.experiment_id is not None:
            conn.execute(
                "INSERT OR IGNORE INTO request_origins VALUES (?,?)",
                (request_id, request.experiment_id),
            )
    return request_id


def search(path, *, status=None):
    """Read requests and review links, optionally filtered by lifecycle status."""
    with closing(_connect(path)) as conn:
        return _search(conn, status=status)


def _search(conn, *, status=None):
    rows = conn.execute(
        "SELECT * FROM requests WHERE (? IS NULL OR status=?) ORDER BY created,id",
        (status, status),
    )
    result = []
    for row in rows:
        key = row["id"]
        origins = conn.execute(
            "SELECT experiment_id FROM request_origins WHERE request_id=? ORDER BY experiment_id",
            (key,),
        )
        history = conn.execute(
            "SELECT * FROM request_reviews WHERE request_id=? ORDER BY seq", (key,)
        )
        result.append(
            {
                "id": key,
                "status": row["status"],
                "created": row["created"],
                "request": json.loads(row["request"]),
                "experiment_ids": [r[0] for r in origins],
                "review": json.loads(row["review"]) if row["review"] else None,
                "review_history": [
                    {
                        "status": r["status"],
                        "created": r["created"],
                        **json.loads(r["review"]),
                    }
                    for r in history
                ],
            }
        )
    return result


def transition(
    path,
    request_id,
    status,
    *,
    reviewer,
    findings,
    commit=None,
    tests=(),
    documentation=(),
):
    """Update a request with review findings and require registered implementation links for closure."""
    allowed = {
        "pending": {"accepted", "rejected"},
        "accepted": {"implemented", "rejected"},
    }
    if not reviewer.strip() or not findings.strip():
        raise ValueError("Reviewer and findings are required")
    with closing(_connect(path)) as conn, conn:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            "SELECT status,request FROM requests WHERE id=?", (request_id,)
        ).fetchone()
        if row is None or status not in allowed.get(row["status"], ()):
            raise ValueError("Invalid request status transition")
        if status == "implemented":
            spec = json.loads(row["request"])
            if not (
                commit
                and tests
                and documentation
                and check(
                    f"{spec['module']}.{spec['name']}",
                    streaming=spec["streaming_required"],
                    causal=spec["causal"],
                )
            ):
                raise ValueError(
                    "Implemented requests require matching catalog metadata and test, documentation and commit links"
                )
        review = dict(
            reviewer=reviewer,
            findings=findings,
            commit=commit,
            tests=tests,
            documentation=documentation,
        )
        content = json.dumps(review)
        conn.execute(
            "UPDATE requests SET status=?,review=? WHERE id=?",
            (status, content, request_id),
        )
        conn.execute(
            "INSERT INTO request_reviews(request_id,status,created,review) VALUES (?,?,?,?)",
            (request_id, status, datetime.now(UTC).isoformat(), content),
        )


__all__ = ["check", "submit", "search", "transition"]
