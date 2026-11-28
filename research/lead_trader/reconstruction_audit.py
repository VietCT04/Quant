"""Report per-symbol reconstruction coverage using a live archive or a frozen SQLite copy."""

import argparse
from contextlib import closing
import csv
from datetime import UTC, datetime
import json
from pathlib import Path
from types import SimpleNamespace

from quant.replay_workspace import ARCHIVE, ReplayWorkspace


def audit(archive, baseline=None):
    reader = ReplayWorkspace(SimpleNamespace(source=Path("data/binance/um/bars_5s")), archive)
    revision = reader.archive_version()
    with closing(reader.archive_db()) as db:
        lo, hi = db.execute("SELECT min(ms),max(ms) FROM orders").fetchone()
        positions, = db.execute("SELECT count(*) FROM positions").fetchone()
        symbols = [symbol for symbol, in db.execute("SELECT symbol FROM orders GROUP BY symbol ORDER BY count(*) DESC")]
    stats = {}
    for symbol in symbols:
        result = reader.external_tape(symbol, revision).reconstruction
        row = {"previous": baseline.get(symbol), **result} if baseline else result
        stats[symbol] = row
    return {"archive_revision": revision, "positions": positions,
            "orders_utc": [datetime.fromtimestamp(t / 1000, UTC).isoformat() for t in (lo, hi)],
            "symbols": stats}



def lifecycle_csv(archive, symbol, output):
    reader = ReplayWorkspace(SimpleNamespace(source=Path("data/binance/um/bars_5s")), archive)
    tape = reader.external_tape(symbol, reader.archive_version())
    if not tape.lives:
        raise ValueError(f"No reported position lifecycles for {symbol}")
    clocks = ("entry_ms", "exit_ms", "first_execution_ms", "opening_execution_ms", "report_through_ms")
    with output.open("w", encoding="utf-8", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=sorted({k for row in tape.lives for k in row}) +
                                [key.replace("_ms", "_utc") for key in clocks])
        writer.writeheader()
        for life in tape.lives:
            row = dict(life)
            for key in clocks:
                stamp = row.get(key)
                row[key.replace("_ms", "_utc")] = datetime.fromtimestamp(stamp / 1000, UTC).isoformat() if stamp else None
            row["covered_order_intervals"] = json.dumps(row["covered_order_intervals"])
            writer.writerow(row)

def main():
    args = argparse.ArgumentParser(description=__doc__)
    args.add_argument("--archive", type=Path, default=ARCHIVE)
    args.add_argument("--baseline", type=Path)
    args.add_argument("--lifecycles", metavar="SYMBOL", help="Write one CSV row per reported lifecycle")
    args.add_argument("--out", type=Path, required=True)
    options = args.parse_args()
    if options.lifecycles:
        lifecycle_csv(options.archive, options.lifecycles, options.out)
        return
    baseline = json.loads(options.baseline.read_text(encoding="utf-8")) if options.baseline else None
    options.out.write_text(json.dumps(audit(options.archive, baseline), indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
