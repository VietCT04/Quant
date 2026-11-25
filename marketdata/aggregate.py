from __future__ import annotations

import csv
import io
import re
import tempfile
from dataclasses import field
import zipfile
from dataclasses import dataclass
from pathlib import Path

import duckdb
import polars as pl
import pyarrow as pa
import pyarrow.csv as pcsv
import pyarrow.parquet as pq

from .model import DAY, MINUTE, VERSION, utc_day

TRADE_FIELDS = {
    "id": pa.int64(), "price": pa.float64(), "qty": pa.float64(), "first_id": pa.int64(),
    "last_id": pa.int64(), "ts": pa.int64(), "maker": pa.bool_(),
}
TRADE_ALIASES = (
    ("id", "agg_trade_id", "aggTradeId", "aggregate_trade_id", "a"),
    ("price", "p"), ("qty", "quantity", "q"), ("first_trade_id", "firstTradeId", "f"),
    ("last_trade_id", "lastTradeId", "l"), ("transact_time", "timestamp", "time", "T"),
    ("is_buyer_maker", "isBuyerMaker", "maker", "m"),
)
KLINE_FIELDS = {
    "timestamp_ms": pa.int64(), "open": pa.float64(), "high": pa.float64(), "low": pa.float64(),
    "close": pa.float64(), "volume": pa.float64(), "close_time": pa.int64(),
    "quote_volume": pa.float64(), "trade_count": pa.uint64(), "buy_volume": pa.float64(),
    "buy_quote_volume": pa.float64(), "ignore": pa.float64(),
}
KLINE_ALIASES = (
    ("open_time", "openTime", "timestamp_ms"), ("open",), ("high",), ("low",), ("close",),
    ("volume",), ("close_time", "closeTime"), ("quote_volume", "quote_asset_volume", "quoteAssetVolume"),
    ("count", "trade_count", "number_of_trades"), ("taker_buy_volume", "taker_buy_base_asset_volume"),
    ("taker_buy_quote_volume", "taker_buy_quote_asset_volume"), ("ignore",),
)
FLOATS = ("open", "high", "low", "close", "volume", "quote_volume", "buy_volume", "sell_volume",
          "buy_quote_volume", "sell_quote_volume")
COUNTS = ("agg_trade_count", "trade_count", "buy_trade_count", "sell_trade_count")
SUMS = (*FLOATS[4:], *COUNTS)
SCHEMA = pa.schema(
    [pa.field("timestamp_ms", pa.int64(), False)]
    + [pa.field(f, pa.float64(), False) for f in FLOATS]
    + [pa.field("agg_trade_count", pa.uint32(), False)]
    + [pa.field(f, pa.uint64(), False) for f in COUNTS[1:]]
    + [pa.field("vwap", pa.float64()), pa.field("max_trade_qty", pa.float64(), False),
       pa.field("is_empty", pa.bool_(), False)],
    metadata={b"schema_version": VERSION.encode()},
)
ID_EDGES = ("first_agg_id", "last_agg_id", "first_fill_id", "last_fill_id")
SPARSE_FIELDS = ("timestamp_ms", *FLOATS, *COUNTS, "max_trade_qty", *ID_EDGES)


class DisorderedTrades(Exception):
    pass


class CrossCheckError(ValueError):
    def __init__(self, details):
        self.details = details
        super().__init__(f"Kline reconciliation failed: {details}")


def normalized_name(name):
    return re.sub(r"[^a-z0-9]", "", name.strip().lower())


