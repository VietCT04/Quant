"""Hypotheses and the durable control center journal, in the experiment registry."""

from collections import Counter
from contextlib import closing, contextmanager
from datetime import datetime
from hashlib import sha256
import json

from . import experiment, requests, research

TERMINAL = {"completed", "failed", "cancelled", "blocked"}
DEFAULTS = {"paused": False, "cpu_limit": 4, "memory_budget_gib": 2.0, "disk_budget_gib": 8.0}
_SCHEMA = """
CREATE TABLE IF NOT EXISTS control_campaigns (
    id TEXT PRIMARY KEY, name TEXT NOT NULL, created TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS control_hypotheses (
    seq INTEGER PRIMARY KEY AUTOINCREMENT,
    identity TEXT UNIQUE NOT NULL, campaign TEXT REFERENCES control_campaigns(id),
    created TEXT NOT NULL, definition TEXT NOT NULL, priority INTEGER NOT NULL,
    verdict TEXT, verdict_note TEXT, acknowledged INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS control_strategy_versions (
    id TEXT NOT NULL, version TEXT NOT NULL, implementation_hash TEXT NOT NULL,
    PRIMARY KEY(id,version)
);
CREATE TABLE IF NOT EXISTS control_trials (
    hypothesis INTEGER NOT NULL REFERENCES control_hypotheses(seq),
    job TEXT UNIQUE NOT NULL REFERENCES research_jobs(id),
    ordinal INTEGER NOT NULL, PRIMARY KEY(hypothesis,ordinal)
);
CREATE TABLE IF NOT EXISTS control_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT, at TEXT NOT NULL, type TEXT NOT NULL,
    hypothesis INTEGER REFERENCES control_hypotheses(seq), job TEXT
);
CREATE INDEX IF NOT EXISTS control_events_hypothesis ON control_events(hypothesis,id);
CREATE INDEX IF NOT EXISTS control_events_result ON control_events(hypothesis,type,id);
CREATE TABLE IF NOT EXISTS control_verdicts (
    id INTEGER PRIMARY KEY AUTOINCREMENT, hypothesis INTEGER NOT NULL REFERENCES control_hypotheses(seq),
    at TEXT NOT NULL, verdict TEXT NOT NULL, note TEXT NOT NULL, result_revision INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS control_verdicts_hypothesis ON control_verdicts(hypothesis,id);
CREATE TABLE IF NOT EXISTS control_batches (
    id INTEGER PRIMARY KEY AUTOINCREMENT, at TEXT NOT NULL, trials TEXT NOT NULL, profile TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS control_config (
    id INTEGER PRIMARY KEY CHECK(id=1), value TEXT NOT NULL
);
CREATE TRIGGER IF NOT EXISTS control_accepted AFTER INSERT ON control_hypotheses BEGIN
    INSERT INTO control_events(at,type,hypothesis)
    VALUES(strftime('%Y-%m-%dT%H:%M:%fZ','now'),'hypothesis.accepted',new.seq);
END;
CREATE TRIGGER IF NOT EXISTS control_queued AFTER INSERT ON control_trials BEGIN
    INSERT INTO control_events(at,type,hypothesis,job)
    VALUES(strftime('%Y-%m-%dT%H:%M:%fZ','now'),'job.queued',new.hypothesis,new.job);
END;
CREATE TRIGGER IF NOT EXISTS control_transition AFTER UPDATE OF status ON experiments
WHEN old.status != new.status BEGIN
    INSERT INTO control_events(at,type,hypothesis,job)
    SELECT strftime('%Y-%m-%dT%H:%M:%fZ','now'),
        'job.' || CASE new.status WHEN 'running' THEN 'started' ELSE new.status END,
        hypothesis,new.id FROM control_trials WHERE job=new.id;
    INSERT INTO control_events(at,type,hypothesis,job)
    SELECT strftime('%Y-%m-%dT%H:%M:%fZ','now'),'result.available',hypothesis,new.id
    FROM control_trials WHERE job=new.id AND new.status IN ('completed','failed','cancelled');
END;
CREATE TRIGGER IF NOT EXISTS control_capability AFTER INSERT ON requests BEGIN
    INSERT INTO control_events(at,type,job)
    VALUES(strftime('%Y-%m-%dT%H:%M:%fZ','now'),'capability.requested',new.id);
END;
CREATE TRIGGER IF NOT EXISTS control_capability_update AFTER UPDATE ON requests BEGIN
    INSERT INTO control_events(at,type,job)
    VALUES(strftime('%Y-%m-%dT%H:%M:%fZ','now'),'capability.changed',new.id);
END;
CREATE TRIGGER IF NOT EXISTS control_capability_link AFTER INSERT ON request_origins BEGIN
    INSERT INTO control_events(at,type,job)
    VALUES(strftime('%Y-%m-%dT%H:%M:%fZ','now'),'capability.changed',new.request_id);
END;
"""


