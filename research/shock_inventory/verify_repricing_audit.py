"""Reconcile an exact short-window repricing audit to native fills and source bars."""

import argparse
from datetime import UTC, datetime
import json
from pathlib import Path

import numpy as np
import polars as pl

from quant.control_cli import request


def check(port, original_id, replay_id):
    original, audit = (request(port, f"/hypotheses/{key}/results")["experiments"][0]
                       for key in (original_id, replay_id))
    if original["status"] != "completed" or audit["status"] != "completed":
        raise ValueError("Both the original and exact audit must be completed")
    expected = original["outcome"]["metrics"]
    report = audit["outcome"]["metrics"]
    for key in ("net_pnl", "roi", "fills", "per_symbol", "end", "position_lifecycles", "daily_marked_equity"):
        if report[key] != expected[key]:
            raise ValueError(f"Audit report differs from the original in {key}")
    artifacts = {Path(path).stem: path for path in audit["outcome"]["artifacts"]}
    fills = pl.read_parquet(artifacts["fills"])
    ordinary = fills.filter(pl.col("reason") != 21)
    if not ordinary.height:
        raise ValueError("No ordinary executed fills to verify")
    source = Path(request(port, "/service")["coverage"]["source"])
    checked = []
    for (index,), ledger in ordinary.group_by("symbol"):
        symbol = report["symbols"][index]
        months = {datetime.fromtimestamp(ms / 1000, UTC).strftime("year=%Y/month=%m") for ms in ledger["ms"]}
        paths = [str(path) for month in months for path in sorted((source / f"symbol={symbol}" / month).glob("*.parquet"))]
        bars = (pl.scan_parquet(paths).select("timestamp_ms", "open", "quote_volume", "is_empty")
                .filter(pl.col("timestamp_ms").is_in(ledger["ms"].implode())).collect())
        rows = ledger.join(bars, left_on="ms", right_on="timestamp_ms", how="left", validate="m:1")
        if rows["open"].null_count() or rows["is_empty"].any():
            raise ValueError(f"{symbol} has a fill without an audited nonempty open")
        np.testing.assert_array_equal(rows["ms"], rows["decision"])
        np.testing.assert_allclose(rows["reference"], rows["open"], rtol=1e-10, atol=1e-12)
        np.testing.assert_allclose(rows["price"], rows["open"] * (1 + np.sign(rows["filled"]) * .0002),
                                   rtol=1e-10, atol=1e-12)
        booked = rows["filled"].abs() * rows["price"]
        np.testing.assert_allclose(rows["fee"], booked * .0005, rtol=1e-10, atol=1e-12)
        checked.extend((booked / rows["quote_volume"]).to_list())
    measured = np.asarray(checked)
    return {"original": original["id"], "audit": audit["id"], "net_pnl": report["net_pnl"],
            "roi": report["roi"], "verified_fills": len(checked),
            "orders_exceeding_whole_bar_traded_notional": int(np.count_nonzero(measured > 1)),
            "median_fill_to_bar_volume": float(np.median(measured)),
            "max_fill_to_bar_volume": float(np.max(measured))}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("original")
    parser.add_argument("audit")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--out", type=Path, required=True)
    options = parser.parse_args()
    result = check(options.port, options.original, options.audit)
    text = json.dumps(result, indent=2)
    options.out.write_text(text, encoding="utf8")
    print(text)


if __name__ == "__main__":
    main()
