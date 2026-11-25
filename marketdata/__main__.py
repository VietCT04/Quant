from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import shutil
import sys
from contextlib import closing
from datetime import UTC, date, datetime
from pathlib import Path

os.environ.setdefault("POLARS_MAX_THREADS", str(min(8, os.cpu_count() or 1)))

import numpy as np
import polars as pl
import pyarrow.parquet as pq

from .aggregate import SCHEMA, validate_bars
from .model import DAY, FAPI, VERSION, load_json, millis, partition_path, resolution, sha256, today, write_json, years_ago
from .network import HTTP, Metrics, session
from .pipeline import Acquisition, Config
from .state import Manifest, writer_lock
from .universe import save_exchange, snapshot_universe

GIB = 1024 ** 3


def positive(value):
    number = int(value)
    if number <= 0:
        raise argparse.ArgumentTypeError("must be positive")
    return number


def window(value):
    if not re.fullmatch(r"[1-9]\d*d", value):
        raise argparse.ArgumentTypeError("use a number of UTC days, e.g. 30d")
    return int(value[:-1])


def acquisition_options(parser):
    parser.add_argument("--workers", type=positive, default=16)
    parser.add_argument("--processors", type=positive, default=3)
    parser.add_argument("--memory-gib", type=positive, default=4)
    parser.add_argument("--temp-budget-gib", type=positive, default=8)
    parser.add_argument("--disk-budget-gib", type=positive)
    parser.add_argument("--temp-dir", type=Path)
    parser.add_argument("--request-rate", type=positive, default=20)
    parser.add_argument("--keep-raw", action="store_true")
    parser.add_argument("--retry-missing", action="store_true")
    parser.add_argument("--no-dashboard", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--resume", action="store_true", help="resume is also the default")


def parser():
    p = argparse.ArgumentParser(description="Read-only Binance USD-M archive pipeline")
    p.add_argument("--data-dir", type=Path, default=Path("data"), help="dataset root before the subcommand")
    commands = p.add_subparsers(dest="command", required=True)
    u = commands.add_parser("universe", help="freeze current active contracts ranked by USD-M quote volume")
    u.add_argument("--top", type=positive, default=50)
    u.add_argument("--volume-window", type=window, default=30)
    u.add_argument("--end", type=date.fromisoformat, help="last UTC midnight, exclusive")
    plan = commands.add_parser("plan", help="persist a dated partition plan")
    plan.add_argument("--start", type=date.fromisoformat)
    plan.add_argument("--end", type=date.fromisoformat, help="last UTC midnight, exclusive")
    plan.add_argument("--years", type=positive, default=2)
    plan.add_argument("--interval", default="5s")
    plan.add_argument("--symbols", nargs="+")
    plan.add_argument("--exchange-snapshot", type=Path, help="optional saved exchangeInfo for custom lists")
    plan.add_argument("--dry-run", action="store_true")
    acquisition_options(commands.add_parser("download", help="download, process, validate and resume a plan"))
    update = commands.add_parser("update", help="extend the frozen plan to the latest completed UTC day")
    update.add_argument("--end", type=date.fromisoformat)
    acquisition_options(update)
    commands.add_parser("validate", help="audit durable output and regenerate coverage and validation reports")
    commands.add_parser("status", help="show persisted partition progress")
    return p


def dump(value):
    print(json.dumps(value, ensure_ascii=True, indent=2), flush=True)


async def create_plan(root, args):
    end = args.end or today()
    start = args.start or years_ago(end, args.years)
    if not start < end <= today():
        raise ValueError("Use an increasing UTC date range ending at or before today.")
    interval = resolution(args.interval)
    frozen = root / "metadata" / "universe.json"
    universe = load_json(frozen) if frozen.is_file() else None
    if not args.symbols:
        if not universe:
            raise ValueError("Freeze the universe first, or provide --symbols.")
        listing = {row["symbol"]: row["listing_timestamp_ms"] for row in universe["symbols"]}
        digest = universe["selection_sha256"]
    else:
        if args.exchange_snapshot:
            exchange = load_json(args.exchange_snapshot)
        elif universe:
            exchange = load_json(root / universe["exchange_snapshot"])
        else:
            async with session(2) as client:
                exchange = await HTTP(client, Metrics()).body(FAPI + "/exchangeInfo", json=True)
        available = {row["symbol"]: row for row in exchange["symbols"]}
        symbols = list(dict.fromkeys(s for item in args.symbols for s in item.split(",") if s))
        for symbol in symbols:
            row = available.get(symbol)
            if not row or row["contractType"] not in {"PERPETUAL", "TRADIFI_PERPETUAL"} or row["marginAsset"] != "USDT":
                raise ValueError(f"{symbol} is absent from the USD-M USDT perpetual metadata")
        listing = {s: int(available[s]["onboardDate"]) for s in symbols}
        digest = None
        if not args.dry_run and not universe:
            from datetime import UTC, datetime
            save_exchange(root, exchange, datetime.now(UTC).isoformat())
    return {
        "schema_version": VERSION, "interval_ms": interval, "start": str(start), "end": str(end),
        "universe_hash": digest, "symbols": listing,
    }


def validate_outputs(manifest):
    plan = manifest.plan()
    interval = plan["interval_ms"]
    errors, audited = [], 0
    previous = {}
    for task in manifest.partitions():
        if task["status"] not in {"complete", "qualified", "incomplete"}:
            continue
        result = json.loads(task["result"])
        path = partition_path(manifest.root, task["symbol"], task["month"], interval)
        start, end = millis(date.fromisoformat(task["start"])), millis(date.fromisoformat(task["end"]))
        coverage = [segment for source in result["sources"] for segment in source["segments"]]
        try:
            if (not coverage or coverage[0][0] != start or coverage[-1][1] != end
                    or any(row[1] != nxt[0] for row, nxt in zip(coverage, coverage[1:]))):
                raise ValueError("Non-contiguous manifest coverage segments")
            if sum((hi - lo) // interval for lo, hi, reason in coverage if reason == "covered") != result["bars"]:
                raise ValueError("Manifest coverage or row count mismatch")
            if result["bars"] == 0:
                continue
            covered = [(lo, hi) for lo, hi, reason in coverage if reason == "covered"]
            starts, ends = np.asarray(covered, dtype=np.int64).T
            if not path.exists() or sha256(path) != result["output_sha256"]:
                raise ValueError("Output file missing or output SHA-256 changed")
            with pq.ParquetFile(path) as file:
                schema = file.schema_arrow
                if not schema.equals(SCHEMA, check_metadata=False) or schema.metadata.get(b"interval_ms") != str(interval).encode() or schema.metadata.get(b"schema_version") != VERSION.encode():
                    raise ValueError("Output schema/version mismatch")
                if file.metadata.num_rows != result["bars"]:
                    raise ValueError("Parquet row count mismatch")
                for batch in file.iter_batches(batch_size=65_536):
                    frame = pl.from_arrow(batch)
                    validate_bars(frame, interval, start, end, task["listing"])
                    times = frame["timestamp_ms"].to_numpy()
                    positions = np.searchsorted(starts, times, side="right") - 1
                    if (positions < 0).any() or (times >= ends[positions]).any():
                        raise ValueError("Output timestamp lies outside the manifest's covered intervals")
                    last = previous.get(task["symbol"])
                    first = frame.row(0, named=True)
                    if last and (first["timestamp_ms"] <= last["timestamp_ms"]
                            or first["is_empty"] and first["timestamp_ms"] == last["timestamp_ms"] + interval
                            and first["close"] != last["close"]):
                        raise ValueError("Cross-batch or cross-partition chronology/fill mismatch")
                    previous[task["symbol"]] = frame.row(-1, named=True)
            audited += 1
        except (OSError, ValueError) as exc:
            errors.append((task["symbol"], task["month"], str(exc)))
            manifest.partition(task, "failed", result | {"file_audit_error": str(exc)})
    audit = {"audited_partitions": audited, "audit_errors": errors, "finished_at_utc": datetime.now(UTC).isoformat()}
    totals = manifest.report(audit)
    return audit | {"status": manifest.summary(), "totals": totals}


def capacity(manifest, args, through=None):
    plan = manifest.plan()
    # Stale output remains on disk during the atomic replacement of one
    # partition per processor. Sixty-four bytes per slot is a planning default.
    tasks = manifest.partitions()
    unbuilt = [t for t in tasks if t["status"] not in {"complete", "qualified"}]
    slots = sum((millis(date.fromisoformat(t["end"])) - millis(date.fromisoformat(t["start"])))
                // plan["interval_ms"] for t in unbuilt)
    if through is not None and str(through) > plan["end"]:
        extra_start = millis(date.fromisoformat(plan["end"]))
        slots += sum(max(0, millis(through) - max(extra_start, listing // DAY * DAY)) // plan["interval_ms"]
                     for listing in plan["symbols"].values())
    bytes_per_slot = 64
    estimate_path = manifest.root / "metadata" / "benchmark_report.json"
    if estimate_path.exists():
        bytes_per_slot = max(bytes_per_slot, load_json(estimate_path).get("conservative_parquet_bytes_per_bar", 64))
    forecast = int(slots * bytes_per_slot)
    tmp = args.temp_dir or manifest.root / "temp"
    tmp.mkdir(parents=True, exist_ok=True)
    shared_disk = os.stat(manifest.root).st_dev == os.stat(tmp).st_dev
    current = sum(json.loads(t["result"]).get("bytes", 0) for t in tasks if t["result"])
    reserve = args.temp_budget_gib * GIB
    output_free, raw_free = shutil.disk_usage(manifest.root).free, shutil.disk_usage(tmp).free
    total_budget = args.disk_budget_gib * GIB if args.disk_budget_gib else None
    if (forecast + (reserve if shared_disk else 0) > output_free or reserve > raw_free
            or total_budget is not None and current + forecast + reserve > total_budget):
        raise OSError(f"Storage preflight failed: estimated output {forecast:,} bytes plus {reserve:,} temp bytes; "
                      f"output disk free {output_free:,} bytes, raw disk free {raw_free:,} bytes.")
    return {"unbuilt_partitions": len(unbuilt), "upper_bound_unbuilt_bars": slots, "estimated_output_bytes": forecast,
            "reserved_temp_bytes": reserve, "output_disk_free_bytes": output_free, "raw_disk_free_bytes": raw_free}


def main(argv=None):
    args = parser().parse_args(argv)
    root = args.data_dir.resolve()
    if args.command == "universe":
        with writer_lock(root):
            dump(asyncio.run(snapshot_universe(root, args.top, args.volume_window, args.end)))
        return 0
    if args.command == "plan" and args.dry_run:
        dump(asyncio.run(create_plan(root, args)))
        return 0
    if args.command == "status":
        with closing(Manifest(root)) as state:
            dump({"plan": state.plan(), "status": state.summary(), "published": state.published_totals()})
        return 0
    if args.command in {"download", "update"} and args.dry_run:
        with closing(Manifest(root)) as state:
            through = (args.end or today()) if args.command == "update" else None
            if through is not None and not date.fromisoformat(state.plan()["end"]) <= through <= today():
                raise ValueError("Update must maintain or extend the plan through a completed UTC day.")
            dump(capacity(state, args, through))
        return 0

    with writer_lock(root), closing(Manifest(root)) as state:
        if args.command == "plan":
            plan = asyncio.run(create_plan(root, args))
            dump(plan | {"symbol_months": state.set_plan(plan)})
            return 0
        if args.command == "validate":
            result = validate_outputs(state)
            dump(result)
            return int(bool(result["audit_errors"] or result["status"].get("failed") or result["status"].get("pending")))
        if args.command == "update":
            plan = state.plan()
            end = args.end or today()
            if not date.fromisoformat(plan["end"]) <= end <= today():
                raise ValueError("Update must maintain or extend the plan through a completed UTC day.")
            state.set_plan(plan | {"end": str(end)})
        dump(capacity(state, args))
        config = Config(workers=args.workers, processors=args.processors, memory=args.memory_gib * GIB,
                        temp=args.temp_dir.resolve() if args.temp_dir else None,
                        raw_budget=args.temp_budget_gib * GIB, keep_raw=args.keep_raw,
                        display=not args.no_dashboard, retry_missing=args.retry_missing or args.command == "update",
                        rate=args.request_rate)
        metrics = asyncio.run(Acquisition(state, config).run())
        write_json(root / "metadata" / "last_run.json", metrics)
        dump(metrics)
        return int(bool(metrics["statuses"].get("failed") or metrics["statuses"].get("incomplete")))


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (OSError, RuntimeError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        sys.exit(1)