def initialize(path):
    with closing(requests._connect(path)), closing(research._db(path)) as db:
        db.executescript(_SCHEMA)


@contextmanager
def transaction(path, *, write=False):
    with closing(experiment.connect(path)) as db, db:
        db.execute("PRAGMA foreign_keys=ON")
        if write:
            db.execute("PRAGMA synchronous=FULL")
        db.execute("BEGIN IMMEDIATE" if write else "BEGIN")
        yield db


def identifier(seq):
    return f"H-{seq:04d}"


def sequence(key):
    if not isinstance(key, str) or not key.startswith("H-") or not key[2:].isascii() or not key[2:].isdigit():
        raise KeyError("Unknown hypothesis")
    seq = int(key[2:])
    if not 0 < seq < 2**63:
        raise KeyError("Unknown hypothesis")
    return seq


def journal(db, event, seq=None, job=None):
    db.execute(
        "INSERT INTO control_events(at,type,hypothesis,job) VALUES(?,?,?,?)",
        (experiment._now(), event, seq, job),
    )


def _identity(item):
    return sha256(experiment._dump(item).encode()).hexdigest()


def priority(value):
    if type(value) is not int or not -100 <= value <= 100:
        raise ValueError("Priority must be an integer from -100 to 100")
    return value


def _definition(item, validate):
    for field in ("name", "family", "mechanism", "definition", "prediction", "stage"):
        if not isinstance(item[field], str) or not item[field].strip():
            raise ValueError(f"{field} must be nonempty text")
    protocol, candidates = item["protocol"], item["candidates"]
    if not isinstance(protocol, dict) or not isinstance(protocol.get("evaluation"), dict):
        raise ValueError("A structured development evaluation protocol is required")
    if not isinstance(candidates, list) or not 1 <= len(candidates) <= 5000:
        raise ValueError("Supply 1 to 5000 candidate configurations per hypothesis")
    if not isinstance(item.get("parameters", {}), dict):
        raise ValueError("Hypothesis parameters must be an object")
    dependencies = item.get("depends_on", [])
    if not isinstance(dependencies, list) or any(not isinstance(dep, str) for dep in dependencies):
        raise ValueError("Dependencies must be a list of hypothesis IDs or campaign keys")
    capabilities = item.get("capabilities", [])
    if not isinstance(capabilities, list):
        raise ValueError("Capabilities must be a list")
    for cap in capabilities:
        if not isinstance(cap, dict) or not isinstance(cap.get("name"), str):
            raise ValueError("Capabilities require canonical SDK names")
        if "request" in cap:
            req = requests.Request(**cap["request"])
            if req.experiment_id is not None or f"{req.module}.{req.name}" != cap["name"]:
                raise ValueError("Capability request must match the required SDK name")
    rank = priority(item.get("priority", 0))
    retries = item.get("max_attempts", 2)
    if type(retries) is not int or not 1 <= retries <= 10:
        raise ValueError("Attempt limit must be an integer from 1 to 10")
    common = experiment._spec(
        item["mechanism"], {}, protocol["start"], protocol["end"], protocol["universe"],
        protocol["dataset_fingerprint"], protocol.get("seed", 0),
        protocol.get("features", []), protocol["evaluation"], protocol.get("costs", {}), len(candidates),
    )
    prepared = [candidate | (validate(candidate, common) or {}) for candidate in candidates]
    if len({_identity(candidate) for candidate in prepared}) != len(candidates):
        raise ValueError("Candidate configurations must be distinct within a hypothesis")
    return common, rank, retries, prepared