def csv_batches(path: Path | bytes, *, trades: bool, block: int):
    fields, aliases = (TRADE_FIELDS, TRADE_ALIASES) if trades else (KLINE_FIELDS, KLINE_ALIASES)
    place = path if isinstance(path, Path) else io.BytesIO(path)
    with zipfile.ZipFile(place) as archive:
        entries = [entry for entry in archive.infolist() if not entry.is_dir()]
        if len(entries) != 1 or not entries[0].filename.lower().endswith(".csv"):
            raise ValueError("Archive must contain exactly one CSV member")
        with archive.open(entries[0]) as stream:
            first = stream.readline(16384)
            if not first:
                return
            if len(first) == 16384:
                raise ValueError("CSV first record exceeds the schema limit")
            row = next(csv.reader([first.decode("utf-8-sig").strip()]))
            types = dict(fields)
            if trades and len(row) == 8:
                if row[0].strip().isdigit() or normalized_name(row[-1]) == "isbestmatch":
                    types["best_match"] = pa.bool_()
                    aliases = (*aliases, ("is_best_match", "isBestMatch"))
                elif normalized_name(row[-1]) in {"nq", "normalquantity"}:
                    types["normal_quantity"] = pa.float64()
                    aliases = (*aliases, ("nq", "normal_quantity"))
            if len(row) != len(types):
                raise ValueError(f"Unexpected CSV field count {len(row)}: {row}")
            has_header = not row[0].strip().isdigit()
            if has_header and not all(normalized_name(value) in {normalized_name(a) for a in options}
                                      for value, options in zip(row, aliases, strict=True)):
                raise ValueError(f"Unsupported CSV header: {row}")
            if not has_header:
                stream.seek(3 if first.startswith(b"\xef\xbb\xbf") else 0)
            if stream.peek(1) == b"":
                return
            reader = pcsv.open_csv(
                stream,
                read_options=pcsv.ReadOptions(column_names=list(types), block_size=block, use_threads=False),
                parse_options=pcsv.ParseOptions(ignore_empty_lines=False),
                convert_options=pcsv.ConvertOptions(
                    column_types=types, true_values=["true", "True", "TRUE", "1"],
                    false_values=["false", "False", "FALSE", "0"],
                ),
            )
            for batch in reader:
                frame = pl.from_arrow(batch)
                if any(frame.null_count().row(0)):
                    raise ValueError("Null field in source CSV")
                if "normal_quantity" in frame.columns and frame.select(
                    (~pl.col("normal_quantity").is_finite() | (pl.col("normal_quantity") < 0)
                     | (pl.col("normal_quantity") > pl.col("qty"))).any()
                ).item():
                    raise ValueError("Invalid RPI-excluded normal quantity")
                yield frame


def timestamp_scale(frame, column, start, end):
    low, high = frame[column].min(), frame[column].max()
    matches = [scale for scale in (1, 1000, 1_000_000) if start * scale <= low <= high < end * scale]
    if len(matches) != 1:
        raise ValueError(f"Unexpected or mixed timestamp units/coverage: {low}..{high}, expected {start}..{end}")
    return matches[0]


@dataclass
class TradesInfo:
    records: int = 0
    duplicates: int = 0
    pre_listing_rows_excluded: int = 0
    agg_id_gaps: int = 0
    underlying_id_gaps: int = 0
    first_id: int | None = None
    last_id: int | None = None
    first_underlying: int | None = None
    last_underlying: int | None = None
    timestamp_scale: int | None = None
    sorted_externally: bool = False
    included_min_id: int | None = None
    included_max_id: int | None = None
    included_first_underlying: int | None = None
    included_last_underlying: int | None = None
    last_ts: int | None = None
    id_gap_timestamps_ms: list[list[int]] = field(default_factory=list)


def normalized_batches(path, start, end, listing, block, info):
    for frame in csv_batches(path, trades=True, block=block):
        scale = timestamp_scale(frame, "ts", start, end)
        if info.timestamp_scale not in (None, scale):
            raise ValueError("Timestamp unit changed within one archive")
        info.timestamp_scale = scale
        invalid = (
            (pl.col("id") < 0) | (pl.col("first_id") < 0)
            | (pl.col("last_id") < pl.col("first_id"))
            | ~pl.col("price").is_finite() | ~pl.col("qty").is_finite()
            | (pl.col("price") <= 0) | (pl.col("qty") <= 0)
            | (~(pl.col("price") * pl.col("qty")).is_finite())
        )
        if frame.select(invalid.any()).item():
            raise ValueError(f"Invalid price, quantity or ID range in {path}")
        excluded = frame.select((pl.col("ts") < listing * scale).sum()).item()
        info.pre_listing_rows_excluded += excluded
        if excluded:
            frame = frame.filter(pl.col("ts") >= listing * scale)
        if frame.height:
            yield frame.select(list(TRADE_FIELDS))


