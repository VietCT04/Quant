"""Report per-symbol reconstruction coverage using a live archive or a frozen SQLite copy."""

import argparse
from contextlib import closing
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


def main():
    args = argparse.ArgumentParser(description=__doc__)
    args.add_argument("--archive", type=Path, default=ARCHIVE)
    args.add_argument("--baseline", type=Path)
    args.add_argument("--out", type=Path, required=True)
    options = args.parse_args()
    baseline = json.loads(options.baseline.read_text(encoding="utf-8")) if options.baseline else None
    options.out.write_text(json.dumps(audit(options.archive, baseline), indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
