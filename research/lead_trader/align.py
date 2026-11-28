"""Match public portfolio observations to audited completed bars and BTC context."""

import argparse
import json
from collections import defaultdict
from pathlib import Path

import numpy as np
import polars as pl

import quant as q
from marketdata.reader import as_millis


BAR = 5_000
DAY = 86_400_000
START, END = map(as_millis, ("2024-11-25", "2026-11-25"))
HORIZONS = {"15s": 3, "1m": 12, "5m": 60, "15m": 180, "1h": 720, "4h": 2880}
FIELDS = ("open", "high", "low", "close", "quote_volume", "buy_quote_volume", "trade_count")


def rows(path):
    with path.open(encoding="utf-8") as source:
        for line in source:
            yield from json.loads(line)["data"]["list"]


def observations(folder, prior):
    old = json.loads(prior.read_text(encoding="utf-8-sig"))
    order_key = lambda r: tuple(r[k] for k in ("symbol", "orderTime", "orderUpdateTime", "side",
                                               "type", "executedQty", "avgPrice", "totalPnl"))
    orders = {order_key(r): r for r in old["latest_records"]}
    for path in sorted(folder.glob("order*.jsonl")):
        orders.update((order_key(r), r) for r in rows(path))
    positions = {}
    incoming = [*old["position_history"], *rows(folder / "position-history.jsonl")]
    for row in incoming:
        key = row["positionId"], row["index"]
        if key not in positions or row["updateTime"] >= positions[key]["updateTime"]:
            positions[key] = row
    positions = sorted(positions.values(), key=lambda r: (r["opened"], r["positionId"], int(r["index"])))
    for name, data in (("orders", list(orders.values())), ("positions", positions)):
        pl.from_dicts(data).write_parquet(folder / f"{name}.parquet")
    events = []
    for r in positions:
        direction = 1 if r["side"] == "Long" else -1
        key = f"{r['positionId']}:{r['index']}"
        shared = dict(symbol=r["symbol"], side=direction, position=key, pnl=None, order_kind=None,
                      notional=None, price=None, submitted_ms=None, status=r["status"])
        events.append(shared | dict(kind="entry", ms=r["opened"]))
        if r["closed"] and r["status"] == "All Closed":
            events.append(shared | dict(kind="exit", ms=r["closed"], pnl=r["closingPnl"]))
    for r in orders.values():
        events.append(dict(kind="order", ms=r["orderUpdateTime"], submitted_ms=r["orderTime"],
                           symbol=r["symbol"], side=1 if r["side"] == "BUY" else -1,
                           pnl=r["totalPnl"], price=r["avgPrice"], notional=r["executedQty"] * r["avgPrice"],
                           order_kind=r["type"], position=None, status=r["positionSide"]))
    return events


def prefix(x):
    return np.r_[0., np.cumsum(x)]


def completed_features(events, lo, status, values, lag):
    events = np.asarray(events, dtype="i8")
    close = values["close"]
    # last scheduled bar whose end plus publication lag is observable at event time
    i = (events - lo - lag) // BAR - 1
    exact = (events - lo) // BAR
    valid = (status <= q.data.EMPTY) & np.isfinite(close) & (close > 0)
    invalid = prefix(~valid)
    quote = prefix(np.nan_to_num(values["quote_volume"], nan=0.))
    taker = prefix(np.nan_to_num(values["buy_quote_volume"], nan=0.))
    log_prices = np.log(np.where(valid, close, 1.))
    squares = prefix(np.diff(log_prices, prepend=log_prices[0]) ** 2)
    out = {}

    def ready(a, b):
        return ((a >= 0) & (b < len(close))
                & (invalid[np.clip(b + 1, 0, len(close))] == invalid[np.clip(a, 0, len(close))]))

    def gather(x, a):
        return x[np.clip(a, 0, len(x) - 1)]

    out["observable_close"] = np.where(ready(i, i), gather(close, i), np.nan)
    for name, n in HORIZONS.items():
        previous = i - n
        ok = ready(previous, i)
        out[f"ret_{name}"] = np.where(ok, gather(close, i) / gather(close, previous) - 1., np.nan)
        out[f"fwd_{name}"] = np.where(ready(i, i + n), gather(close, i + n) / gather(close, i) - 1., np.nan)
        if name not in {"1m", "5m", "15m", "1h"}:
            continue
        vol = gather(quote, i + 1) - gather(quote, previous + 1)
        buy = gather(taker, i + 1) - gather(taker, previous + 1)
        earlier = gather(quote, previous + 1) - gather(quote, previous - 720 + 1)
        squared = gather(squares, i + 1) - gather(squares, previous + 1)
        out[f"quote_{name}"] = np.where(ok, vol, np.nan)
        out[f"flow_{name}"] = np.divide(2 * buy - vol, vol, out=np.full(len(i), np.nan), where=ok & (vol > 0))
        out[f"activity_{name}"] = np.divide(vol * 720 / n, earlier, out=np.full(len(i), np.nan),
                                            where=ok & ready(previous - 720, previous) & (earlier > 0))
        out[f"vol_{name}"] = np.where(ok, np.sqrt(np.maximum(0., squared)), np.nan)
    # Additional causal microstructure features used by the inverse decision study.
    valid5 = ready(i - 59, i)
    high = pl.Series(np.where(valid, values["high"], np.nan)).fill_nan(None).rolling_max(60).to_numpy()
    low = pl.Series(np.where(valid, values["low"], np.nan)).fill_nan(None).rolling_min(60).to_numpy()
    out["range_rebound5m"] = np.where(valid5 & (gather(low,i)>0), gather(close,i)/gather(low,i)-1, np.nan)
    out["range_drawdown5m"] = np.where(valid5 & (gather(high,i)>0), gather(close,i)/gather(high,i)-1, np.nan)
    prior = np.where(ready(i-24,i), gather(close,i-12)/gather(close,i-24)-1, np.nan)
    out["acceleration_1m"] = out["ret_1m"]-prior
    if "trade_count" in values:
        counts = prefix(np.nan_to_num(values["trade_count"]))
        now = gather(counts,i+1)-gather(counts,i-11)
        before = gather(counts,i-11)-gather(counts,i-23)
        base = gather(counts,i-11)-gather(counts,i-731)
        out["trade_accel1m"] = np.divide(now,before,out=np.full(len(i),np.nan),
                                         where=ready(i-23,i)&(before>0))
        out["trade_intensity1m"] = np.divide(now*60,base,out=np.full(len(i),np.nan),
                                              where=ready(i-731,i)&(base>0))
    contemporaneous = (exact >= 0) & (exact < len(close)) & (gather(status, exact) == q.data.TRADED)
    for key in ("high", "low", "quote_volume"):
        out[f"event_{key}"] = np.where(contemporaneous, gather(values[key], exact), np.nan)
    return out