def submit(path, payload, validate):
    """Persist an entire submission before returning stable IDs."""
    if not isinstance(payload, dict):
        raise ValueError("Supply a hypothesis object or a hypotheses envelope")
    items = payload.get("hypotheses", [payload])
    if not isinstance(items, list) or not 1 <= len(items) <= 1000:
        raise ValueError("Submit 1 to 1000 hypotheses")
    campaign = payload.get("campaign")
    if campaign is not None and (not isinstance(campaign, str) or not campaign.strip()):
        raise ValueError("Campaign must be nonempty text")
    campaign_id = f"C-{_identity({'name': campaign})[:16]}" if campaign else None
    staged = {}
    for index, item in enumerate(items):
        if not isinstance(item, dict):
            raise ValueError("Each hypothesis must be an object")
        key = item.get("key", str(index))
        if not isinstance(key, str) or not key or key.startswith("H-") or key in staged:
            raise ValueError("Campaign keys must be unique strings and cannot start with H-")
        staged[key] = (item, *_definition(item, validate))
    accepted = {}
    with transaction(path, write=True) as db:
        if campaign:
            db.execute("INSERT OR IGNORE INTO control_campaigns VALUES(?,?,?)", (campaign_id, campaign, experiment._now()))
        pending = set(staged)
        while pending:
            count = len(pending)
            for key, (item, common, rank, retries, prepared) in staged.items():
                if key not in pending:
                    continue
                refs = item.get("depends_on", [])
                if any(dep in pending for dep in refs):
                    continue
                seqs = [accepted[dep] if dep in accepted else sequence(dep) for dep in refs]
                dependencies = []
                for dep in sorted(set(seqs)):
                    ids = _job_ids(db, dep)
                    dependencies.extend(ids)
                frozen = item | {"depends_on": [identifier(dep) for dep in sorted(set(seqs))]}
                configuration = [campaign_id, {k: v for k, v in frozen.items() if k != "priority"}]
                if any("strategy" in candidate for candidate in prepared):
                    configuration.append(prepared)
                for candidate in prepared:
                    if "strategy" not in candidate:
                        continue
                    binding = candidate["strategy"]
                    selector = binding["id"], binding["version"]
                    db.execute("INSERT OR IGNORE INTO control_strategy_versions VALUES(?,?,?)",
                               (*selector, binding["implementation_hash"]))
                    recorded = db.execute(
                        "SELECT implementation_hash FROM control_strategy_versions WHERE id=? AND version=?", selector,
                    ).fetchone()[0]
                    if recorded != binding["implementation_hash"]:
                        raise ValueError(f"Strategy {selector[0]} {selector[1]} changed. Register a new version.")
                identity = _identity(configuration)
                existing = db.execute("SELECT seq FROM control_hypotheses WHERE identity=?", (identity,)).fetchone()
                if existing:
                    seq = existing["seq"]
                else:
                    seq = db.execute(
                        "INSERT INTO control_hypotheses(identity,campaign,created,definition,priority) VALUES(?,?,?,?,?)",
                        (identity, campaign_id, experiment._now(), experiment._dump(frozen), rank),
                    ).lastrowid
                    jobs = [
                        {"kind": candidate["kind"], "parameters": candidate.get("parameters", {}),
                         "depends_on": dependencies, "capabilities": item.get("capabilities", []) + [
                             {"name": name} for name in candidate.get("strategy", {}).get("capabilities", [])
                             if name not in {cap["name"] for cap in item.get("capabilities", [])}
                         ], **({"strategy": candidate["strategy"]} if "strategy" in candidate else {})}
                        for candidate in prepared
                    ]
                    ids = research._submit(db, jobs, item["mechanism"], common, retries, namespace=identifier(seq))
                    db.executemany("INSERT INTO control_trials VALUES(?,?,?)", [(seq, job, i) for i, job in enumerate(ids)])
                    db.executemany("INSERT INTO research_job_options VALUES(?,?)", [(job, rank) for job in ids])
                accepted[key] = seq
                pending.remove(key)
            if len(pending) == count:
                raise ValueError("Cyclic hypothesis dependencies")
    return {"campaign_id": campaign_id, "submission_ids": [identifier(accepted[key]) for key in staged]}


def _job_ids(db, seq):
    jobs = [row[0] for row in db.execute("SELECT job FROM control_trials WHERE hypothesis=? ORDER BY ordinal", (seq,))]
    if not jobs:
        raise KeyError(identifier(seq))
    return jobs


