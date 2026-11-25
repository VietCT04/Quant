"""Add and audit the crypto-only universe derived from the frozen ranking."""

import argparse
import asyncio
import csv
from collections import Counter, defaultdict
from contextlib import closing
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pyarrow.parquet as pq

from .__main__ import acquisition_options, capacity, dump, validate_outputs, GIB
from .model import load_json, partition_path, replace_file, sha256, write_json
from .pipeline import Acquisition, Config
from .state import Manifest, writer_lock


def select_crypto(ranking, metadata, limit=50):
    selected, uncertain = [], []
    for rank in sorted(ranking, key=lambda r: (-Decimal(r["quote_volume_usdt"]), r["symbol"])):
        contract = metadata[rank["symbol"]]
        tags = contract.get("underlyingSubType", [])
        if contract["contractType"] == "TRADIFI_PERPETUAL" or "TradFi" in tags:
            continue
        if (contract["contractType"] != "PERPETUAL" or contract["underlyingType"] != "COIN" or "Crypto" not in tags
                or contract["status"] != "TRADING" or contract["marginAsset"] != "USDT" or contract["quoteAsset"] != "USDT"):
            uncertain.append(rank["symbol"])
            continue
        selected.append(rank | {"original_rank": rank["rank"], "rank": len(selected) + 1,
                                "asset_classification": "Crypto", "underlying_type": contract["underlyingType"],
                                "underlying_subtypes": tags})
        if len(selected) == limit:
            return selected, uncertain
    raise ValueError(f"Frozen ranking contains fewer than {limit} explicitly classified crypto contracts")


def prepare(root, state):
    meta = root / "metadata"
    baseline = load_json(meta / "universe.json")
    if (baseline["window_start"], baseline["window_end_exclusive"]) != ("2026-10-26", "2026-11-25"):
        raise ValueError("Liquid 50 requires the frozen November 25, 2026 ranking")
    plan = state.plan()
    if (plan["start"], plan["end"], plan["interval_ms"]) != ("2024-11-25", "2026-11-25", 5000):
        raise ValueError("Liquid 50 requires the original historical date range and five-second interval")
    ranked_path = meta / "universe_rankings.json"
    exchange = root / baseline["exchange_snapshot"]
    contracts = {row["symbol"]: row for row in load_json(exchange)["symbols"]}
    rows, uncertain = select_crypto(load_json(ranked_path), contracts)
    original = {row["symbol"] for row in baseline["symbols"]}
    additions = [row["symbol"] for row in rows if row["symbol"] not in original]
    universe = {
        "name": "Liquid 50", "selection_timestamp_utc": baseline["selection_timestamp_utc"],
        "window_start": baseline["window_start"], "window_end_exclusive": baseline["window_end_exclusive"],
        "ranking_source": "metadata/universe_rankings.json", "ranking_sha256": sha256(ranked_path),
        "exchange_snapshot": baseline["exchange_snapshot"], "exchange_sha256": sha256(exchange),
        "bias": "Current active contracts and recent quote-volume ranks introduce survivorship and historical selection bias. This is a fixed universe.",
        "uncertain_classifications_before_cutoff": uncertain, "added_symbols": additions, "symbols": rows,
    }
    universe_path = meta / "universes" / "liquid50.json"
    if universe_path.exists() and load_json(universe_path) != universe:
        raise ValueError("Existing Liquid 50 configuration differs from the frozen inputs")
    listings = dict(plan["symbols"])
    for row in rows:
        symbol, listing = row["symbol"], int(contracts[row["symbol"]]["onboardDate"])
        if listing != row["listing_timestamp_ms"] or listings.get(symbol, listing) != listing:
            raise ValueError(f"Listing timestamp disagreement for {symbol}")
        listings[symbol] = listing
    if listings != plan["symbols"]:
        state.set_plan(plan | {"symbols": listings}, extend_symbols=True)
    if not universe_path.exists():
        write_json(universe_path, universe)
    return universe


