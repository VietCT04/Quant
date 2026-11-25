"""Acquire public USD-M mark-price candles and settlement events."""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import io
import math
import time
from datetime import date
from pathlib import Path
from zipfile import ZipFile

import polars as pl
import numpy as np

from marketdata.model import BASE, DAY, FAPI, MINUTE, dates, load_json, millis, next_month, replace_file, utc_day, write_json
from marketdata.network import HTTP, IntegrityError, Metrics, MissingArchive, session
from marketdata.state import writer_lock

MARK = {"available_ms": pl.Int64, "price": pl.Float64}
FUNDING = {"timestamp_ms": pl.Int64, "rate": pl.Float64, "price": pl.Float64}


def save(frame, path):
    path.parent.mkdir(parents=True, exist_ok=True)
    staged = path.with_suffix(".part")
    frame.write_parquet(staged, compression="zstd")
    replace_file(staged, path)


def parse_mark(blob, source_start, source_end):
    with ZipFile(io.BytesIO(blob)) as z:
        entries = [f for f in z.infolist() if not f.is_dir()]
        if len(entries) != 1 or entries[0].file_size > 32 * 1024**2:
            raise IntegrityError("Unexpected mark ZIP members")
        raw = z.read(entries[0])
    header = not raw.split(b",", 1)[0].strip().isdigit()
    df = pl.read_csv(io.BytesIO(raw), has_header=header, columns=[0, 4, 6],
                     new_columns=["open_ms", "price", "close_ms"]).cast(
                         {"open_ms": pl.Int64, "price": pl.Float64, "close_ms": pl.Int64})
    if (df.null_count().row(0) != (0, 0, 0) or not df["open_ms"].is_sorted()
            or df["open_ms"].n_unique() != df.height or df.filter(
                (pl.col("open_ms") < source_start) | (pl.col("open_ms") >= source_end)
                | (pl.col("open_ms") % MINUTE != 0) | (pl.col("close_ms") != pl.col("open_ms") + MINUTE - 1)
                | ~pl.col("price").is_finite() | (pl.col("price") <= 0)).height):
        raise IntegrityError("Invalid mark timestamps, prices or coverage")
    return df.select((pl.col("close_ms") + 101).alias("available_ms"), "price")


async def mark_archive(http, symbol, start, monthly):
    tag = start.isoformat()[:7] if monthly else start.isoformat()
    name = f"{symbol}-1m-{tag}.zip"
    kind = "monthly" if monthly else "daily"
    url = f"{BASE}/{kind}/markPriceKlines/{symbol}/1m/{name}"
    checksum = (await http.body(url + ".CHECKSUM")).decode("utf-8").split()
    if len(checksum) != 2 or checksum[1].lstrip("*") != name:
        raise IntegrityError(f"Invalid Binance checksum record for {url}")
    blob = await http.body(url)
    digest = hashlib.sha256(blob).hexdigest()
    if digest != checksum[0].lower():
        raise IntegrityError(f"SHA-256 mismatch for {url}")
    end = millis(next_month(start)) if monthly else millis(start) + DAY
    table = await asyncio.to_thread(parse_mark, blob, millis(start), end)
    return table, {"url": url, "sha256": digest, "bytes": len(blob), "rows": table.height}


async def marks_for(http, root, symbol, first, end, log):
    month = first.replace(day=1)
    while month < end:
        stop = next_month(month)
        path = root / "marks" / symbol / f"{month:%Y-%m}.parquet"
        receipt = path.with_suffix(".json")
        if path.exists() and receipt.exists() and (saved := load_json(receipt))["complete"]:
            log.append(saved)
        else:
            frames, sources, missing = [], [], []
            start = max(first, month)
            finish = min(stop, end)
            if stop <= end:
                try:
                    frame, src = await mark_archive(http, symbol, month, True)
                    frames.append(frame)
                    sources.append(src)
                except MissingArchive:
                    pass
            if not frames:
                for day in dates(start, finish):
                    try:
                        frame, src = await mark_archive(http, symbol, day, False)
                        frames.append(frame)
                        sources.append(src)
                    except MissingArchive:
                        missing.append(str(day))
            frame = pl.concat(frames) if frames else pl.DataFrame(schema=MARK)
            frame = frame.filter(pl.col("available_ms").is_between(
                millis(start) + MINUTE + 100, millis(finish) + 100))
            expected = (millis(finish) - millis(start)) // MINUTE
            if frame.height:
                await asyncio.to_thread(save, frame, path)
            saved = {"symbol": symbol, "month": f"{month:%Y-%m}", "sources": sources, "missing_days": missing,
                     "rows": frame.height, "expected_minutes_from_listing_day": expected,
                     "complete": not missing and frame.height == expected}
            write_json(receipt, saved)
            log.append(saved)
        month = stop