def _summaries(db, seqs=None):
    where = ""
    args = ()
    if seqs is not None:
        if not seqs:
            return []
        where = "WHERE h.seq IN (" + ",".join("?" for _ in seqs) + ")"
        args = tuple(seqs)
    rows = db.execute(f"""
        SELECT h.seq,h.campaign,h.created,h.priority,h.verdict,h.verdict_note,h.acknowledged,
            json_extract(h.definition,'$.name') name,
            json_extract(h.definition,'$.family') family,
            json_extract(h.definition,'$.stage') stage,
            json_extract(h.definition,'$.depends_on') dependencies
        FROM control_hypotheses h {where} ORDER BY h.priority DESC,h.seq
    """, args)
    values = []
    for row in rows:
        seq = row["seq"]
        trials = db.execute("""
            SELECT e.status, count(*) n FROM control_trials t JOIN experiments e ON e.id=t.job
            WHERE t.hypothesis=? GROUP BY e.status
        """, (seq,)).fetchall()
        counts = Counter({trial["status"]: trial["n"] for trial in trials})
        errors = db.execute("""
            SELECT DISTINCT json_extract(e.outcome,'$.failure') FROM control_trials t
            JOIN experiments e ON e.id=t.job
            WHERE t.hypothesis=? AND e.status IN ('failed','cancelled')
            AND json_extract(e.outcome,'$.failure') IS NOT NULL
            LIMIT 8
        """, (seq,)).fetchall()
        failures = [json.loads(error[0]) for error in errors]
        dependencies = json.loads(row["dependencies"])
        waiting = counts["queued"] and any(
            db.execute("""
                SELECT 1 FROM control_trials t JOIN experiments e ON e.id=t.job
                WHERE t.hypothesis=? AND e.status!='completed' LIMIT 1
            """, (sequence(dep),)).fetchone()
            for dep in dependencies
        )
        failed_trials = counts["failed"]
        blocked = db.execute("""
            SELECT count(*) FROM control_trials t JOIN experiments e ON e.id=t.job
            WHERE t.hypothesis=? AND e.status='failed'
            AND json_extract(e.outcome,'$.failure.type') IN ('MissingCapability','DependencyFailed')
        """, (seq,)).fetchone()[0] if failed_trials else 0
        total = sum(counts.values())
        if counts["running"]:
            state = "running"
        elif counts["queued"]:
            state = "blocked" if waiting else "queued"
        elif failed_trials > blocked:
            state = "failed"
        elif blocked:
            state = "blocked"
        elif counts["cancelled"]:
            state = "cancelled"
        else:
            state = "completed"
        now = experiment._now()
        timing = db.execute("""
            WITH intervals AS (
                SELECT a.started, a.ended, coalesce(a.ended,?) stop, a.job, a.attempt
                FROM control_trials t JOIN research_attempts a ON a.job=t.job WHERE t.hypothesis=?
            ), ordered AS (
                SELECT *,
                    max(stop) OVER (ORDER BY started,job,attempt ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING) prior
                FROM intervals
            )
            SELECT min(started), max(ended), count(*) FILTER (WHERE ended IS NULL),
                coalesce(sum(max(0, julianday(stop) -
                    julianday(max(started,coalesce(prior,started)))) * 86400),0)
            FROM ordered
        """, (now, seq)).fetchone()
        cancelling = db.execute("""
            SELECT count(*) FROM control_trials t JOIN experiments e ON e.id=t.job
            JOIN research_jobs j ON j.id=t.job
            WHERE t.hypothesis=? AND e.status='running' AND j.cancel_requested=1
        """, (seq,)).fetchone()[0] if counts["running"] else 0
        revision = db.execute("""
            SELECT coalesce(max(id),0) FROM control_events
            WHERE hypothesis=? AND type IN ('result.available','verdict.changed')
        """, (seq,)).fetchone()[0]
        last_result = db.execute("""
            SELECT at FROM control_events WHERE hypothesis=? AND type='result.available'
            ORDER BY id DESC LIMIT 1
        """, (seq,)).fetchone()
        values.append(dict(row) | {
            "id": identifier(seq), "dependencies": dependencies, "state": state,
            "counts": dict(counts), "total": total, "finished": total - counts["queued"] - counts["running"],
            "first_started": timing[0], "last_attempt": None if timing[2] else timing[1],
            "execution_seconds": timing[3], "timing_sampled_at": now, "active_attempt": bool(timing[2]),
            "cancelling": cancelling,
            "last_result": last_result[0] if last_result else None, "errors": failures, "revision": revision,
            "unreviewed": revision > row["acknowledged"] and not (counts["queued"] or counts["running"]),
        })
    return values