def report(root):
    meta = root / "metadata"
    output = meta / "universes"
    universe = load_json(output / "liquid50.json")
    validation = load_json(meta / "validation_report.json")
    selected = {x["symbol"] for x in universe["symbols"]}
    interval = validation["plan"]["interval_ms"]
    slots, gaps, parts = defaultdict(Counter), [], defaultdict(list)
    stamp = lambda t: datetime.fromtimestamp(t / 1000, UTC).isoformat()
    for part in validation["partitions"]:
        if part["symbol"] in selected:
            parts[part["symbol"]].append(part)
    for entry in pq.read_table(meta / "coverage.parquet", filters=[("symbol", "in", sorted(selected))]).to_pylist():
        count = (entry["end_ms"] - entry["start_ms"]) // interval
        slots[entry["symbol"]][entry["status"]] += count
        if entry["status"] != "covered":
            gaps.append({**entry, "start_utc": stamp(entry["start_ms"]), "end_utc_exclusive": stamp(entry["end_ms"]), "slots": count})
    rows = []
    for member in universe["symbols"]:
        symbol = member["symbol"]
        bars, size, first = 0, 0, None
        for part in parts[symbol]:
            if part["status"] in {"pending", "failed"} or not part.get("bars"):
                continue
            path = partition_path(root, symbol, part["month"], interval)
            footer = pq.read_metadata(path)
            earliest = footer.row_group(0).column(0).statistics.min
            first = earliest if first is None else min(first, earliest)
            if footer.num_rows != part["bars"] or path.stat().st_size != part["bytes"]:
                raise ValueError(f"Parquet footer disagrees with manifest for {path}")
            bars += footer.num_rows
            size += path.stat().st_size
        planned = sum(slots[symbol].values())
        if bars != slots[symbol]["covered"]:
            raise ValueError(f"Coverage disagrees with Parquet row counts for {symbol}")
        rows.append({
            "rank": member["rank"], "symbol": symbol, "asset_classification": member["asset_classification"],
            "quote_volume_usdt": member["quote_volume_usdt"], "listing_utc": member["listing_utc"],
            "first_bar_utc": stamp(first) if first is not None else None, "bars": bars,
            "coverage_pct": 100 * bars / planned if planned else None, "parquet_bytes": size,
        })
    for name, entries in (("liquid50_symbols.csv", rows), ("liquid50_gaps.csv", gaps)):
        if entries:
            target = output / name
            staged = target.with_suffix(".part")
            with staged.open("w", encoding="utf-8", newline="") as stream:
                writer = csv.DictWriter(stream, fieldnames=list(entries[0]))
                writer.writeheader()
                writer.writerows(entries)
            replace_file(staged, target)
    runs_path = output / "liquid50_acquisition.json"
    runs = load_json(runs_path) if runs_path.exists() else []
    summary = {
        "symbols": rows, "archive_payload_bytes": sum(r["input_bytes"] for r in runs),
        "acquisition_seconds": sum(r["elapsed_seconds"] for r in runs),
        "peak_sampled_rss_bytes": max((r["memory_peak"] for r in runs), default=None),
        "bars": sum(r["bars"] for r in rows), "parquet_bytes": sum(r["parquet_bytes"] for r in rows),
        "coverage_slots": dict(sum(slots.values(), Counter())),
        "partition_statuses": dict(Counter(x["status"] for group in parts.values() for x in group)),
        "output_audit": validation.get("output_audit"),
    }
    write_json(output / "liquid50_report.json", summary)
    return {k: v for k, v in summary.items() if k != "symbols"}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, default=Path("data"))
    parser.add_argument("--prepare-only", action="store_true")
    parser.add_argument("--report-only", action="store_true")
    acquisition_options(parser)
    args = parser.parse_args()
    if args.dry_run:
        parser.error("Use --prepare-only to freeze and schedule the universe without downloading")
    root = args.data_dir.resolve()
    if args.report_only:
        with writer_lock(root):
            dump(report(root))
        return 0
    with writer_lock(root), closing(Manifest(root)) as state:
        universe = prepare(root, state)
        dump({"selected": len(universe["symbols"]), "added_symbols": universe["added_symbols"]})
        if args.prepare_only:
            return 0
        dump(capacity(state, args))
        config = Config(workers=args.workers, processors=args.processors, memory=args.memory_gib * GIB,
                        temp=args.temp_dir, raw_budget=args.temp_budget_gib * GIB, keep_raw=args.keep_raw,
                        display=not args.no_dashboard, retry_missing=args.retry_missing, rate=args.request_rate)
        if universe["added_symbols"]:
            metrics = asyncio.run(Acquisition(state, config).run(set(universe["added_symbols"])))
            path = root / "metadata" / "universes" / "liquid50_acquisition.json"
            runs = load_json(path) if path.exists() else []
            write_json(path, [*runs, metrics])
            dump(metrics)
        result = validate_outputs(state)
        dump(result)
        dump(report(root))
        return int(bool(result["audit_errors"] or result["status"].get("failed") or result["status"].get("pending")))


if __name__ == "__main__":
    raise SystemExit(main())
