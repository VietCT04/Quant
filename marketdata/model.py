from __future__ import annotations

import calendar
import hashlib
import json
import os
import re
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

DAY = 86_400_000
MINUTE = 60_000
VERSION = "1.0.0"
BASE = "https://data.binance.vision/data/futures/um"
FAPI = "https://fapi.binance.com/fapi/v1"


def millis(value: date) -> int:
    return (value - date(1970, 1, 1)).days * DAY


def utc_day(value: int) -> date:
    return date(1970, 1, 1) + timedelta(days=value // DAY)


def today() -> date:
    return datetime.now(UTC).date()


def years_ago(end: date, years: int) -> date:
    year = end.year - years
    return date(year, end.month, min(end.day, calendar.monthrange(year, end.month)[1]))


def next_month(d: date) -> date:
    return (d.replace(day=28) + timedelta(days=4)).replace(day=1)


def dates(start: date, end: date):
    for day in range((end - start).days):
        yield start + timedelta(days=day)


def resolution(text: str) -> int:
    match = re.fullmatch(r"([1-9]\d*)(s|m)", text)
    if match is None:
        raise ValueError("Use a resolution such as 1s, 5s, or 1m.")
    interval = int(match[1]) * (1000 if match[2] == "s" else MINUTE)
    if MINUTE % interval:
        raise ValueError("Resolution must divide one UTC minute for kline reconciliation.")
    return interval


def symbol_name(symbol: str) -> str:
    if not symbol or not all(c.isalnum() or c in "_-" for c in symbol):
        raise ValueError(f"Invalid symbol: {symbol!r}")
    return symbol


def sync_file(path: Path) -> None:
    with path.open("rb+") as f:
        os.fsync(f.fileno())


def replace_file(staged: Path, path: Path) -> None:
    sync_file(staged)
    os.replace(staged, path)
    if os.name != "nt":
        fd = os.open(path.parent, os.O_DIRECTORY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)


def write_json(path: Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    staged = path.with_name(path.name + ".part")
    staged.write_text(json.dumps(obj, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    replace_file(staged, path)


def load_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def sha256(path: Path) -> str:
    with path.open("rb") as f:
        return hashlib.file_digest(f, "sha256").hexdigest()


@dataclass(frozen=True)
class Archive:
    symbol: str
    start: date
    monthly: bool = False
    kline: bool = False

    @property
    def end(self):
        return next_month(self.start) if self.monthly else self.start + timedelta(days=1)

    @property
    def filename(self):
        kind = "1m" if self.kline else "aggTrades"
        period = self.start.isoformat()[:7 if self.monthly else 10]
        return f"{self.symbol}-{kind}-{period}.zip"

    @property
    def url(self):
        from urllib.parse import quote

        cadence = "monthly" if self.monthly else "daily"
        symbol = quote(symbol_name(self.symbol), safe="")
        path = f"klines/{symbol}/1m" if self.kline else f"aggTrades/{symbol}"
        return f"{BASE}/{cadence}/{path}/{quote(self.filename, safe='-_.')}"


def partition_path(root: Path, symbol: str, month: str, interval: int) -> Path:
    name = f"bars_{interval // 1000}s"
    return (root / "binance" / "um" / name / f"symbol={symbol_name(symbol)}"
            / f"year={month[:4]}" / f"month={month[5:]}" / "part-000.parquet")
