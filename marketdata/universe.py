from __future__ import annotations

import asyncio
import hashlib
import json
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

from .model import DAY, FAPI, load_json, millis, replace_file, symbol_name, today, write_json
from .network import HTTP, Metrics, session


def eligible(info):
    return (info["status"] == "TRADING"
            and info["contractType"] in {"PERPETUAL", "TRADIFI_PERPETUAL"}
            and info["quoteAsset"] == info["marginAsset"] == "USDT")


def save_exchange(root: Path, exchange, as_of: str):
    meta = root / "metadata"
    stamp = as_of.replace(":", "").replace(".", "-")
    snapshot = meta / "exchange" / f"{stamp}.json"
    write_json(snapshot, exchange)
    symbol_rows = []
    for item in exchange["symbols"]:
        symbol_rows.append({
            "symbol": item["symbol"], "pair": item.get("pair"),
            "contract_type": item["contractType"], "status": item["status"],
            "base_asset": item["baseAsset"], "quote_asset": item["quoteAsset"],
            "margin_asset": item["marginAsset"], "listing_timestamp_ms": int(item["onboardDate"]),
            "delivery_timestamp_ms": item.get("deliveryDate"), "observed_at_utc": as_of,
            "metadata_json": json.dumps(item, ensure_ascii=False, separators=(",", ":")),
        })
    staged = meta / "symbols.parquet.part"
    pq.write_table(pa.Table.from_pylist(symbol_rows), staged, compression="zstd")
    replace_file(staged, meta / "symbols.parquet")
    return snapshot.relative_to(root).as_posix()


async def snapshot_universe(root: Path, top: int, window: int, end: date | None = None, exchange_path=None):
    target = root / "metadata" / "universe.json"
    if top < 1 or not 1 <= window <= 499:
        raise ValueError("Universe size must be positive and ranking window must be 1–499 days.")
    if target.exists():
        previous = load_json(target)
        if ((top, window) != (previous["top"], previous["volume_window_days"])
                or end is not None and end.isoformat() != previous["window_end_exclusive"]):
            raise ValueError("A universe is already frozen here. Select a new data directory for a different universe.")
        return previous
    end = end or today()
    if end > today():
        raise ValueError("Ranking end must be at or before the current UTC date.")
    start = end - timedelta(days=window)
    as_of = datetime.now(UTC).isoformat(timespec="milliseconds")

    async with session(12) as client:
        http = HTTP(client, Metrics(), rate=15)
        exchange = load_json(exchange_path) if exchange_path else await http.body(FAPI + "/exchangeInfo", json=True)
        snapshot = save_exchange(root, exchange, as_of)
        candidates = [item for item in exchange["symbols"] if eligible(item)]
        throttle = asyncio.Semaphore(12)
        completed = 0

        async def rank_volume(item):
            nonlocal completed
            symbol = symbol_name(item["symbol"])
            listing = int(item["onboardDate"])
            if listing >= millis(end):
                return None
            async with throttle:
                values = await http.body(FAPI + "/klines", params={
                    "symbol": symbol, "interval": "1d", "limit": window,
                    "startTime": millis(start), "endTime": millis(end) - 1,
                }, json=True)
            if not isinstance(values, list):
                raise ValueError(f"Invalid ranking response for {symbol}: {values}")
            opened = [int(row[0]) for row in values]
            if opened != sorted(set(opened)) or any(
                ts % DAY or not millis(start) <= ts < millis(end) for ts in opened
            ):
                raise ValueError(f"Invalid ranking-window kline timestamps for {symbol}")
            if any(len(row) != 12 or int(row[6]) != int(row[0]) + DAY - 1 for row in values):
                raise ValueError(f"Inconsistent ranking-window kline schema for {symbol}")
            volumes = [Decimal(row[7]) for row in values]
            if any(not vol.is_finite() or vol < 0 for vol in volumes):
                raise ValueError(f"Invalid Binance quote volume for {symbol}")
            expected = (millis(end) - max(millis(start), listing // DAY * DAY)) // DAY
            completed += 1
            if completed % 100 == 0:
                print(f"Ranked {completed}/{len(candidates)} eligible symbols", flush=True)
            return {
                "symbol": symbol, "quote_volume_usdt": str(sum(volumes, Decimal(0))),
                "listing_timestamp_ms": listing,
                "listing_utc": datetime.fromtimestamp(listing / 1000, UTC).isoformat(),
                "contract_type": item["contractType"], "status": item["status"],
                "daily_klines_received": len(values), "days_without_returned_kline": expected - len(values),
            }

        results = await asyncio.gather(*(rank_volume(item) for item in candidates))
    ranked = sorted((row for row in results if row is not None),
                    key=lambda row: (-Decimal(row["quote_volume_usdt"]), row["symbol"]))
    if len(ranked) < top:
        raise ValueError(f"Only {len(ranked)} eligible listings available for a top-{top} universe")
    for i, row in enumerate(ranked, 1):
        row["rank"] = i
    write_json(root / "metadata" / "universe_rankings.json", ranked)
    universe = {
        "selection_timestamp_utc": as_of, "exchange_snapshot": snapshot,
        "rank_source": FAPI + "/klines?interval=1d", "top": top, "volume_window_days": window,
        "window_start": start.isoformat(), "window_end_exclusive": end.isoformat(),
        "eligible_candidates": len(ranked), "symbols": ranked[:top],
        "bias": "Current active-contract selection excludes contracts delisted before the snapshot and uses future information for historical simulations.",
    }
    universe["selection_sha256"] = hashlib.sha256(
        json.dumps(universe, sort_keys=True, ensure_ascii=False).encode("utf-8")
    ).hexdigest()
    write_json(target, universe)
    return universe
