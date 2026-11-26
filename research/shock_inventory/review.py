"""Evidence checkpoints for the inventory campaign. Raw outcomes stay in the local registry."""

import argparse
import asyncio
from collections import Counter
import json
from pathlib import Path

import numpy as np
import polars as pl

from quant.control_cli import request
from quant.explorer_ledger import reconstruct
from research.phase5.review import wait_for_outcomes


def summarize(trial):
    outcome = trial["outcome"] or {}
    m = outcome.get("metrics")
    if not m:
        return {"job": trial["id"], "status": trial["status"], "failure": outcome.get("failure")}
    spec = trial["specification"]
    elapsed = (spec["end_ms"] - spec["start_ms"]) / 86400000
    execution = m["execution_diagnostics"]
    gross = m["gross_pnl_before_execution_cost_fees_and_funding"]
    costs = m["execution_costs"]
    count = m["completed_positions"]
    details = m["position_lifecycles"]
    symbol_pnl = {s: v["net_closed"] for s, v in details["per_symbol"].items()}
    return {
        "job": trial["id"], "status": trial["status"], "parameters": spec["parameters"],
        "roi": m["roi"], "sharpe": m["sharpe"], "drawdown": m["maximum_drawdown"],
        "net": m["net_pnl"], "gross": gross, "executions": execution["filled"],
        "lifecycles": count, "executions_per_day": execution["filled"] / elapsed,
        "lifecycles_per_day": count / elapsed, "fills_per_lifecycle": execution["filled"] / count if count else None,
        "holding_ms": details["holding_ms"], "mean_fill_notional": execution["filled_notional"] / execution["filled"]
        if execution["filled"] else None,
        "peak_gross": m["risk"]["peak_gross"], "costs": costs,
        "fees_over_positive_gross": costs["fees"] / gross if gross > 0 else None,
        "friction_over_positive_gross": (costs["fees"] + costs["spread_slippage_impact"]) / gross if gross > 0 else None,
        "rejections": execution["reasons"], "unavailable_decisions": execution["not_ready_or_unavailable_decisions"],
        "monthly": m["monthly_marked_pnl"], "daily": m["daily_marked_equity"],
        "top_symbol_pnl": sorted(symbol_pnl.items(), key=lambda x: x[1], reverse=True)[:5],
        "winning_symbols": sum(x > 0 for x in symbol_pnl.values()),
        "funding_warnings": m["funding_warnings"],
    }


def quantiles(values):
    values = list(values)
    return dict(zip(("p10", "p50", "p90", "max"), np.quantile(values, (.1, .5, .9, 1)).tolist())) if values else {}


def audit_summary(trial):
    outcome = trial["outcome"]
    files = {Path(s).stem: s for s in outcome["artifacts"]}
    metrics = outcome["metrics"]
    fills = pl.read_parquet(files["fills"])
    funding = pl.read_parquet(files["funding"])
    trace = pl.read_parquet(files["strategy_trace"], columns=["symbol", "available_ms", "phase_code"]).drop_nulls()
    trace = {(r["symbol"], r["available_ms"]): int(r["phase_code"]) for r in trace.iter_rows(named=True)
             if np.isfinite(r["phase_code"])}
    positions, exit_reasons = [], Counter()
    for i, symbol in enumerate(metrics["symbols"]):
        per_symbol = metrics["per_symbol"][i]
        events = fills.filter(pl.col("symbol") == i).to_dicts()
        cashflows = funding.filter(pl.col("symbol") == i).to_dicts()
        ledger, _, _ = reconstruct(symbol, events, cashflows, per_symbol, trial["specification"]["end_ms"])
        for item in ledger:
            entries = [e for e in item["timeline"] if e.get("allocation") == "open"]
            exits = [e for e in item["timeline"] if e.get("allocation") == "close"]
            if not exits:
                continue
            last = exits[-1]
            why = 21 if last["reason"] == 21 else trace.get((i, last["decision"]))
            row = {"id": item["id"], "symbol": symbol,
                   "entry_utc_ms": item["entry_ms"], "exit_utc_ms": item["exit_ms"],
                   "entries": len(entries), "exits": len(exits), "minutes": item["duration_ms"] / 60_000,
                   "exit_phase": why, "net_pnl": item["net_pnl"], "fees": item["fees"],
                   "opening_notional": item["entry_value"],
                   "peak_inventory_at_execution_prices": max(abs(e["after"]) * e["price"] for e in entries + exits)}
            positions.append(row)
            if item["net_pnl"] < 0:
                exit_reasons[why] += item["net_pnl"]
    won = sorted((x for x in positions if x["net_pnl"] > 0), key=lambda x: x["net_pnl"])
    lost = sorted((x for x in positions if x["net_pnl"] < 0), key=lambda x: x["net_pnl"])
    equity = pl.read_parquet(files["equity"], columns=["gross", "equity"]).filter(pl.col("equity") > 0)
    return {"completed_positions": len(positions), "entry_counts": quantiles(x["entries"] for x in positions),
            "exit_counts": quantiles(x["exits"] for x in positions), "holding_minutes": quantiles(x["minutes"] for x in positions),
            "order_sizes": quantiles((fills["filled"].abs() * fills["price"]).to_list()),
            "opened_notional": quantiles(x["opening_notional"] for x in positions),
            "fill_sampled_inventory": quantiles(x["peak_inventory_at_execution_prices"] for x in positions),
            "mean_hourly_utilization": equity.select((pl.col("gross") / pl.col("equity")).mean()).item(),
            "negative_pnl_by_exit_phase": dict(exit_reasons),
            "median_profitable": won[len(won) // 2] if won else None,
            "largest_loss": lost[0] if lost else None,
            "rationale_codes": {"3": "distributed", "4": "fair value", "5": "adverse stop",
                                "6": "expiry", "7": "failed recovery", "21": "virtual terminal"}}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("folder", type=Path)
    parser.add_argument("--port", type=int, default=8791)
    parser.add_argument("--audit", action="store_true")
    parser.add_argument("--events", action="store_true")
    args = parser.parse_args()
    ticket = json.loads((args.folder / "receipt.json").read_text())
    ids = set(ticket["submission_ids"])
    if args.events:
        asyncio.run(wait_for_outcomes(args.port, ids))
    inbox = request(args.port, "/inbox?unreviewed=true")
    details = {key: request(args.port, f"/hypotheses/{key}/results") for key in sorted(ids)}
    jobs = {key: [summarize(trial) for trial in detail["experiments"]] for key, detail in details.items()}
    record = {"jobs": jobs, "found_unreviewed": sorted(row["id"] for row in inbox if row["id"] in ids)}
    if args.audit:
        record["audit"] = {key: [audit_summary(trial) for trial in detail["experiments"] if trial["status"] == "completed"]
                           for key, detail in details.items()}
    (args.folder / "evidence.json").write_text(json.dumps(record, indent=2, allow_nan=False), encoding="utf8")
    compact = {key: [{k: v for k, v in job.items() if k not in {"daily", "unavailable_decisions"}}
                     for job in entries] for key, entries in jobs.items()}
    print(json.dumps({"jobs": compact, "audit": record.get("audit")}))


if __name__ == "__main__":
    main()
