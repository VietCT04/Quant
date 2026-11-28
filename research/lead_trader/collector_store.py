"""SQLite page archive and checkpoint store for a single public portfolio."""

import hashlib
import json
import sqlite3
from pathlib import Path


DAY = 86_400
HOUR = 3_600
STEP = 900
SETTLE = 300
RETENTION = 7 * DAY


SCHEMA = """
CREATE TABLE IF NOT EXISTS state(key TEXT PRIMARY KEY, body TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS tasks(
 id INTEGER PRIMARY KEY, kind TEXT NOT NULL, lo INTEGER NOT NULL, hi INTEGER NOT NULL,
 due REAL NOT NULL, mode TEXT NOT NULL, state TEXT NOT NULL DEFAULT 'pending',
 page INTEGER NOT NULL DEFAULT 1, cursor TEXT, expected INTEGER, received INTEGER NOT NULL DEFAULT 0,
 last_ms INTEGER, unstable INTEGER NOT NULL DEFAULT 0, failure TEXT,
 UNIQUE(kind,lo,hi,mode));
CREATE INDEX IF NOT EXISTS dispatch ON tasks(state,due);
CREATE TABLE IF NOT EXISTS requests(
 id INTEGER PRIMARY KEY, started REAL NOT NULL, finished REAL, task INTEGER,
 endpoint TEXT NOT NULL, input TEXT NOT NULL, http INTEGER, code TEXT,
 headers TEXT, error TEXT);
CREATE INDEX IF NOT EXISTS usage_time ON requests(started);
CREATE TABLE IF NOT EXISTS pages(
 id INTEGER PRIMARY KEY, request INTEGER, origin TEXT UNIQUE, captured REAL,
 kind TEXT NOT NULL, input TEXT NOT NULL, body TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS orders(
 id TEXT PRIMARY KEY, ms INTEGER NOT NULL, symbol TEXT NOT NULL, body TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS order_time ON orders(ms);
CREATE TABLE IF NOT EXISTS positions(
 id TEXT PRIMARY KEY, ms INTEGER NOT NULL, symbol TEXT NOT NULL, body TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS position_time ON positions(ms);
CREATE TABLE IF NOT EXISTS seen(
 task INTEGER NOT NULL, record TEXT NOT NULL, PRIMARY KEY(task,record));
"""


def connect(home):
    Path(home).mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(Path(home) / "collector.sqlite", timeout=15)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA journal_mode=WAL")
    db.execute("PRAGMA synchronous=FULL")
    db.executescript(SCHEMA)
    return db


def get(db, key, default=None):
    row = db.execute("SELECT body FROM state WHERE key=?", (key,)).fetchone()
    return json.loads(row[0]) if row else default


def put(db, key, value):
    db.execute("""INSERT INTO state VALUES (?,?) ON CONFLICT(key) DO UPDATE SET body=excluded.body
                  WHERE excluded.body!=state.body""",
               (key, json.dumps(value, separators=(",", ":"))))


