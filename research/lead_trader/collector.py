"""Detached, rate-limit-aware archive of the public Binance portfolio feeds."""

import argparse
import hashlib
import json
import os
import signal
import subprocess
import sys
import time
from contextlib import closing, contextmanager
from datetime import UTC, datetime
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from . import collector_store as store
from . import rate_limit as rate
from .collect import BASE, PORTFOLIO


ROOT = Path(__file__).resolve().parents[2]
DEFAULT = ROOT / "data" / "research" / "lead-trader" / "collector"
DEFAULT_SETTINGS = {"margin": .25, "max_request_cost": 1, "weights": {}, "retention": store.RETENTION}
PAGE_SIZE = 200
SOFT_HEADERS = {"date", "retry-after", "server", "content-type", "cache-control", "age", "x-cache"}
PREFIXES = ("ratelimit", "x-ratelimit", "x-rate-limit", "x-mbx-used-weight", "x-sapi-used-")


def serialized(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


@contextmanager
def lease(home):
    handle = (home / "worker.lock").open("a+b")
    try:
        if handle.seek(0, os.SEEK_END) == 0:
            handle.write(b"L")
            handle.flush()
        handle.seek(0)
        if os.name == "nt":
            import msvcrt
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        handle.close()
        yield False
        return
    try:
        yield True
    finally:
        handle.close()


def request_body(job):
    body = {"portfolioId": PORTFOLIO, "pageSize": PAGE_SIZE, "pageNumber": 1}
    if job["kind"] == "order":
        body.update(startTime=job["lo"], endTime=job["hi"] - 1)
        if job["cursor"]:
            body["indexValue"] = job["cursor"]
    else:
        body["pageNumber"] = job["page"]
    return body


def public_read(endpoint, body):
    request = Request(f"{BASE}/{endpoint}", data=serialized(body).encode(),
                      headers={"Content-Type": "application/json", "Accept": "application/json"}, method="POST")
    try:
        response = urlopen(request, timeout=20)
    except HTTPError as error:
        response = error
    except (URLError, TimeoutError, OSError) as error:
        return None, {}, {}, str(error)
    with response:
        headers = {}
        for name, value in response.headers.items():
            name = name.lower()
            if name in SOFT_HEADERS or name.startswith(PREFIXES):
                headers[name] = f"{headers[name]}, {value}" if name in headers else value
        http = response.code
        try:
            raw = response.read(8_000_001)
        except (TimeoutError, OSError) as error:
            return http, headers, {}, str(error)
    if len(raw) > 8_000_000:
        return http, headers, {}, "Oversized response"
    try:
        parsed = json.loads(raw)
    except (ValueError, UnicodeError):
        return http, headers, {"message": raw[:400].decode(errors="replace")}, "Non-JSON response"
    if not isinstance(parsed, dict):
        return http, headers, {}, "Unexpected response type"
    return http, headers, parsed, None


def execute(db, job, limiter, settings, read=public_read, clock=time.time):
    endpoint = f"{job['kind']}-history"
    body = request_body(job)
    now = clock()
    with db:
        rate.reserve(limiter, now, settings["max_request_cost"])
        store.put(db, "limiter", limiter)
        attempt = db.execute(
            "INSERT INTO requests(started,task,endpoint,input) VALUES(?,?,?,?)", (now, job["id"], endpoint, serialized(body))
        ).lastrowid
        store.put(db, "heartbeat", {"pid": os.getpid(), "time": now, "phase": "request", "task": job["id"]})
    http, headers, payload, error = read(endpoint, body)
    now = clock()
    result = rate.feedback(limiter, http, headers, payload, now, settings)
    if error and result == "error" and http == 200:
        result = rate.feedback(limiter, None, headers, payload, now, settings)
    detail = str(error or payload.get("message") or "")[:400]
    with db:
        db.execute("UPDATE requests SET finished=?,http=?,code=?,headers=?,error=? WHERE id=?",
                   (now, http, payload.get("code"), serialized(headers), detail or None, attempt))
        store.put(db, "limiter", limiter)
        store.put(db, "recent_sweeps", store.get(db, "recent_sweeps", 0) + 1)
        if payload:
            db.execute("INSERT INTO pages(request,captured,kind,input,body) VALUES(?,?,?,?,?)",
                       (attempt, now, job["kind"], serialized(body), serialized(payload)))
        if result == "ok":
            db.execute("SAVEPOINT parsed_page")
            try:
                store.advance(db, job, payload["data"], now, PAGE_SIZE)
            except (KeyError, ValueError, TypeError, OverflowError) as error:
                db.execute("ROLLBACK TO parsed_page")
                db.execute("UPDATE tasks SET due=?,failure=? WHERE id=?",
                           (now + 3600, f"Unrecognized page payload: {error}", job["id"]))
            db.execute("RELEASE parsed_page")
        elif result == "error":
            count = int(job["unstable"]) + 1
            phase = "unverified" if count >= 3 else "pending"
            db.execute("UPDATE tasks SET state=?,unstable=?,due=?,failure=? WHERE id=?",
                       (phase, count, now + 900 * count, f"HTTP {http}: {detail}", job["id"]))
            if phase == "unverified" and job["kind"] == "position" and job["mode"] == "sweep":
                store.put(db, "position_sweep_finished", now)
        else:
            db.execute("UPDATE tasks SET failure=? WHERE id=?", (f"{result}: {detail}", job["id"]))
    return result


def serve(home):
    stop = False

    def handle(_signal, _frame):
        nonlocal stop
        stop = True

    signal.signal(signal.SIGTERM, handle)
    signal.signal(signal.SIGINT, handle)
    with lease(home) as acquired:
        if not acquired:
            return
        with closing(store.connect(home)) as db:
            try:
                while not stop:
                    now = time.time()
                    if store.get(db, "stop", False):
                        break
                    limiter = store.get(db, "limiter", {})
                    settings = store.get(db, "settings", DEFAULT_SETTINGS)
                    paused = limiter.get("paused") or store.get(db, "manual_pause", False)
                    limiter["cooldown"] = max(limiter.get("cooldown", 0), store.get(db, "operator_cooldown", 0))
                    with db:
                        store.schedule(db, now, settings)
                        job = store.next_task(db, now, settings["retention"])
                        when, _, _, blockers = rate.budget(db, limiter, now, settings)
                        phase = "paused" if paused else "idle" if job is None else "waiting"
                        store.put(db, "heartbeat", {"pid": os.getpid(), "time": now, "phase": phase, "blockers": blockers})
                    if job is not None and when <= now and not paused:
                        execute(db, job, limiter, settings)
                    else:
                        time.sleep(min(10., max(.25, when - now)) if when > now else 5.)
            finally:
                with db:
                    store.put(db, "heartbeat", {"pid": os.getpid(), "time": time.time(), "phase": "stopped"})


def iso(value, *, ms=False):
    return datetime.fromtimestamp(value / 1000 if ms else value, UTC).isoformat() if value is not None else None


def status(db):
    limiter = store.get(db, "limiter", {})
    settings = store.get(db, "settings", DEFAULT_SETTINGS)
    now = time.time()
    operator_cooldown = store.get(db, "operator_cooldown", 0)
    if operator_cooldown > limiter.get("cooldown", 0):
        limiter.update(cooldown=operator_cooldown, last_reason="operator-supplied cooldown")
    paused = bool(limiter.get("paused") or store.get(db, "manual_pause", False))
    when, local, remote, blockers = rate.budget(db, limiter, now, settings)
    heart = store.get(db, "heartbeat", {})
    running = heart.get("phase") != "stopped" and now - heart.get("time", 0) < 45
    unfinished = db.execute("SELECT count(*) FROM requests WHERE finished IS NULL").fetchone()[0]
    scopes = {}
    for table in ("orders", "positions"):
        n, lo, hi = db.execute(f"SELECT count(*),min(ms),max(ms) FROM {table}").fetchone()
        scopes[table] = dict(records=n, first_utc=iso(lo, ms=True), last_utc=iso(hi, ms=True))
    rows = db.execute("SELECT kind,mode,state,count(*) AS tasks FROM tasks GROUP BY kind,mode,state")
    complete = store.coverage(db)
    frontier = store.get(db, "scheduled_until")
    start = store.get(db, "requested_from")
    missing = list(store.gaps(start, frontier, complete)) if start is not None and frontier is not None else []
    readable = int(now - settings["retention"]) * 1000
    info = db.execute("""SELECT started,finished,endpoint,http,code,headers,error FROM requests
                         ORDER BY id DESC LIMIT 1""").fetchone()
    newest = dict(info) if info else None
    if newest and newest["headers"]:
        newest["headers"] = json.loads(newest["headers"])
    errors = [dict(row) for row in db.execute("""SELECT kind,mode,lo,hi,page,failure FROM tasks WHERE failure IS NOT NULL
                                                ORDER BY id DESC LIMIT 15""")]
    spans = [{"from_utc": iso(lo, ms=True), "until_utc": iso(hi, ms=True),
              "availability": "expired" if hi <= readable else "partial retention overlap" if lo < readable else "eligible"}
             for lo, hi in missing]
    current = store.next_task(db, now, settings["retention"])
    sweep = db.execute("""
        SELECT id,state,page,expected,received,unstable,failure,
               (SELECT count(*) FROM seen WHERE task=tasks.id) AS distinct_records
        FROM tasks WHERE kind='position' AND mode='sweep' ORDER BY id DESC LIMIT 1
    """).fetchone()
    by_endpoint = {row[0]: row[1] for row in db.execute("SELECT endpoint,count(*) FROM requests GROUP BY endpoint")}
    return {"portfolio_id": PORTFOLIO, "running": running, "worker": heart, "stopping": store.get(db, "stop", False),
            "records": scopes, "tasks": [dict(r) for r in rows], "verified_order_intervals": complete,
            "missing_order_intervals": spans, "order_frontier_utc": iso(frontier, ms=True),
            "latest_position_sweep": iso(store.get(db, "position_sweep_finished")), "position_sweep": dict(sweep) if sweep else None,
            "next_task": dict(current) if current else None, "next_permitted_request_utc": None if paused else iso(when),
            "pacing_seconds": limiter.get("pace", rate.MIN_PACE), "blockers": blockers,
            "local_windows": local, "reported_limits": remote, "paused": paused,
            "requests_made": db.execute("SELECT count(*) FROM requests").fetchone()[0],
            "requests_by_endpoint": by_endpoint, "unfinished_requests": unfinished, "latest_response": newest, "errors": errors}


def import_captures(db, paths):
    sources = [file for path in paths for file in (sorted(path.glob("*.jsonl")) if path.is_dir() else [path])]
    with db:
        for path in sources:
            if path.suffix == ".json":
                original = json.loads(path.read_text(encoding="utf-8-sig"))
                for kind, feed in (("order", "latest_records"), ("position", "position_history")):
                    data = original[feed]
                    origin = hashlib.sha256((kind + serialized(data)).encode()).hexdigest()
                    db.execute("INSERT OR IGNORE INTO pages(origin,kind,input,body) VALUES(?,?,?,?)",
                               (origin, kind, "{}", serialized({"data": {"list": data}})))
                    store.save_records(db, kind, data)
                continue
            if path.name.startswith("order"):
                kind = "order"
            elif path.name == "position-history.jsonl":
                kind = "position"
            else:
                continue
            items = []
            for line in path.read_bytes().splitlines():
                origin = hashlib.sha256(line).hexdigest()
                raw = json.loads(line)
                items.append((raw["request"], raw["data"]))
                db.execute("INSERT OR IGNORE INTO pages(origin,captured,kind,input,body) VALUES(?,?,?,?,?)",
                           (origin, datetime.fromisoformat(raw["utc"]).timestamp(), kind, serialized(raw["request"]),
                            serialized({"data": raw["data"]})))
                store.save_records(db, kind, raw["data"]["list"])
            certify_import(db, kind, items)
        db.execute("""UPDATE tasks SET state='unverified',failure='Imported coverage conflicts with archived identities'
                      WHERE kind='order' AND mode='import' AND state='complete' AND expected!=
                        (SELECT count(*) FROM orders WHERE ms>=tasks.lo AND ms<tasks.hi)""")


def certify_import(db, kind, pages):
    if kind != "order" or not pages:
        return
    first, initial = pages[0]
    lo, end = first.get("startTime"), first.get("endTime")
    if lo is None or end is None:
        return
    rows = [r for request, data in pages for r in data["list"]]
    observed = [store.identity(kind, r) for r in rows]
    keys = {k for k, _ in observed}
    if not (len(keys) == len(rows) == initial["total"] < 6000
            and all(lo <= ms <= end for _, ms in observed)
            and all(r["pageNumber"] == 1 and r.get("startTime") == lo and r.get("endTime") == end
                    and data["total"] == initial["total"] for r, data in pages)
            and all(p[0].get("indexValue") == q[1]["indexValue"] for q, p in zip(pages, pages[1:]))
            and all(a[1] >= b[1] for a, b in zip(observed, observed[1:]))):
        return
    store.task(db, "order", lo, end + 1, 0, "import")
    db.execute("""UPDATE tasks SET state='complete',received=?,expected=?
                  WHERE kind='order' AND lo=? AND hi=? AND mode='import'""", (len(keys), len(keys), lo, end + 1))


def launch(home, db):
    out = home / "service.log"
    executable = Path(sys.executable)
    if os.name == "nt" and executable.name.lower() == "pythonw.exe":
        executable = executable.with_name("python.exe")
    command = [str(executable), "-m", "research.lead_trader.collector", "--home", str(home), "serve"]
    options = ({"creationflags": subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP}
               if os.name == "nt" else {"start_new_session": True})
    with lease(home) as acquired:
        if not acquired:
            return status(db)
        with db:
            store.put(db, "stop", False)
            store.put(db, "heartbeat", {"phase": "starting", "time": time.time()})
    with out.open("ab") as stream:
        child = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=stream, stderr=stream, cwd=ROOT, **options)
    for _ in range(50):
        info = status(db)
        if info["worker"].get("pid") and info["worker"].get("phase") != "stopped":
            return info
        if child.poll() is not None:
            raise RuntimeError(f"Collector exited during startup. See {out}")
        time.sleep(.2)
    raise RuntimeError(f"Collector did not report ready. See {out}")


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--home", type=Path, default=DEFAULT)
    commands = p.add_subparsers(dest="command", required=True)
    for name in ("start", "ensure", "stop", "serve", "status", "resume", "pause"):
        commands.add_parser(name)
    importer = commands.add_parser("import")
    importer.add_argument("paths", nargs="+", type=Path)
    configure = commands.add_parser("configure")
    configure.add_argument("--margin", type=float)
    configure.add_argument("--max-request-cost", type=int)
    configure.add_argument("--weight-cap", action="append", default=[], metavar="HEADER=CAP")
    configure.add_argument("--not-before", type=datetime.fromisoformat)
    exporter = commands.add_parser("export")
    exporter.add_argument("feed", choices=("orders", "positions"))
    exporter.add_argument("file", type=Path)
    args = p.parse_args()
    home = args.home.resolve()
    home.mkdir(parents=True, exist_ok=True)
    if args.command == "serve":
        serve(home)
        return
    with closing(store.connect(home)) as db:
        if args.command == "start" or (args.command == "ensure" and not store.get(db, "stop", False)):
            info = launch(home, db)
        elif args.command == "export":
            with args.file.open("x", encoding="utf-8") as output:
                for row in db.execute(f"SELECT body FROM {args.feed} ORDER BY ms,id"):
                    output.write(row[0] + "\n")
            info = {"file": str(args.file)}
        else:
            if args.command == "import":
                import_captures(db, args.paths)
            with db:
                if args.command == "stop":
                    store.put(db, "stop", True)
                elif args.command in {"pause", "resume"}:
                    store.put(db, "manual_pause", args.command == "pause")
                    if args.command == "resume":
                        limiter = store.get(db, "limiter", {})
                        limiter["paused"] = False
                        store.put(db, "limiter", limiter)
                elif args.command == "configure":
                    settings = store.get(db, "settings", DEFAULT_SETTINGS)
                    if args.margin is not None:
                        if not 0 <= args.margin <= .7:
                            p.error("Margin must be between 0 and 0.7")
                        settings["margin"] = args.margin
                    if args.max_request_cost is not None:
                        if args.max_request_cost < 1:
                            p.error("Request cost must be positive")
                        settings["max_request_cost"] = args.max_request_cost
                    for entry in args.weight_cap:
                        name, cap = entry.lower().split("=", 1)
                        if not name.startswith("x-mbx-used-weight-") or not cap.isdigit() or int(cap) < 1:
                            p.error("Weight caps take a Binance used-weight header and a positive integer")
                        settings["weights"][name] = int(cap)
                    store.put(db, "settings", settings)
                    if args.not_before:
                        store.put(db, "operator_cooldown", max(store.get(db, "operator_cooldown", 0),
                                                               args.not_before.timestamp()))
            info = status(db)
        print(serialized(info))


if __name__ == "__main__":
    main()