def _events(db, after, limit=500):
    return [
        dict(row) | {"hypothesis": identifier(row["hypothesis"]) if row["hypothesis"] else None}
        for row in db.execute("SELECT * FROM control_events WHERE id>? ORDER BY id LIMIT ?", (after, limit))
    ]


def cursor(path):
    with transaction(path) as db:
        return db.execute("SELECT coalesce(max(id),0) FROM control_events").fetchone()[0]


def snapshot(path):
    with transaction(path) as db:
        cursor = db.execute("SELECT coalesce(max(id),0) FROM control_events").fetchone()[0]
        return {
            "cursor": cursor, "hypotheses": _summaries(db), "requests": requests._search(db),
            "events": _events(db, max(0, cursor - 30), 30),
            "campaigns": [dict(row) for row in db.execute("SELECT * FROM control_campaigns ORDER BY created DESC")],
        }


def updates(path, after):
    with transaction(path) as db:
        events = _events(db, after)
        ids = {sequence(event["hypothesis"]) for event in events if event["hypothesis"]}
        if ids:
            # A prerequisite transition can change the display state of an untouched dependent.
            ids.update(row[0] for row in db.execute("""
                SELECT DISTINCT h.seq FROM control_hypotheses h, json_each(h.definition,'$.depends_on') d
                WHERE d.value IN (SELECT value FROM json_each(?))
            """, (json.dumps([identifier(seq) for seq in ids]),)))
        delta = {"events": events, "hypotheses": _summaries(db, ids)}
        if any(event["type"].startswith("capability.") for event in events):
            delta["requests"] = requests._search(db)
        if any(event["type"] == "hypothesis.accepted" for event in events):
            delta["campaigns"] = [dict(row) for row in db.execute("SELECT * FROM control_campaigns ORDER BY created DESC")]
        return delta


def detail(path, key, *, include_results=False):
    seq = sequence(key)
    with transaction(path) as db:
        summaries = _summaries(db, {seq})
        if not summaries:
            raise KeyError(key)
        row = db.execute("SELECT definition FROM control_hypotheses WHERE seq=?", (seq,)).fetchone()
        value = summaries[0] | {"definition": json.loads(row[0]), "results_url": f"/api/hypotheses/{key}/results"}
        value["verdict_history"] = [dict(row) for row in db.execute(
            "SELECT at,verdict,note,result_revision FROM control_verdicts WHERE hypothesis=? ORDER BY id",
            (seq,),
        )]
        if include_results:
            jobs = db.execute("""
                SELECT e.*,j.kind,j.attempts,j.cancel_requested FROM control_trials t
                JOIN experiments e ON e.id=t.job JOIN research_jobs j ON j.id=t.job
                WHERE t.hypothesis=? ORDER BY t.ordinal
            """, (seq,))
            results = []
            for job in jobs:
                record = experiment._record(job) | {"kind": job["kind"], "cancel_requested": bool(job["cancel_requested"])}
                outcome = record["outcome"] or {}
                record["artifact_urls"] = [f"/api/artifacts/{job['id']}/{i}" for i, _ in enumerate(outcome.get("artifacts", []))]
                record["attempts"] = [
                    dict(attempt) | {"failure": json.loads(attempt["failure"]) if attempt["failure"] else None}
                    for attempt in db.execute("""
                        SELECT attempt,started,ended,json_extract(outcome,'$.failure') failure
                        FROM research_attempts WHERE job=? ORDER BY attempt
                    """, (job["id"],))
                ]
                results.append(record)
            value["experiments"] = results
        return value


def inbox(path, *, unreviewed=False):
    with transaction(path) as db:
        return [
            {k: row[k] for k in ("id", "name", "state", "counts", "errors", "verdict", "revision", "unreviewed", "last_result")}
            | {"results_url": f"/api/hypotheses/{row['id']}/results"}
            for row in _summaries(db)
            if row["revision"] and row["state"] in TERMINAL and row["finished"] == row["total"]
            and (row["unreviewed"] or not unreviewed)
        ]