def identity(kind, row):
    if kind == "position":
        return f"{row['positionId']}:{row['index']}", row["updateTime"]
    keys = ("symbol", "orderTime", "orderUpdateTime", "side", "type", "executedQty", "avgPrice", "totalPnl")
    value = json.dumps([row[k] for k in keys], ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(value.encode()).hexdigest(), row["orderUpdateTime"]


def save_records(db, kind, rows):
    table = "orders" if kind == "order" else "positions"
    items = []
    for row in rows:
        key, ms = identity(kind, row)
        items.append((key, ms, row["symbol"], json.dumps(row, ensure_ascii=False, separators=(",", ":"))))
    db.executemany(f"""INSERT INTO {table} VALUES (?,?,?,?) ON CONFLICT(id) DO UPDATE
        SET ms=excluded.ms,body=excluded.body WHERE excluded.ms>{table}.ms""", items)
    return [(k, ms) for k, ms, _, _ in items]


def task(db, kind, lo, hi, due, mode):
    db.execute("INSERT OR IGNORE INTO tasks(kind,lo,hi,due,mode) VALUES(?,?,?,?,?)", (kind, lo, hi, due, mode))


def coverage(db):
    spans = []
    for lo, hi in db.execute("SELECT lo,hi FROM tasks WHERE kind='order' AND state='complete' ORDER BY lo"):
        if spans and lo <= spans[-1][1]:
            spans[-1][1] = max(hi, spans[-1][1])
        else:
            spans.append([lo, hi])
    return spans


def gaps(lo, hi, spans):
    start = lo
    for a, b in spans:
        if b <= start:
            continue
        if a >= hi:
            break
        if a > start:
            yield [start, min(a, hi)]
        start = max(start, b)
        if start >= hi:
            return
    if start < hi:
        yield [start, hi]


def schedule(db, now, settings):
    settled = int(now - SETTLE) // STEP * STEP * 1000
    hard_boundary = int(now - settings["retention"]) * 1000
    available = int(now - settings["retention"] + 180) * 1000
    horizon = max(available, settled - 2 * STEP * 1000)
    cursor = get(db, "scheduled_until")
    if cursor is None:
        cursor = available
        old = db.execute("SELECT min(ms) FROM orders").fetchone()[0]
        put(db, "requested_from", min(available, old) if old is not None else available)
    elif cursor < available:
        # Preserve the outage and advance the frontier directly to recoverable history.
        cursor = available
    if cursor < horizon:
        for a, b in gaps(cursor, horizon, coverage(db)):
            while a < b:
                stop = min(b, (a // (HOUR * 1000) + 1) * HOUR * 1000)
                task(db, "order", a, stop, now, "backfill")
                a = stop
        cursor = horizon
    while cursor < settled:
        stop = min(settled, (cursor // (STEP * 1000) + 1) * STEP * 1000)
        task(db, "order", cursor, stop, now, "live")
        cursor = stop
    put(db, "scheduled_until", cursor)
    db.execute("""UPDATE tasks SET state='expired',failure='Outside configured public order retention'
                  WHERE kind='order' AND state='pending' AND hi<=?""", (hard_boundary,))
    # A partially expired task can still recover its portion after the lower boundary.
    for row in db.execute("""SELECT id,lo,hi,mode FROM tasks WHERE kind='order' AND state='pending'
                             AND lo<? AND hi>?""", (hard_boundary, hard_boundary)).fetchall():
        if available < row["hi"]:
            task(db, "order", available, row["hi"], now, row["mode"])
        db.execute("UPDATE tasks SET state='expired',failure='Partial retention lapse; suffix rescheduled' WHERE id=?", (row["id"],))
    last = get(db, "position_head_scheduled", 0)
    if now >= last + 1800:
        task(db, "position", int(now), 0, now, "head")
        put(db, "position_head_scheduled", now)
    pending = db.execute("SELECT 1 FROM tasks WHERE kind='position' AND mode='sweep' AND state='pending'").fetchone()
    last = get(db, "position_sweep_finished", 0)
    if pending is None and now >= last + 12 * HOUR:
        task(db, "position", int(now), 0, now, "sweep")
    db.execute("""INSERT OR IGNORE INTO tasks(kind,lo,hi,due,mode)
                  SELECT kind,lo,hi,?,'repair' FROM tasks
                  WHERE kind='order' AND state='unverified' AND mode!='repair' AND lo>=? AND due<=?""",
               (now + 3600, available, now))
    return settled, available


def next_task(db, now, retention=RETENTION):
    # Live orders first, then expiring history, then head refresh and interleaved sweeps.
    urgent_before = int(now - retention + 8 * HOUR) * 1000
    recent_after = int(now - HOUR) * 1000
    last_endpoint = get(db, "recent_sweeps", 0)
    return db.execute("""
        SELECT * FROM tasks WHERE state='pending' AND due<=?
        ORDER BY CASE WHEN kind='order' AND mode='live' AND hi>=? THEN 0
                      WHEN kind='order' AND lo<? THEN 1
                      WHEN kind='position' AND mode='head' THEN 2
                      WHEN kind='position' AND ? THEN 3
                      WHEN kind='order' THEN 4 ELSE 5 END,
                 CASE WHEN kind='order' AND mode='live' AND hi>=? THEN -hi ELSE lo END,id LIMIT 1
    """, (now, recent_after, urgent_before, last_endpoint % 5 == 0, recent_after)).fetchone()


def restart_order(db, job, now, detail, *, split=True):
    lo, hi = job["lo"], job["hi"]
    if not split:
        db.execute("""UPDATE tasks SET cursor=NULL,page=1,expected=NULL,received=0,last_ms=NULL,
                         unstable=unstable+1,due=?,failure=? WHERE id=?""", (now + 600, detail, job["id"]))
        db.execute("DELETE FROM seen WHERE task=?", (job["id"],))
    elif hi - lo > 60_000:
        midpoint = (lo + hi) // 2
        for a, b in ((lo, midpoint), (midpoint, hi)):
            task(db, "order", a, b, now, job["mode"])
        db.execute("UPDATE tasks SET state='split',failure=? WHERE id=?", (detail, job["id"]))
    else:
        db.execute("UPDATE tasks SET state='unverified',failure=? WHERE id=?", (detail, job["id"]))


def advance(db, job, data, now, portfolio_page_size):
    rows = data["list"]
    advertised = int(data["total"])
    if advertised < 0 or not isinstance(rows, list):
        raise ValueError("Binance page count or list has invalid type")
    current = job["id"]
    refs = save_records(db, job["kind"], rows)
    db.executemany("INSERT OR IGNORE INTO seen VALUES (?,?)", [(current, key) for key, _ in refs])
    count = db.execute("SELECT count(*) FROM seen WHERE task=?", (current,)).fetchone()[0]
    expected = advertised if job["expected"] is None else job["expected"]
    received = job["received"] + len(rows)
    newest = refs[0][1] if refs else None
    oldest = refs[-1][1] if refs else job["last_ms"]
    ordered = all(a[1] >= b[1] for a, b in zip(refs, refs[1:]))
    monotone = job["last_ms"] is None or not refs or newest <= job["last_ms"]
    next_cursor = data.get("indexValue")
    if job["kind"] == "order":
        if advertised >= 6000:
            restart_order(db, job, now, "Order response reached the observed 6,000-row cap")
            return
        if any(not job["lo"] <= ms < job["hi"] for _, ms in refs):
            restart_order(db, job, now, "Returned timestamps fall outside the requested order window")
            return
        if expected != advertised or received != count or not ordered or not monotone:
            if job["mode"] == "verify":
                db.execute("""UPDATE tasks SET state='unverified',failure='Later rescan disagreed'
                              WHERE kind='order' AND lo=? AND hi=? AND state='complete'""", (job["lo"], job["hi"]))
            restart_order(db, job, now, "Order total, duplicate rows or ordering changed", split=job["unstable"] >= 1)
            return
        if count == expected:
            state = "complete"
            if job["mode"] == "live":
                task(db, "order", job["lo"], job["hi"], now + 3600, "verify")
            # A later or overlapping response cannot silently omit known orders.
            prior = db.execute("""SELECT count(*) FROM orders WHERE ms>=? AND ms<?
                                  AND id NOT IN (SELECT record FROM seen WHERE task=?)""",
                               (job["lo"], job["hi"], current)).fetchone()[0]
            if prior:
                db.execute("""UPDATE tasks SET state='unverified',failure='Rescan omitted archived rows'
                              WHERE kind='order' AND lo=? AND hi=? AND state='complete'""", (job["lo"], job["hi"]))
                restart_order(db, job, now, "Response omitted archived order identities", split=job["unstable"] >= 1)
                return
        elif refs and next_cursor and (job["cursor"] is None or int(next_cursor) < int(job["cursor"])):
            state = "pending"
        else:
            restart_order(db, job, now, "Order cursor stopped before the advertised total")
            return
        unstable = job["unstable"]
    else:
        unstable = int(bool(job["unstable"] or expected != advertised or count != received or not ordered or not monotone))
        if job["mode"] == "head":
            state = "fetched"
        elif not refs or received >= expected or len(rows) < portfolio_page_size:
            state = "fetched" if not unstable and expected == count else "unverified"
            put(db, "position_sweep_finished", now)
        else:
            state = "pending"
    db.execute("""UPDATE tasks SET state=?,page=page+1,cursor=?,received=?,expected=?,last_ms=?,
                                  unstable=?,failure=NULL WHERE id=?""",
               (state, None if next_cursor is None else str(next_cursor), received, expected, oldest, unstable, current))