def track_ids(frame, info):
    if not frame.height:
        return
    first = frame.row(0, named=True)
    last = frame.row(-1, named=True)
    id_delta = pl.col("id").diff()
    under_delta = pl.col("first_id") - pl.col("last_id").shift()
    gaps, under_gaps, overlaps = frame.select(
        (id_delta > 1).sum().alias("id_gaps"),
        (under_delta > 1).sum().alias("under_gaps"),
        (under_delta <= 0).any().alias("overlap"),
    ).row(0)
    if overlaps or (info.last_underlying is not None and first["first_id"] <= info.last_underlying):
        raise ValueError("Overlapping underlying trade-ID ranges in aggTrades")
    if info.first_id is None:
        info.first_id, info.first_underlying = first["id"], first["first_id"]
    else:
        if first["id"] > info.last_id + 1:
            gaps += 1
            info.id_gap_timestamps_ms.append([info.last_ts // info.timestamp_scale, first["ts"] // info.timestamp_scale])
        under_gaps += first["first_id"] > info.last_underlying + 1
    if gaps:
        broken = frame.with_columns(pl.col("ts").shift().alias("previous_ts")).filter(id_delta > 1)
        remaining = max(0, 1024 - len(info.id_gap_timestamps_ms))
        info.id_gap_timestamps_ms.extend(
            [old // info.timestamp_scale, new // info.timestamp_scale]
            for old, new in broken.select("previous_ts", "ts").head(remaining).iter_rows()
        )
    info.last_id, info.last_underlying = last["id"], last["last_id"]
    info.last_ts = last["ts"]
    info.agg_id_gaps += gaps
    info.underlying_id_gaps += under_gaps
    info.records += frame.height


def ordered_batches(frames, info):
    previous = None
    for frame in frames:
        if previous is not None:
            frame = pl.concat([previous, frame])
        if not frame["id"].is_sorted() or not frame["ts"].is_sorted():
            raise DisorderedTrades
        has_duplicate = frame["id"].diff().min() == 0
        distinct = frame.unique(maintain_order=True) if has_duplicate else frame
        if has_duplicate and distinct["id"].diff().min() == 0:
            raise ValueError("Conflicting records for one aggregate trade ID")
        info.duplicates += frame.height - distinct.height
        previous = distinct.tail(1)
        ready = distinct.head(distinct.height - 1)
        track_ids(ready, info)
        if ready.height:
            yield ready
    if previous is not None:
        track_ids(previous, info)
        yield previous


def external_batches(frames, info, directory, spill_bytes):
    info.sorted_externally = True
    with tempfile.TemporaryDirectory(prefix="sort-", dir=directory) as staging:
        with duckdb.connect(str(Path(staging) / "sort.duckdb")) as db:
            db.execute("SET memory_limit='192MB'; SET threads=2; SET preserve_insertion_order=false")
            db.execute("SET temp_directory = ?", [str(Path(staging) / "spill")])
            db.execute("SET max_temp_directory_size = ?", [f"{spill_bytes}B"])
            consumed = 0
            for frame in frames:
                consumed += frame.height
                if consumed * 130 > spill_bytes:
                    raise ValueError("External-sort input exceeds its disk budget")
                db.register("chunk", frame.to_arrow())
                db.execute("CREATE TABLE IF NOT EXISTS source AS SELECT * FROM chunk LIMIT 0")
                db.execute("INSERT INTO source SELECT * FROM chunk")
                db.unregister("chunk")
            db.execute("CREATE TABLE dedup AS SELECT DISTINCT * FROM source")
            if db.execute("SELECT id FROM dedup GROUP BY id HAVING count(*) > 1 LIMIT 1").fetchone():
                raise ValueError("Conflicting records for one aggregate trade ID")
            info.duplicates = consumed - db.execute("SELECT count(*) FROM dedup").fetchone()[0]
            with db.execute("SELECT * FROM dedup ORDER BY id").to_arrow_reader(100_000) as reader:
                for batch in reader:
                    track_ids(pl.from_arrow(batch), info)
            with db.execute("SELECT * FROM dedup ORDER BY ts, id").to_arrow_reader(100_000) as reader:
                for batch in reader:
                    yield pl.from_arrow(batch)


def sparse_chunk(frame, scale, interval):
    frame = frame.with_columns(
        ((pl.col("ts") // (scale * interval)) * interval).alias("timestamp_ms"),
        (pl.col("price") * pl.col("qty")).alias("quote"),
        (pl.col("last_id").cast(pl.UInt64) - pl.col("first_id").cast(pl.UInt64) + 1).alias("fills"),
    )
    price, qty, quote, fills = (pl.col(c) for c in ("price", "qty", "quote", "fills"))
    buy = ~pl.col("maker")
    sell = pl.col("maker")
    return frame.group_by("timestamp_ms", maintain_order=True).agg(
        price.first().alias("open"), price.max().alias("high"), price.min().alias("low"),
        price.last().alias("close"), qty.sum().alias("volume"), quote.sum().alias("quote_volume"),
        qty.filter(buy).sum().alias("buy_volume"), qty.filter(sell).sum().alias("sell_volume"),
        quote.filter(buy).sum().alias("buy_quote_volume"), quote.filter(sell).sum().alias("sell_quote_volume"),
        pl.len().cast(pl.UInt64).alias("agg_trade_count"), fills.sum().alias("trade_count"),
        fills.filter(buy).sum().alias("buy_trade_count"), fills.filter(sell).sum().alias("sell_trade_count"),
        qty.max().alias("max_trade_qty"),
        pl.col("id").min().alias("first_agg_id"), pl.col("id").max().alias("last_agg_id"),
        pl.col("first_id").min().alias("first_fill_id"), pl.col("last_id").max().alias("last_fill_id"),
    )


def reduce_bars(frame, interval):
    return frame.with_columns(((pl.col("timestamp_ms") // interval) * interval).alias("timestamp_ms")).group_by(
        "timestamp_ms", maintain_order=True
    ).agg(
        pl.col("open").first(), pl.col("high").max(), pl.col("low").min(), pl.col("close").last(),
        *(pl.col(f).sum() for f in SUMS), pl.col("max_trade_qty").max(),
        *(pl.col(edge).min() if edge.startswith("first_") else pl.col(edge).max()
          for edge in ID_EDGES if edge in frame.columns),
    )


def sparse_bars(path, archive_start, archive_end, start, end, listing, interval, block, work, spill_bytes):
    def aggregate(external=False):
        info = TradesInfo()
        raw = normalized_batches(path, archive_start, archive_end, listing, block, info)
        frames = external_batches(raw, info, work, spill_bytes) if external else ordered_batches(raw, info)
        pieces = []
        for frame in frames:
            scale = info.timestamp_scale
            if start != archive_start or end != archive_end:
                frame = frame.filter(pl.col("ts").is_between(start * scale, end * scale, closed="left"))
                if not frame.height:
                    continue
                minimum, maximum, first, last = frame.select(
                    pl.col("id").min().alias("min"), pl.col("id").max().alias("max"),
                    pl.col("first_id").min(), pl.col("last_id").max(),
                ).row(0)
                info.included_min_id = min(info.included_min_id if info.included_min_id is not None else minimum, minimum)
                info.included_max_id = max(info.included_max_id if info.included_max_id is not None else maximum, maximum)
                info.included_first_underlying = min(info.included_first_underlying if info.included_first_underlying is not None else first, first)
                info.included_last_underlying = max(info.included_last_underlying if info.included_last_underlying is not None else last, last)
            bars = sparse_chunk(frame, scale, interval)
            if bars.height:
                pieces.append(bars)
        if start == archive_start and end == archive_end:
            info.included_min_id, info.included_max_id = info.first_id, info.last_id
            info.included_first_underlying, info.included_last_underlying = info.first_underlying, info.last_underlying
        if pieces:
            result = pl.concat(pieces)
            return (reduce_bars(result, interval) if result["timestamp_ms"].is_duplicated().any() else result), info
        empty = pl.from_arrow(pa.Table.from_batches([], schema=SCHEMA)).with_columns(
            *(pl.lit(None, dtype=pl.Int64).alias(edge) for edge in ID_EDGES)
        ).select(SPARSE_FIELDS)
        return empty.with_columns(pl.col("agg_trade_count").cast(pl.UInt64)), info

    try:
        return aggregate()
    except DisorderedTrades:
        return aggregate(external=True)


def gap_boundaries(frame, gaps):
    bounds = []
    for lo, hi in gaps:
        left = frame.filter(pl.col("timestamp_ms") < lo).select("last_agg_id", "last_fill_id")
        right = frame.filter(pl.col("timestamp_ms") >= hi).select("first_agg_id", "first_fill_id")
        bounds.append([
            list(left.max().row(0)) if left.height else None,
            list(right.min().row(0)) if right.height else None,
        ])
    return bounds


def klines(data, archive_start, archive_end, start, end):
    pieces = list(csv_batches(data, trades=False, block=4 * 1024 ** 2))
    if not pieces:
        return pl.from_arrow(pa.Table.from_pydict({name: [] for name in KLINE_FIELDS},
                                                 schema=pa.schema(KLINE_FIELDS)))
    frame = pl.concat(pieces)
    scale = timestamp_scale(frame, "timestamp_ms", archive_start, archive_end)
    frame = frame.with_columns(pl.col("timestamp_ms", "close_time") // scale)
    timestamps = frame["timestamp_ms"]
    numbers = ["open", "high", "low", "close", "volume", "quote_volume", "buy_volume", "buy_quote_volume"]
    if not timestamps.is_sorted() or timestamps.is_duplicated().any() or frame.select(
        ((pl.col("timestamp_ms") % MINUTE != 0)
         | (pl.col("close_time") != pl.col("timestamp_ms") + MINUTE - 1)
         | (pl.col("high") < pl.max_horizontal("low", "open", "close"))
         | (pl.col("low") > pl.min_horizontal("open", "close"))
         | (pl.col("buy_volume") > pl.col("volume") + 1e-12)
         | (pl.col("buy_quote_volume") > pl.col("quote_volume") + 1e-12)
         | pl.any_horizontal(~pl.col(numbers).is_finite() | (pl.col(numbers) < 0))).any()
    ).item():
        raise ValueError("Malformed Binance 1-minute kline timestamps or values")
    return frame.filter(pl.col("timestamp_ms").is_between(start, end, closed="left"))


def reconcile(sparse, reference, scratch_start, scratch_end):
    fields = [*FLOATS[:6], "buy_volume", "buy_quote_volume", "trade_count"]
    actual = reduce_bars(sparse, MINUTE).select("timestamp_ms", *fields)
    checks = actual.join(reference.select("timestamp_ms", *fields), on="timestamp_ms",
                         how="full", coalesce=True, suffix="_ref").sort("timestamp_ms")
    checks = checks.filter(pl.col("timestamp_ms").is_between(scratch_start, scratch_end, closed="left"))
    traded = checks.filter((pl.col("volume") > 0).fill_null(False) | (pl.col("volume_ref") > 0).fill_null(False))
    stats = {}
    any_wrong = pl.lit(False)
    for name in fields:
        col, ref = pl.col(name), pl.col(name + "_ref")
        if name == "trade_count":
            wrong = col.fill_null(0) != ref.fill_null(0)
        else:
            eps = 2e-11 if name in FLOATS[:4] else 2e-8
            wrong = ((col - ref).abs() > pl.max_horizontal(ref.abs() * eps, pl.lit(1e-10))).fill_null(True)
        stats[name] = traded.select(wrong.sum()).item()
        any_wrong = any_wrong | wrong
    mismatches = traded.filter(any_wrong)
    missing = traded.filter(pl.col("volume").is_null() & (pl.col("volume_ref") > 0))
    stats["traded_minutes_compared"] = traded.height
    stats["mismatched_minutes"] = mismatches.height
    stats["missing_agg_minutes"] = traded["volume"].null_count()
    stats["missing_agg_timestamps_ms"] = missing["timestamp_ms"].to_list()
    stats["missing_ref_minutes"] = traded["volume_ref"].null_count()
    stats["missing_ref_timestamps_ms"] = traded.filter(pl.col("volume_ref").is_null())["timestamp_ms"].to_list()
    stats["first_mismatch_samples"] = mismatches.head(6).to_dicts()
    for name in ("volume", "quote_volume", "buy_volume", "trade_count"):
        src, ref = checks[name].sum(), checks[name + "_ref"].sum()
        stats[f"total_{name}"] = [src, ref]
    totals = ("volume", "quote_volume", "buy_volume", "buy_quote_volume", "trade_count")
    daily = checks.with_columns((pl.col("timestamp_ms") // DAY).alias("day")).group_by("day").agg(
        *(pl.col(f + suffix).sum() for f in totals for suffix in ("", "_ref"))
    ).sort("day")
    stats["daily_outliers"] = []
    stats["outlier_candidates_ms"] = []
    for row in daily.iter_rows(named=True):
        deviations = {}
        drift = []
        for name in totals:
            src, ref = row[name], row[name + "_ref"]
            tolerance = .002 if name == "trade_count" else .0005
            if abs(src - ref) > max(abs(ref) * tolerance, 1 if name == "trade_count" else 1e-8):
                deviations[name] = [src, ref]
                weight = (1 if src > ref else -1) / max(abs(ref), abs(src), 1)
                drift.append((pl.col(name).fill_null(0) - pl.col(name + "_ref").fill_null(0)) * weight)
        if deviations:
            stats["daily_outliers"].append({"date": str(utc_day(row["day"] * DAY)), "totals": deviations})
            offenders = checks.filter((pl.col("timestamp_ms") // DAY) == row["day"]).select(
                "timestamp_ms", pl.max_horizontal(drift).alias("drift"),
            ).filter(pl.col("drift") >= .00015).sort("drift", descending=True).head(3)
            stats["outlier_candidates_ms"].extend(offenders["timestamp_ms"].to_list())
    return stats


def quarantine_unobserved_minutes(sparse, reference, start, end, report):
    """Identify sustained gaps using independent positive-volume exchange candles."""
    missing = report["missing_agg_timestamps_ms"]
    gaps = []
    if missing:
        begin = previous = missing[0]
        for key in [*missing[1:], end + 2 * MINUTE]:
            if key == previous + MINUTE:
                previous = key
                continue
            if previous > begin:
                # The minute on either side can be partially affected.
                gaps.append([max(start, begin - MINUTE), min(end, previous + 2 * MINUTE)])
            begin = previous = key
    if not gaps:
        gaps = [[start, start + MINUTE] for start in report.get("outlier_candidates_ms", [])]
    return mask_minutes(sparse, reference, gaps)


def mask_minutes(sparse, reference, gaps):
    combined = []
    for lo, hi in sorted(gaps):
        if combined and combined[-1][1] >= lo:
            combined[-1][1] = max(hi, combined[-1][1])
        else:
            combined.append([lo, hi])
    gaps = combined
    for lo, hi in gaps:
        outside = ~pl.col("timestamp_ms").is_between(lo, hi, closed="left")
        sparse = sparse.filter(outside)
        reference = reference.filter(outside)
    return sparse, reference, gaps


def fill_bars(sparse, verified_minutes, interval, start, end, listing, seed=None):
    # A known 1-minute trading candle attests a traded minute. An absent or
    # zero-volume reference minute provides no historical session-state guarantee.
    offsets = pl.DataFrame({"offset": pl.int_range(0, MINUTE, interval, eager=True)})
    grid = verified_minutes.select("timestamp_ms").join(offsets, how="cross").select(
        (pl.col("timestamp_ms") + pl.col("offset")).alias("timestamp_ms")
    )
    grid = grid.filter(pl.col("timestamp_ms").is_between(max(start, listing), end, closed="left"))
    joined = grid.join(sparse, on="timestamp_ms", how="full", coalesce=True).sort("timestamp_ms")
    ts = pl.col("timestamp_ms")
    joined = joined.with_columns(
        ((ts.diff().fill_null(0) != interval).fill_null(False)).cast(pl.UInt32).cum_sum().alias("segment")
    ).with_columns(
        pl.col("close").forward_fill().over("segment").alias("known_close"),
        pl.col("volume").is_null().alias("is_empty"),
    )
    if seed is not None and joined.height and joined["timestamp_ms"][0] == start:
        joined = joined.with_columns(
            pl.when(pl.col("segment") == 1).then(pl.col("known_close").fill_null(seed))
            .otherwise(pl.col("known_close")).alias("known_close")
        )
    return joined.filter(pl.col("known_close").is_not_null()).with_columns(
        *(pl.col(f).fill_null(pl.col("known_close")) for f in FLOATS[:4]),
        *(pl.col(f).fill_null(0) for f in (*SUMS, "max_trade_qty")),
        pl.when(~pl.col("is_empty")).then(pl.col("quote_volume") / pl.col("volume")).alias("vwap"),
    ).select(SCHEMA.names)


def validate_bars(frame, interval, start, end, listing):
    ts = frame["timestamp_ms"]
    if not ts.is_sorted() or ts.is_duplicated().any():
        raise ValueError("Bars must be strictly chronological and unique")
    c = pl.col
    empty = c("is_empty")
    def near(a, b):
        return (a - b).abs() <= pl.max_horizontal(b.abs() * 1e-12, pl.lit(1e-13))

    invalid = (
        (c("timestamp_ms") % interval != 0)
        | ~c("timestamp_ms").is_between(start, end, closed="left")
        | (c("timestamp_ms") + interval <= listing)
        | pl.any_horizontal(~c((*FLOATS, "max_trade_qty")).is_finite() | (c((*FLOATS, "max_trade_qty")) < 0))
        | (c("low") > pl.min_horizontal("open", "close")) | (c("high") < pl.max_horizontal("open", "close"))
        | (c("low") > c("high")) | (c("low") <= 0)
        | ~near(c("buy_volume") + c("sell_volume"), c("volume"))
        | ~near(c("buy_quote_volume") + c("sell_quote_volume"), c("quote_volume"))
        | (c("buy_trade_count") + c("sell_trade_count") != c("trade_count"))
        | (c("trade_count") < c("agg_trade_count"))
        | (c("agg_trade_count") > 2 ** 32 - 1)
        | (c("max_trade_qty") > c("volume") + 1e-12)
        | (empty != (c("agg_trade_count") == 0)) | (empty != c("vwap").is_null())
        | (empty & (pl.any_horizontal(c((*SUMS, "max_trade_qty")) != 0)
                    | (c("open") != c("close")) | (c("low") != c("close")) | (c("high") != c("close"))))
        | (~empty & ((c("volume") <= 0) | ~c("vwap").is_finite()
                     | ~near(c("vwap"), c("quote_volume") / c("volume")))).fill_null(False)
        | (empty & (c("timestamp_ms").diff() == interval) & (c("close") != c("close").shift())).fill_null(False)
    )
    if frame.select(invalid.any()).item() or any(frame.select(pl.exclude("vwap")).null_count().row(0)):
        raise ValueError("Output bar invariant failed")


def arrow_bars(frame, interval):
    schema = SCHEMA.with_metadata(SCHEMA.metadata | {b"interval_ms": str(interval).encode()})
    return frame.to_arrow().cast(schema)


def writer(path, interval):
    schema = SCHEMA.with_metadata(SCHEMA.metadata | {b"interval_ms": str(interval).encode()})
    return pq.ParquetWriter(path, schema, compression="zstd", compression_level=3,
                            use_dictionary=False, write_statistics=True,
                            column_encoding={name: "BYTE_STREAM_SPLIT" for name in FLOATS})
