"""Copy one completed summary record to an isolated acceptance registry."""

import argparse
from contextlib import closing
from pathlib import Path
import sqlite3

from quant import control_store


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("destination", type=Path)
    parser.add_argument("--source", type=Path, default=Path("data/quant-control/research.sqlite"))
    parser.add_argument("--candidate", default="48b299ec64e74adc9f24d3315ea04bc3")
    args = parser.parse_args()
    if args.destination.exists():
        raise SystemExit("Use a fresh acceptance registry.")
    args.destination.parent.mkdir(parents=True, exist_ok=True)
    control_store.initialize(args.destination)
    with closing(sqlite3.connect(args.source.resolve().as_uri() + "?mode=ro", uri=True)) as source:
        with control_store.transaction(args.destination, write=True) as db:
            key = args.candidate
            owner = source.execute("SELECT hypothesis FROM control_trials WHERE job=?", (key,)).fetchone()[0]
            rows = source.execute("SELECT job FROM control_trials WHERE hypothesis=?", (owner,)).fetchall()
            if rows != [(key,)]:
                raise ValueError("Acceptance seed requires a hypothesis with exactly one candidate")
            for table, predicate, value in (
                ("control_hypotheses", "seq=? AND campaign IS NULL", owner),
                ("experiments", "id=? AND status='completed'", key),
                ("research_jobs", "id=?", key),
                ("research_job_options", "id=?", key),
                ("control_trials", "job=?", key),
                ("research_attempts", "job=?", key),
            ):
                for row in source.execute(f"SELECT * FROM {table} WHERE {predicate}", (value,)):
                    placeholders = ",".join("?" for _ in row)
                    db.execute(f"INSERT INTO {table} VALUES({placeholders})", row)
    print(args.destination)


if __name__ == "__main__":
    main()