def sample(dataset, symbol, timestamps):
    """Read contiguous runs of touched UTC days with bounded surrounding history."""
    by_day = defaultdict(list)
    for i, event in enumerate(timestamps):
        if START <= event < END:
            by_day[event // DAY].append(i)
    outputs = {}
    for day, indices in sorted(by_day.items()):
        ticks = [timestamps[i] for i in indices]
        lo = max(START, day * DAY - 4 * 3_600_000 - BAR)
        hi = min(END, (day + 1) * DAY + 4 * 3_600_000)
        selection = q.data.select(dataset, start=lo, end=hi, symbols=(symbol,), fields=FIELDS)
        blocks = list(q.data.iter_blocks(selection, block_steps=32_768))
        status = np.concatenate([block.status[:, 0] for block in blocks])
        values = {key: np.concatenate([block.values[key][:, 0] for block in blocks]) for key in FIELDS}
        with np.errstate(divide="ignore", invalid="ignore"):
            found = completed_features(ticks, lo, status, values, dataset.availability_lag_ms)
        outputs.update((idx, {key: float(col[j]) for key, col in found.items()})
                       for j, idx in enumerate(indices))
    return outputs


def align(events, dataset):
    by_symbol = defaultdict(list)
    for event in events:
        if START <= event["ms"] < END:
            by_symbol[event["symbol"]].append(event)
    times = sorted({event["ms"] + offset for group in by_symbol.values() for event in group
                    for offset in (0, -DAY) if (dataset.path / f"symbol={event['symbol']}").is_dir()})
    btc = {times[i]: x for i, x in sample(dataset, "BTCUSDT", times).items()}
    for symbol, group in sorted(by_symbol.items()):
        if not (dataset.path / f"symbol={symbol}").is_dir():
            for event in group:
                yield event | {"control": False, "market_symbol_available": False}
            continue
        timestamps = [event["ms"] + offset for offset in (0, -DAY) for event in group]
        matched = sample(dataset, symbol, timestamps)
        for i, ts in enumerate(timestamps):
            control = i >= len(group)
            event = group[i % len(group)]
            features = matched.get(i, {})
            btc_features = {f"btc_{k}": v for k, v in btc.get(ts, {}).items() if k.startswith("ret_")}
            yield event | {"control": control, "market_symbol_available": True} | features | btc_features


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("folder", type=Path)
    parser.add_argument("--prior", type=Path, default=Path("binance-5140488422016171264-raw.json"))
    parser.add_argument("--source", type=Path, default=Path("data/binance/um/bars_5s"))
    args = parser.parse_args()
    data = observations(args.folder, args.prior)
    pl.from_dicts(data, infer_schema_length=None).write_parquet(args.folder / "events.parquet")
    aligned = list(align(data, q.data.open(args.source, universe=None)))
    pl.from_dicts(aligned, infer_schema_length=None).write_parquet(args.folder / "matched.parquet")
    print("observations:", len(data), "in-range events and controls:", len(aligned))


if __name__ == "__main__":
    main()