async def funding_for(http, root, symbol, start, end):
    path = root / "funding" / f"{symbol}.parquet"
    receipt = path.with_suffix(".json")
    if path.exists() and receipt.exists():
        saved = load_json(receipt)
        if saved["start_ms"] == start and saved["end_ms_exclusive"] == end:
            return saved
    hits, requests = [], 0
    cursor = start
    while cursor < end:
        payload = await http.body(FAPI + "/fundingRate", json=True,
                                  params={"symbol": symbol, "startTime": cursor, "endTime": end - 1, "limit": 1000})
        requests += 1
        if not isinstance(payload, list):
            raise IntegrityError(f"Unexpected funding response for {symbol}")
        if not payload:
            break
        times = [int(row["fundingTime"]) for row in payload]
        if times != sorted(set(times)) or not cursor <= times[0] <= times[-1] < end:
            raise IntegrityError(f"Nonmonotonic or out-of-range settlements for {symbol}")
        for ts, row in zip(times, payload):
            rate = float(row["fundingRate"])
            mark = float(row["markPrice"]) if row.get("markPrice") else None
            if row["symbol"] != symbol or not math.isfinite(rate) or mark is not None and (not math.isfinite(mark) or mark < 0):
                raise IntegrityError(f"Invalid funding row for {symbol}")
            hits.append((ts, rate, mark or None))
        cursor = times[-1] + 1
        if len(payload) < 1000:
            break
    frame = pl.DataFrame(hits, schema=FUNDING, orient="row")
    await asyncio.to_thread(save, frame, path)
    boundaries = [start, *(r[0] for r in hits), end]
    max_gap = max(b - a for a, b in zip(boundaries, boundaries[1:]))
    info = {"symbol": symbol, "source": FAPI + "/fundingRate", "rows": len(hits), "requests": requests,
            "first_ms": hits[0][0] if hits else None, "last_ms": hits[-1][0] if hits else None,
            "start_ms": start, "end_ms_exclusive": end, "max_gap_ms": max_gap,
            "coverage_warning": max_gap > 12 * 60 * MINUTE or not hits or frame["price"].null_count() > 0,
            "missing_settlement_price_ms": frame.filter(pl.col("price").is_null())["timestamp_ms"].to_list()}
    write_json(receipt, info)
    return info


async def acquire(data):
    from marketdata.reader import MarketDataset
    dataset = MarketDataset(data / "binance/um/bars_5s", universe="liquid50")
    u = load_json(dataset.meta / "universes" / "liquid50.json")
    plan = load_json(dataset.meta / "plan.json")
    start, end = map(date.fromisoformat, (plan["start"], plan["end"]))
    root = data / "binance" / "um" / "simulator_aux"
    marks, settlements, errors = [], [], []
    beginning = time.perf_counter()
    with writer_lock(root):
        async with session(18) as client:
            marks_http, funding_http = HTTP(client, Metrics(), rate=18), HTTP(client, Metrics(), rate=1.25)
            permits = asyncio.Semaphore(14)

            async def one_mark(row):
                async with permits:
                    try:
                        await marks_for(marks_http, root, row["symbol"],
                                        max(start, utc_day(row["listing_timestamp_ms"])), end, marks)
                    except Exception as exc:
                        errors.append({"symbol": row["symbol"], "kind": "marks", "error": str(exc)})
                    print(f'marks {row["symbol"]}', flush=True)

            async def one_funding(row):
                try:
                    settlements.append(await funding_for(funding_http, root, row["symbol"],
                        max(millis(start), row["listing_timestamp_ms"]), millis(end)))
                except Exception as exc:
                    errors.append({"symbol": row["symbol"], "kind": "funding", "error": str(exc)})

            await asyncio.gather(*(job(row) for row in u["symbols"] for job in (one_mark, one_funding)))
        report = {"start": str(start), "end_exclusive": str(end), "mark_months": sorted(marks, key=lambda x: (x["symbol"], x["month"])),
                  "funding": sorted(settlements, key=lambda x: x["symbol"]), "errors": errors,
                  "elapsed_seconds": time.perf_counter() - beginning}
        write_json(root / "inventory.json", report)
    print(f"{len(marks)} mark months, {len(settlements)} funding symbols, {len(errors)} errors", flush=True)
    return int(bool(errors))


