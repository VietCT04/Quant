"""Run official sample acquisitions, estimate capacity, optionally launch the frozen plan."""
from __future__ import annotations

import argparse
import asyncio
import json
import shutil
from contextlib import closing
from dataclasses import asdict
from datetime import date, timedelta
from pathlib import Path

from .__main__ import validate_outputs
from .model import Archive, DAY, load_json, millis, next_month, today, write_json
from .network import HTTP, Metrics, MissingArchive, session
from .pipeline import Acquisition, Config
from .state import Manifest, writer_lock


async def sample_archive_sizes(plan, month):
    last = today() - timedelta(days=2)
    stop = next_month(month)
    lock = asyncio.Semaphore(8)
    async with session(8) as client:
        http = HTTP(client, Metrics(), rate=12)

        async def size(item):
            symbol, listing = item
            async with lock:
                if listing < millis(stop):
                    archive = Archive(symbol, month, monthly=True)
                    try:
                        n = await http.size(archive.url)
                        days = (millis(stop) - max(millis(month), listing // DAY * DAY)) / DAY
                        return symbol, (n / days, archive.url, n)
                    except MissingArchive:
                        pass
                if listing >= millis(last + timedelta(days=1)):
                    return symbol, None
                archive = Archive(symbol, last)
                try:
                    n = await http.size(archive.url)
                    return symbol, (n, archive.url, n)
                except MissingArchive:
                    return symbol, None

        return dict(await asyncio.gather(*(size(item) for item in plan["symbols"].items())))


def estimate(root, bench_root, args):
    with closing(Manifest(root)) as manifest:
        plan = manifest.plan()
    with closing(Manifest(bench_root / "daily")) as m:
        daily = validate_outputs(m)
    with closing(Manifest(bench_root / "monthly")) as m:
        monthly = validate_outputs(m)
    for label, report in (("daily", daily), ("monthly", monthly)):
        if (report["audit_errors"] or set(report["status"]) - {"complete", "qualified"}
                or report["audited_partitions"] != 3):
            raise ValueError(f"{label} benchmarks did not pass qualified structural and daily-total validation: {report}")

    d, m = (load_json(bench_root / kind / "metadata" / "benchmark_metrics.json") for kind in ("daily", "monthly"))
    sizing = asyncio.run(sample_archive_sizes(plan, args.month))
    missing = [symbol for symbol, sample in sizing.items() if sample is None]
    usable = [value[0] for value in sizing.values() if value is not None]
    if not usable:
        raise ValueError("No archive sizes could be verified for a full-run estimate")
    raw_est, slots = 0, 0
    days_by_symbol = {}
    for symbol, listing in plan["symbols"].items():
        days = max(0, (millis(date.fromisoformat(plan["end"])) -
                       max(millis(date.fromisoformat(plan["start"])), listing // DAY * DAY)) // DAY)
        days_by_symbol[symbol] = days
        rate = sizing[symbol][0] if sizing[symbol] else sorted(usable)[len(usable) // 2]
        raw_est += days * rate
        slots += days * DAY // plan["interval_ms"]
    # Measuring bytes/bar across each dataset bounds the forecast without
    # assuming that small-order TradFi symbols compress like BTC.
    per_bar = max(d["output_bytes"] / d["bars"], m["output_bytes"] / m["bars"])
    forecast_bytes = int(slots * per_bar)
    conservative = int(per_bar * 1.3 + 1)
    elapsed_network_lower_bound = raw_est / max(d["input_bytes"] / d["elapsed_seconds"],
                                                m["input_bytes"] / m["elapsed_seconds"])
    report = {
        "daily": d, "monthly": m, "daily_audit": daily, "monthly_audit": monthly,
        "monthly_size_probe": {s: (None if result is None else {
            "archive": result[1], "bytes": result[2], "bytes_per_day": result[0],
        }) for s, result in sizing.items()},
        "symbol_days": days_by_symbol, "symbols_without_size_probe": missing,
        "estimated_archive_download_bytes": int(raw_est), "estimated_maximum_bar_count": slots,
        "estimated_parquet_bytes": forecast_bytes, "parquet_budget_bytes": slots * conservative,
        "conservative_parquet_bytes_per_bar": conservative,
        "rough_elapsed_seconds_at_observed_payload_rate": int(elapsed_network_lower_bound),
        "volume_estimate_notes": "Recent archive sizes extrapolated per symbol; missing probes use observed median. Actual historical activity and latency vary.",
        "space_available_bytes": shutil.disk_usage(root).free,
    }
    write_json(root / "metadata" / "benchmark_report.json", report)
    return report


def run_samples(root, bench, args):
    plan = load_json(root / "metadata" / "plan.json")
    listing = plan["symbols"]
    samples = (
        ("daily", args.day, args.day + timedelta(days=1), ("BTCUSDT", "ETHUSDT", "LINKUSDT")),
        ("monthly", args.month, next_month(args.month), ("BTCUSDT", "LINKUSDT", "XAUUSDT")),
    )
    for label, start, end, symbols in samples:
        target = bench / label
        with writer_lock(target), closing(Manifest(target)) as state:
            state.set_plan(plan | {
                "start": str(start), "end": str(end), "interval_ms": 5000,
                "symbols": {name: listing[name] for name in symbols},
            })
            metrics = asyncio.run(Acquisition(state, Config(display=False)).run())
            write_json(target / "metadata" / "benchmark_metrics.json", metrics)
            print(label, json.dumps(metrics), flush=True)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--data-dir", type=Path, default=Path("data"))
    p.add_argument("--bench-dir", type=Path, default=Path("bench-data"))
    p.add_argument("--day", type=date.fromisoformat, required=True)
    p.add_argument("--month", type=date.fromisoformat, required=True)
    p.add_argument("--reuse-measurements", action="store_true")
    p.add_argument("--launch", action="store_true")
    args = p.parse_args()
    if args.month.day != 1:
        p.error("--month must be the first day of a completed UTC calendar month")
    if not args.reuse_measurements:
        run_samples(args.data_dir, args.bench_dir, args)
    report = estimate(args.data_dir, args.bench_dir, args)
    print(json.dumps({k: v for k, v in report.items() if k.startswith(("estimated", "rough", "space"))}, indent=2),
          flush=True)
    if args.launch:
        from .__main__ import main as run
        return run(["--data-dir", str(args.data_dir), "download", "--workers", "16", "--no-dashboard"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
