from __future__ import annotations

import json
import sqlite3
from collections import Counter
from contextlib import contextmanager
from datetime import date
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

from .model import millis, next_month, partition_path, replace_file, utc_day, write_json


@contextmanager
def writer_lock(root):
    directory = root / "metadata"
    directory.mkdir(parents=True, exist_ok=True)
    with (directory / "writer.lock").open("a+b") as file:
        if file.tell() == 0:
            file.write(b"0")
            file.flush()
        file.seek(0)
        if __import__("os").name == "nt":
            import msvcrt
            try:
                msvcrt.locking(file.fileno(), msvcrt.LK_NBLCK, 1)
            except OSError as exc:
                raise RuntimeError("Another writer is working in this data directory") from exc
        else:
            import fcntl
            try:
                fcntl.flock(file, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError as exc:
                raise RuntimeError("Another writer is working in this data directory") from exc
        yield


class Manifest:
    def __init__(self, root: Path):
        self.root = root
        directory = root / "metadata"
        directory.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(directory / "manifest.sqlite")
        self.db.row_factory = sqlite3.Row
        self.db.executescript("""
            PRAGMA journal_mode=WAL;
            PRAGMA synchronous=FULL;
            CREATE TABLE IF NOT EXISTS plan (
                id INTEGER PRIMARY KEY CHECK(id=1), value TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS partitions (
                symbol TEXT, month TEXT, start TEXT NOT NULL, end TEXT NOT NULL, listing INTEGER NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending', result TEXT, PRIMARY KEY(symbol, month)
            );
            CREATE TABLE IF NOT EXISTS fragments (
                symbol TEXT, month TEXT, fragment TEXT, result TEXT NOT NULL,
                PRIMARY KEY(symbol, month, fragment)
            );
        """)

    def close(self):
        self.db.close()

    def plan(self):
        row = self.db.execute("SELECT value FROM plan WHERE id=1").fetchone()
        if row is None:
            raise ValueError("Run 'plan' before acquisition.")
        return json.loads(row["value"])

    def set_plan(self, plan, *, extend_symbols=False):
        old = self.db.execute("SELECT value FROM plan WHERE id=1").fetchone()
        if old is not None:
            previous = json.loads(old["value"])
            fixed = ("start", "interval_ms", "universe_hash", "schema_version")
            if any(previous[f] != plan[f] for f in fixed) or plan["end"] < previous["end"]:
                raise ValueError("An existing plan can only extend its end date. Use another data directory for a different plan.")
            before, after = previous["symbols"], plan["symbols"]
            if before != after and (not extend_symbols or plan["end"] != previous["end"]
                                    or any(after.get(symbol) != listing for symbol, listing in before.items())):
                raise ValueError("Symbol extension must preserve the original date range, symbols, and listing timestamps.")
        start, end = date.fromisoformat(plan["start"]), date.fromisoformat(plan["end"])
        jobs = []
        for symbol, listing in plan["symbols"].items():
            current = max(start, utc_day(listing))
            while current < end:
                month = current.isoformat()[:7]
                stop = min(end, next_month(current))
                jobs.append((symbol, month, str(current), str(stop), listing))
                current = stop
        with self.db:
            self.db.execute("INSERT OR REPLACE INTO plan VALUES (1,?)", (json.dumps(plan),))
            self.db.executemany("""
                INSERT INTO partitions(symbol, month, start, end, listing) VALUES(?,?,?,?,?)
                ON CONFLICT(symbol,month) DO UPDATE SET end=excluded.end, status='pending'
                WHERE excluded.end != partitions.end
            """, jobs)
        write_json(self.root / "metadata" / "plan.json", plan | {"symbol_months": len(jobs)})
        return len(jobs)

    def partitions(self):
        return self.db.execute("SELECT * FROM partitions ORDER BY symbol,month").fetchall()

    def partition(self, task, status, result):
        with self.db:
            self.db.execute("UPDATE partitions SET status=?,result=? WHERE symbol=? AND month=?",
                            (status, json.dumps(result), task["symbol"], task["month"]))

    def get_fragment(self, task, key):
        row = self.db.execute("SELECT result FROM fragments WHERE symbol=? AND month=? AND fragment=?",
                              (task["symbol"], task["month"], key)).fetchone()
        return json.loads(row["result"]) if row else None

    def fragment(self, task, key, result):
        with self.db:
            self.db.execute("INSERT OR REPLACE INTO fragments VALUES(?,?,?,?)",
                            (task["symbol"], task["month"], key, json.dumps(result)))

    def summary(self):
        return {row[0]: row[1] for row in self.db.execute("SELECT status,count(*) FROM partitions GROUP BY status")}

    def published_totals(self):
        row = self.db.execute("""
            SELECT coalesce(sum(json_extract(result,'$.bars')),0),
                   coalesce(sum(json_extract(result,'$.bytes')),0)
            FROM partitions WHERE status IN ('complete','qualified','incomplete')
        """).fetchone()
        return {"bars": row[0], "bytes": row[1]}

    def report(self, audit=None):
        plan = self.plan()
        rows, details = [], []
        for task in self.partitions():
            result = json.loads(task["result"]) if task["result"] else {}
            symbol, month = task["symbol"], task["month"]
            details.append({
                "symbol": symbol, "month": month, "status": task["status"],
                "requested_start": task["start"], "requested_end_exclusive": task["end"], **result,
            })
            if task["status"] in {"pending", "failed"}:
                rows.append({
                    "symbol": symbol, "month": month, "start_ms": millis(date.fromisoformat(task["start"])),
                    "end_ms": millis(date.fromisoformat(task["end"])), "status": task["status"],
                })
                continue
            for source in result.get("sources", []):
                for lo, hi, reason in source["segments"]:
                    rows.append({
                        "symbol": symbol, "month": month, "start_ms": lo, "end_ms": hi,
                        "status": reason, "source": source.get("archive_url"),
                        "crosscheck_status": source.get("validation"),
                    })
        schema = pa.schema([
            ("symbol", pa.string()), ("month", pa.string()), ("start_ms", pa.int64()), ("end_ms", pa.int64()),
            ("status", pa.string()), ("source", pa.string()), ("crosscheck_status", pa.string()),
        ])
        table = pa.Table.from_pylist(rows, schema=schema)
        meta = self.root / "metadata"
        staged = meta / "coverage.parquet.part"
        pq.write_table(table, staged, compression="zstd")
        replace_file(staged, meta / "coverage.parquet")
        summary = self.summary()
        coverage_slots = Counter()
        for row in rows:
            coverage_slots[row["status"]] += (row["end_ms"] - row["start_ms"]) // plan["interval_ms"]
        totals = {
            "bars": sum(r.get("bars", 0) for r in details if r["status"] not in {"pending", "failed"}),
            "bytes": sum(r.get("bytes", 0) for r in details if r["status"] not in {"pending", "failed"}),
            "failed": sum(r["status"] == "failed" for r in details),
            "coverage_slots": dict(sorted(coverage_slots.items())),
            "mismatched_reference_minutes": sum(s.get("crosscheck", {}).get("mismatched_minutes", 0)
                                               for r in details for s in r.get("sources", [])),
        }
        report = {
            "plan": plan, "partition_status_counts": summary, "totals": totals, "partitions": details,
        }
        if audit is not None:
            report["output_audit"] = audit
        write_json(meta / "validation_report.json", report)
        lines = ["Binance USD-M historical data validation", "",
                 f"UTC date range {plan['start']} to {plan['end']} (exclusive)",
                 f"Interval {plan['interval_ms']} ms", f"Partitions {summary}",
                 f"Published {totals['bars']:,} bars in {totals['bytes']:,} bytes",
                 f"Reference minutes with differences {totals['mismatched_reference_minutes']:,}",
                 "", "Slots by coverage status",
                 *(f"  {reason} {count:,}" for reason, count in sorted(coverage_slots.items())),
                 "", "See validation_report.json for source checksums and discrepancies.",
                 "See coverage.parquet for covered and unavailable UTC intervals."]
        if audit is not None:
            lines.extend(["", f"Output files audited {audit['audited_partitions']:,}",
                          f"Output audit errors {len(audit['audit_errors']):,}"])
        staged = meta / "validation_summary.txt.part"
        staged.write_text("\n".join(lines) + "\n", encoding="utf-8")
        replace_file(staged, meta / "validation_summary.txt")
        return totals


def output_present(root, task, interval):
    result = json.loads(task["result"]) if task["result"] else {}
    path = partition_path(root, task["symbol"], task["month"], interval)
    return (not result.get("bars") or (path.exists() and path.stat().st_size == result.get("bytes")))
