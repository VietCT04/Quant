from __future__ import annotations

import asyncio
import hashlib
import io
import json
import zipfile
from datetime import date
from pathlib import Path

import polars as pl
import pytest
from aiohttp import web

from marketdata import MarketDataset
from marketdata.aggregate import (
    fill_bars, quarantine_unobserved_minutes, reconcile, sparse_bars, validate_bars,
)
from marketdata.model import Archive, DAY, VERSION, millis
from marketdata.network import Downloader, HTTP, IntegrityError, Metrics, RawBudget, session
from marketdata.pipeline import Acquisition, Config, coverage_rows
from marketdata.state import Manifest

DAY1 = date(2026, 10, 1)
START = millis(DAY1)
TRADE_HEADER = "agg_trade_id,price,quantity,first_trade_id,last_trade_id,transact_time,is_buyer_maker"
KLINE_HEADER = "open_time,open,high,low,close,volume,close_time,quote_volume,count,taker_buy_volume,taker_buy_quote_volume,ignore"


def zipped(rows, header=None):
    sink = io.BytesIO()
    lines = ([header] if header else []) + [",".join(map(str, row)) for row in rows]
    with zipfile.ZipFile(sink, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("data.csv", "\n".join(lines) + "\n")
    return sink.getvalue()


def aggregate(tmp_path, rows, header=TRADE_HEADER, listing=0):
    z = tmp_path / "trades.zip"
    z.write_bytes(zipped(rows, header))
    return sparse_bars(z, START, START + DAY, START, START + DAY, listing, 5000, 128, tmp_path, 2**28)


def test_ohlcv_flows_chunk_and_time_boundaries(tmp_path):
    rows = [
        (1, 10, 2, 11, 12, START, "FALSE"),
        (2, 13, 3, 13, 15, START + 1, "True"),
        (3, 8, 5, 16, 17, START + 4999, "false"),
        (4, 11, 7, 18, 20, START + 5000, "true"),
    ]
    bars, info = aggregate(tmp_path, rows)
    assert info.records == 4
    first, second = bars.to_dicts()
    assert (first["open"], first["high"], first["low"], first["close"]) == (10, 13, 8, 8)
    assert first["volume"] == 10 and first["quote_volume"] == 99
    assert first["buy_volume"] == 7 and first["sell_volume"] == 3
    assert first["buy_quote_volume"] == 60 and first["sell_quote_volume"] == 39
    assert first["agg_trade_count"] == 3 and first["trade_count"] == 7
    assert first["max_trade_qty"] == 5
    assert first["buy_trade_count"] == 4 and first["sell_trade_count"] == 3
    assert second["timestamp_ms"] == START + 5000


def test_no_header_microseconds_and_duplicate_run(tmp_path):
    row = (1, 2, 4, 2, 3, START * 1000 + 4_999_999, 0)
    rows = [row] * 7 + [(2, 3, 5, 4, 4, (START + 5000) * 1000, 1)]
    bars, info = aggregate(tmp_path, rows, header=None)
    assert info.duplicates == 6
    assert info.timestamp_scale == 1000
    assert bars["timestamp_ms"].to_list() == [START, START + 5000]
    assert bars["trade_count"].to_list() == [2, 1]


def test_out_of_order_and_id_tiebreaker(tmp_path):
    rows = [
        (3, 9, 1, 23, 23, START + 3100, True),
        (2, 8, 1, 22, 22, START + 3100, False),
        (1, 7, 1, 21, 21, START + 4, False),
    ]
    bars, info = aggregate(tmp_path, rows)
    assert info.sorted_externally
    assert bars["open"].to_list() == [7]
    assert bars["close"].to_list() == [9]


def test_bad_fields_conflicting_duplicates_and_listing(tmp_path):
    orig = (1, 2, 4, 1, 1, START, False)
    with pytest.raises(ValueError, match="Conflicting"):
        aggregate(tmp_path, [orig, (1, 3, *orig[2:])])
    bars, info = aggregate(tmp_path, [orig, (2, 3, 4, 2, 2, START + 1, True)], listing=START + 1)
    assert bars["open"].to_list() == [3]
    assert info.pre_listing_rows_excluded == 1
    with pytest.raises(ValueError, match="Unsupported CSV header"):
        aggregate(tmp_path, [orig], header="unknown,price,qty,first_trade_id,last_trade_id,time,maker")
    with pytest.raises(ValueError, match="Overlapping"):
        aggregate(tmp_path, [orig, (2, 3, *orig[2:])])
    with pytest.raises(ValueError, match="timestamp units"):
        aggregate(tmp_path, [(1, 2, 4, 1, 1, 17, False)])


def test_seed_fill_and_gap_without_lookahead(tmp_path):
    bars, _ = aggregate(tmp_path, [
        (1, 15, 1, 1, 1, START + 5000, False),
        (2, 20, 1, 2, 2, START + 125000, True),
    ])
    minutes = pl.DataFrame({"timestamp_ms": [START, START + 120000]})
    result = fill_bars(bars, minutes, 5000, START, START + 180000, 0, seed=10)
    validate_bars(result, 5000, START, START + 180000, 0)
    assert result["close"].to_list()[:3] == [10, 15, 15]
    assert result["vwap"].to_list()[:3] == [None, 15, None]
    assert result["is_empty"].to_list()[:3] == [True, False, True]
    assert coverage_rows(result, START, START + 180000, 5000)[1] == [
        START + 60_000, START + 125_000, "no_verified_trading",
    ]


def test_independent_positive_minutes_are_quarantined(tmp_path):
    bars, _ = aggregate(tmp_path, [
        (1, 15, 1, 1, 1, START + 1000, False),
        (2, 15, 1, 2, 2, START + 240000, False),
    ])
    k = pl.DataFrame({
        "timestamp_ms": [START + i * 60000 for i in range(5)],
        **{f: [15.0] * 5 for f in ("open", "high", "low", "close", "quote_volume", "buy_quote_volume")},
        "volume": [1.] * 5, "buy_volume": [1.] * 5, "trade_count": pl.Series([1] * 5, dtype=pl.UInt64),
    })
    result = reconcile(bars, k, START, START + 300000)
    assert result["missing_agg_minutes"] == 3
    _, _, holes = quarantine_unobserved_minutes(bars, k, START, START + 300000, result)
    assert holes == [[START, START + 300000]]


@pytest.fixture
async def archive_server(monkeypatch):
    from marketdata import model

    requests, entries, broken, bad_hash = [], {}, set(), set()

    async def handle(request):
        path = request.path
        checksum = path.endswith(".CHECKSUM")
        zip_path = path.removesuffix(".CHECKSUM")
        requests.append((request.method, path, request.headers.get("Range")))
        if zip_path not in entries:
            return web.Response(status=404)
        body = entries[zip_path]
        if checksum:
            digest = ("0" * 64 if zip_path in bad_hash else hashlib.sha256(body).hexdigest())
            return web.Response(text=f"{digest}  {Path(zip_path).name}\n")
        if request.method == "HEAD":
            return web.Response(headers={"Content-Length": str(len(body))})
        header = request.headers.get("Range")
        if header:
            offset = int(header[6:-1])
            return web.Response(status=206, body=body[offset:],
                                headers={"Content-Range": f"bytes {offset}-{len(body) - 1}/{len(body)}"})
        if zip_path in broken:
            broken.remove(zip_path)
            response = web.StreamResponse(headers={"Content-Length": str(len(body))})
            await response.prepare(request)
            await response.write(body[:len(body) // 2])
            await asyncio.sleep(.05)
            request.transport.abort()
            return response
        return web.Response(body=body)

    app = web.Application()
    app.router.add_route("*", "/{rest:.*}", handle)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]
    monkeypatch.setattr(model, "BASE", f"http://127.0.0.1:{port}")
    try:
        yield requests, entries, broken, bad_hash
    finally:
        await runner.cleanup()


def raw_path(spec):
    from urllib.parse import urlparse
    return urlparse(spec.url).path


async def test_interruption_range_and_atomic_publication(tmp_path, archive_server):
    requests, entries, broken, _ = archive_server
    spec = Archive("TESTUSDT", DAY1)
    entries[raw_path(spec)] = zip_data = zipped([(1, 5, 2, 2, 2, START, False)])
    broken.add(raw_path(spec))
    metrics = Metrics()
    budget = RawBudget(tmp_path, 1000000, metrics)
    async with session(2) as client:
        result = await Downloader(HTTP(client, metrics, rate=1000), budget, metrics).fetch(spec)
    assert result.path.read_bytes() == zip_data
    assert [h for _, _, h in requests if h] == [f"bytes={len(zip_data)//2}-"]
    assert not result.path.with_name(result.path.name + ".part").exists()
    assert metrics.retries > 0
    await budget.release(result.path, True)
    assert not result.path.exists()


async def test_checksum_failure_is_never_published(tmp_path, archive_server):
    _, entries, _, bad_hash = archive_server
    spec = Archive("TESTUSDT", DAY1)
    entries[raw_path(spec)] = zipped([(1, 5, 2, 2, 2, START, False)])
    bad_hash.add(raw_path(spec))
    metrics = Metrics()
    budget = RawBudget(tmp_path, 1000000, metrics)
    async with session(2) as client:
        with pytest.raises(IntegrityError, match="SHA-256 mismatch"):
            await Downloader(HTTP(client, metrics, rate=1000), budget, metrics).fetch(spec)
    assert not (tmp_path / spec.filename).exists()


def fixture_archives(entries, monthly=False):
    spec = Archive("TESTUSDT", DAY1, monthly=monthly)
    entries[raw_path(spec)] = zipped([(1, 5, 2, 2, 2, START, False)], TRADE_HEADER)
    entries[raw_path(Archive("TESTUSDT", DAY1, monthly=monthly, kline=True))] = zipped(
        [(START, 5, 5, 5, 5, 2, START + 59999, 10, 1, 2, 10, 0)], KLINE_HEADER
    )


def configure(state, end):
    state.set_plan({
        "start": str(DAY1), "end": end, "interval_ms": 5000, "symbols": {"TESTUSDT": START},
        "universe_hash": None, "schema_version": VERSION,
    })


async def test_durable_resume_and_missing_data(tmp_path, archive_server):
    requests, entries, _, _ = archive_server
    fixture_archives(entries)
    db = Manifest(tmp_path)
    try:
        configure(db, "2026-10-03")
        cfg = Config(display=False, rate=1000)
        assert (await Acquisition(db, cfg).run())["statuses"] == {"incomplete": 1}
        n = len(requests)
        assert (await Acquisition(db, cfg).run())["input_bytes"] == 0
        assert len(requests) == n
        assert (await Acquisition(db, Config(display=False, rate=1000, retry_missing=True)).run())["input_bytes"] == 0
        assert len(requests) == n + 1
        data = MarketDataset(tmp_path / "binance/um/bars_5s")
        aligned = next(data.iter_aligned(["TESTUSDT"], START, START + 65_000, ["is_empty"]))[1]["TESTUSDT"]
        assert aligned["present"].to_pylist() == [True] * 12 + [False]
        assert aligned["is_empty"].to_pylist() == [False] + [True] * 11 + [None]
        assert data.load(["TESTUSDT"], START, START + 5000, ["available_at_ms"])[0][0].as_py() == START + 5100
        assert "missing_source" in data.coverage(["TESTUSDT"], START, START + DAY * 2)["status"].to_pylist()
    finally:
        db.close()


async def test_incremental_update_carries_close_and_reuses_prefix(tmp_path, archive_server):
    from datetime import timedelta

    requests, entries, _, _ = archive_server
    first, second = Archive("TESTUSDT", DAY1), Archive("TESTUSDT", DAY1 + timedelta(days=1))
    last_minute = START + DAY - 60_000
    entries[raw_path(first)] = zipped([(1, 5, 2, 2, 2, last_minute, False)], TRADE_HEADER)
    entries[raw_path(Archive(first.symbol, first.start, kline=True))] = zipped(
        [(last_minute, 5, 5, 5, 5, 2, last_minute + 59999, 10, 1, 2, 10, 0)], KLINE_HEADER
    )
    l2 = START + DAY
    entries[raw_path(second)] = zipped([(2, 6, 3, 3, 3, l2 + 5000, True)], TRADE_HEADER)
    entries[raw_path(Archive(second.symbol, second.start, kline=True))] = zipped(
        [(l2, 6, 6, 6, 6, 3, l2 + 59999, 18, 1, 0, 0, 0)], KLINE_HEADER
    )
    db = Manifest(tmp_path)
    try:
        configure(db, "2026-10-02")
        cfg = Config(display=False, rate=1000)
        assert (await Acquisition(db, cfg).run())["statuses"] == {"complete": 1}
        boundary = len(requests)
        configure(db, "2026-10-03")
        assert (await Acquisition(db, cfg).run())["statuses"] == {"complete": 1}
        assert all("2026-10-02" in route for _, route, _ in requests[boundary:])
        data = MarketDataset(tmp_path / "binance/um/bars_5s")
        table = data.load(["TESTUSDT"], l2, l2 + 10000, ["close", "is_empty"])
        assert table.to_pydict() == {"close": [5.0, 6.0], "is_empty": [True, False]}
    finally:
        db.close()


async def test_monthly_does_not_download_daily_overlap(tmp_path, archive_server):
    requests, entries, _, _ = archive_server
    fixture_archives(entries, monthly=True)
    fixture_archives(entries, monthly=False)
    db = Manifest(tmp_path)
    try:
        configure(db, "2026-11-01")
        result = await Acquisition(db, Config(display=False, rate=1000)).run()
        assert result["statuses"] == {"complete": 1}
        assert result["bars"] == 12
        assert all("/monthly/" in route for _, route, _ in requests)
        row = json.loads(db.partitions()[0]["result"])
        assert len(row["sources"]) == 1 and row["output_sha256"]
    finally:
        db.close()


async def test_conflicting_monthly_ids_fall_back_to_daily(tmp_path, archive_server):
    _, entries, _, _ = archive_server
    fixture_archives(entries, monthly=True)
    fixture_archives(entries, monthly=False)
    monthly = Archive("TESTUSDT", DAY1, monthly=True)
    entries[raw_path(monthly)] = zipped(
        [(1, 5, 2, 2, 2, START, False), (1, 6, 2, 2, 2, START, False)], TRADE_HEADER
    )
    db = Manifest(tmp_path)
    try:
        configure(db, "2026-11-01")
        result = await Acquisition(db, Config(display=False, rate=1000)).run()
        assert result["statuses"] == {"incomplete": 1}
        assert result["bars"] == 12
        row = json.loads(db.partitions()[0]["result"])
        assert "Conflicting records" in row["fallbacks"][0]["reason"]
        assert row["sources"][0]["archive_url"] == Archive("TESTUSDT", DAY1).url
        assert (tmp_path / "temp" / "raw" / monthly.filename).exists()
    finally:
        db.close()


def test_frozen_crypto_ranking_uses_explicit_metadata():
    from marketdata.liquid50 import select_crypto

    metadata = {}
    for s in ("GOLDTOKENUSDT", "BTCUSDT", "HIGHUSDT", "UNKNOWNUSDT"):
        metadata[s] = {"contractType": "PERPETUAL", "underlyingType": "COIN",
                       "underlyingSubType": ["Crypto"], "status": "TRADING",
                       "marginAsset": "USDT", "quoteAsset": "USDT"}
    metadata["HIGHUSDT"]["contractType"] = "TRADIFI_PERPETUAL"
    metadata["UNKNOWNUSDT"]["underlyingSubType"] = []
    ranking = [{"symbol": s, "quote_volume_usdt": v, "rank": i} for i, (s, v) in enumerate(
        [("HIGHUSDT", "100"), ("UNKNOWNUSDT", "90"), ("GOLDTOKENUSDT", "9.1"), ("BTCUSDT", "9.0")], 1
    )]
    selected, uncertain = select_crypto(ranking[::-1], metadata, limit=2)
    assert [(x["symbol"], x["rank"], x["original_rank"]) for x in selected] == [
        ("GOLDTOKENUSDT", 1, 3), ("BTCUSDT", 2, 4)
    ]
    assert uncertain == ["UNKNOWNUSDT"]


async def test_add_symbols_and_acquire_only_requested_partitions(tmp_path, archive_server):
    from marketdata.model import write_json

    requests, entries, _, _ = archive_server
    fixture_archives(entries)
    db = Manifest(tmp_path)
    try:
        configure(db, "2026-10-02")
        original = db.plan()
        extension = original | {"symbols": original["symbols"] | {"EXTRAUSDT": START}}
        with pytest.raises(ValueError, match="Symbol extension"):
            db.set_plan(extension)
        with pytest.raises(ValueError, match="Symbol extension"):
            db.set_plan(original | {"symbols": {"EXTRAUSDT": START}}, extend_symbols=True)
        db.set_plan(extension, extend_symbols=True)
        await Acquisition(db, Config(display=False, rate=1000)).run({"TESTUSDT"})
        assert db.summary() == {"complete": 1, "pending": 1}
        assert all("EXTRAUSDT" not in route for _, route, _ in requests)
        write_json(tmp_path / "metadata/universes/liquid50.json", {"symbols": [{"symbol": "TESTUSDT"}]})
        dataset = MarketDataset(tmp_path / "binance/um/bars_5s", universe="liquid50")
        assert dataset.load(None, START, START + 5000, ["symbol"])["symbol"].to_pylist() == ["TESTUSDT"]
        with pytest.raises(ValueError, match="configured universe"):
            dataset.load(["EXTRAUSDT"], START, START + 5000)
    finally:
        db.close()