def action(path, keys, operation, values):
    seqs = list(dict.fromkeys(sequence(key) for key in keys))
    if not seqs or len(seqs) > 1000:
        raise ValueError("Supply 1 to 1000 hypothesis IDs")
    with transaction(path, write=True) as db:
        for seq in seqs:
            jobs = _job_ids(db, seq)
            if operation == "cancel":
                for job in jobs:
                    research._cancel(db, job)
            elif operation == "retry":
                requeued = False
                for job in jobs:
                    status = db.execute("SELECT status FROM experiments WHERE id=?", (job,)).fetchone()[0]
                    if status in {"failed", "cancelled"}:
                        db.execute("UPDATE research_jobs SET cancel_requested=0,max_attempts=max(max_attempts,attempts+1) WHERE id=?", (job,))
                        db.execute("UPDATE experiments SET status='queued',outcome=NULL WHERE id=?", (job,))
                        requeued = True
                if requeued:
                    db.execute("UPDATE control_hypotheses SET verdict=NULL,verdict_note=NULL WHERE seq=?", (seq,))
            elif operation == "priority":
                rank = priority(values["priority"])
                db.execute("UPDATE control_hypotheses SET priority=? WHERE seq=?", (rank, seq))
                db.executemany("UPDATE research_job_options SET priority=? WHERE id=?", [(rank, job) for job in jobs])
            elif operation == "acknowledge":
                revision = values["revision"]
                current = _summaries(db, {seq})[0]["revision"]
                if type(revision) is not int or not 0 < revision <= current:
                    raise ValueError("A previously observed result revision is required")
                db.execute("UPDATE control_hypotheses SET acknowledged=max(acknowledged,?) WHERE seq=?", (revision, seq))
            elif operation == "verdict":
                verdict, note = values["verdict"], values["note"]
                current = _summaries(db, {seq})[0]
                if current["finished"] != current["total"] or verdict not in {"promising", "rejected", "inconclusive"} or not isinstance(note, str) or not note.strip():
                    raise ValueError("A finished hypothesis, verdict and supporting note are required")
                db.execute("UPDATE control_hypotheses SET verdict=?,verdict_note=? WHERE seq=?", (verdict, note, seq))
                db.execute(
                    "INSERT INTO control_verdicts(hypothesis,at,verdict,note,result_revision) VALUES(?,?,?,?,?)",
                    (seq, experiment._now(), verdict, note, current["revision"]),
                )
            else:
                raise ValueError("Unknown queue operation")
            journal(db, operation + ".changed", seq)
        return _summaries(db, set(seqs))


def config(path, changes=None, *, cpus=None):
    with transaction(path, write=changes is not None) as db:
        stored = db.execute("SELECT value FROM control_config WHERE id=1").fetchone()
        current = DEFAULTS | (json.loads(stored[0]) if stored else {})
        if changes is None:
            return current
        if not isinstance(changes, dict) or changes.keys() - DEFAULTS.keys():
            raise ValueError("Unknown service setting")
        current.update(changes)
        if type(current["paused"]) is not bool or type(current["cpu_limit"]) is not int or not 1 <= current["cpu_limit"] <= cpus:
            raise ValueError(f"CPU limit must be an integer between 1 and {cpus}")
        for key in ("memory_budget_gib", "disk_budget_gib"):
            if type(current[key]) not in {float, int} or not 0 < current[key] <= 1024:
                raise ValueError(f"{key} must be greater than 0 and at most 1024 GiB")
        db.execute("INSERT OR REPLACE INTO control_config VALUES(1,?)", (experiment._dump(current),))
        journal(db, "worker.changed")
        return current


def record_batch(path, jobs, profile):
    with transaction(path, write=True) as db:
        return db.execute(
            "INSERT INTO control_batches(at,trials,profile) VALUES(?,?,?)",
            (experiment._now(), experiment._dump(jobs), experiment._dump(profile)),
        ).lastrowid


def last_batch(path):
    with transaction(path) as db:
        row = db.execute("SELECT profile FROM control_batches ORDER BY id DESC LIMIT 1").fetchone()
        return json.loads(row[0]) if row else None


def batch(path, batch_id):
    with transaction(path) as db:
        row = db.execute("SELECT * FROM control_batches WHERE id=?", (batch_id,)).fetchone()
        if row is None:
            raise KeyError(batch_id)
        return dict(row) | {"profile": json.loads(row["profile"]), "trials": json.loads(row["trials"])}