async def repair_marks(data):
    """Reconcile partially published monthly mark files against daily ZIPs."""
    root = data / "binance" / "um" / "simulator_aux"
    meta = data / "metadata"
    u = load_json(meta / "universes" / "liquid50.json")
    listing = {r["symbol"]: r["listing_timestamp_ms"] for r in u["symbols"]}
    beginning = time.perf_counter()
    with writer_lock(root):
        path = root / "inventory.json"
        inventory = load_json(path)
        start, end = map(date.fromisoformat, (inventory["start"], inventory["end_exclusive"]))
        async with session(12) as client:
            http = HTTP(client, Metrics(), rate=12)
            permits = asyncio.Semaphore(12)

            async def repair(entry):
                if entry["complete"]:
                    return
                async with permits:
                    symbol = entry["symbol"]
                    month = date.fromisoformat(entry["month"] + "-01")
                    mark_path = root / "marks" / symbol / f"{month:%Y-%m}.parquet"
                    current = pl.read_parquet(mark_path) if mark_path.exists() else pl.DataFrame(schema=MARK)
                    first = max(millis(max(start, month)), ((listing[symbol] + MINUTE - 1) // MINUTE) * MINUTE)
                    last = millis(min(end, next_month(month)))
                    expected = np.arange(first + MINUTE + 100, last + 101, MINUTE, dtype="i8")
                    missing = np.setdiff1d(expected, current["available_ms"].to_numpy(), assume_unique=True)
                    frames = [current]
                    for day in sorted({utc_day(int(t) - MINUTE - 100) for t in missing}):
                        try:
                            frame, src = await mark_archive(http, symbol, day, False)
                            frame = frame.filter(pl.col("available_ms").is_in(missing.tolist()))
                            frames.append(frame)
                            entry["sources"].append(src | {"purpose": "fill missing monthly mark candles"})
                        except MissingArchive:
                            continue
                    combined = pl.concat(frames).sort("available_ms")
                    unseen = np.setdiff1d(expected, combined["available_ms"].to_numpy(), assume_unique=True)
                    if combined.height != current.height:
                        await asyncio.to_thread(save, combined, mark_path)
                    entry.update(rows=combined.height, missing_minutes=len(unseen),
                                 expected_post_listing_minutes=len(expected), complete=not len(unseen))
                    entry["missing_days"] = sorted({str(utc_day(int(t) - MINUTE - 100)) for t in unseen})
                    write_json(mark_path.with_suffix(".json"), entry)

            await asyncio.gather(*(repair(entry) for entry in inventory["mark_months"]))
        inventory["mark_repair_seconds"] = time.perf_counter() - beginning
        write_json(path, inventory)
    print("remaining incomplete mark partitions", sum(not row["complete"] for row in inventory["mark_months"]))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", type=Path, default=Path("data"))
    parser.add_argument("--repair-marks", action="store_true")
    args = parser.parse_args()
    if args.repair_marks:
        asyncio.run(repair_marks(args.data))
    else:
        raise SystemExit(asyncio.run(acquire(args.data)))
