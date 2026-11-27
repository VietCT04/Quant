"""Summarize source coverage, inventory and paired market-event observations."""

import argparse
import json
from bisect import bisect_left, bisect_right
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import polars as pl


def finite(x):
    return np.asarray(x, dtype=float)[np.isfinite(np.asarray(x, dtype=float))]


def distribution(x):
    x = finite(x)
    if not len(x):
        return {"n": 0}
    p10, median, p90 = np.quantile(x, [.1, .5, .9])
    return {"n": len(x), "p10": float(p10), "median": float(median), "p90": float(p90),
            "mean": float(np.mean(x)), "share_positive": float(np.mean(x > 0))}


def span(x):
    return [datetime.fromtimestamp(value / 1000, UTC).isoformat() for value in (min(x), max(x))]


def lifecycles(positions, orders):
    rows = defaultdict(list)
    for order in orders.sort("orderUpdateTime").to_dicts():
        rows[order["symbol"]].append(order)
    timestamps = {symbol: [order["orderUpdateTime"] for order in trades] for symbol, trades in rows.items()}
    records = positions.to_dicts()
    starts = {(p["symbol"], p["side"], p["opened"]) for p in records}
    ends = {(p["symbol"], p["side"], p["closed"]) for p in records if p["closed"]}
    for p in records:
        if p["status"] != "All Closed":
            continue
        symbol = p["symbol"]
        ts = timestamps.get(symbol, [])
        head = range(bisect_left(ts, p["opened"] - 1), bisect_right(ts, p["opened"] + 1))
        tail = range(bisect_left(ts, p["closed"] - 1), bisect_right(ts, p["closed"] + 1))
        if len(head) != 1 or len(tail) != 1 or head.start >= tail.start:
            continue
        trades = rows[symbol][head.start:tail.start + 1]
        side = 1 if p["side"] == "Long" else -1
        opposite = "Short" if side > 0 else "Long"
        qty = np.array([(1 if order["side"] == "BUY" else -1) * side * order["executedQty"] for order in trades])
        if qty[0] <= 0 or qty[-1] >= 0:
            continue
        internal = qty[1:-1]
        opened = p["closedVolume"] - internal[internal > 0].sum()
        closed = p["closedVolume"] + internal[internal < 0].sum()
        tolerance = 1e-8 * max(1., p["closedVolume"])
        if not (0 < opened <= qty[0] + tolerance and 0 < closed <= -qty[-1] + tolerance):
            continue
        if qty[0] - opened > tolerance and not any((symbol, opposite, p["opened"] + t) in ends for t in (-1, 0, 1)):
            continue
        if -qty[-1] - closed > tolerance and not any((symbol, opposite, p["closed"] + t) in starts for t in (-1, 0, 1)):
            continue
        boundary_split = (qty[0] - opened > tolerance) or (-qty[-1] - closed > tolerance)
        qty[0], qty[-1] = opened, -closed
        exposure = qty.cumsum()
        if (np.any(exposure[:-1] <= 1e-8) or not np.isclose(exposure[-1], 0., atol=tolerance)
                or not np.isclose(exposure.max(), p["maxOpenInterest"], rtol=1e-7)
                or not np.isclose(-qty[qty < 0].sum(), p["closedVolume"], rtol=1e-7)):
            continue
        prices = np.array([r["avgPrice"] for r in trades])
        cost = 0.
        booked = 0.
        actions = []
        last_add = None
        for trade, delta, price, remaining in zip(trades, qty, prices, exposure, strict=True):
            kind = "open" if delta > 0 and booked == 0 else "add" if delta > 0 else "close" if remaining < 1e-8 else "reduce"
            actions.append({"symbol": symbol, "id": f"{p['positionId']}:{p['index']}", "side": side,
                            "timestamp": trade["orderUpdateTime"], "action": kind, "size": abs(delta) * price,
                            "booked_notional": max(0., remaining) * price,
                            "from_cost_bps": side * (price / cost - 1) * 10_000 if cost > 0 else None,
                            "from_last_add_bps": side * (price / last_add - 1) * 10_000 if last_add else None})
            if delta > 0:
                cost = (booked * cost + delta * price) / remaining
                last_add = price
            booked = remaining
        yield {"symbol": symbol, "side": side, "pnl": p["closingPnl"], "hold_s": (p["closed"] - p["opened"]) / 1_000,
               "adds": int(np.sum(qty > 0) - 1), "reduces": int(np.sum(qty < 0) - 1),
               "peak_notional": float(max(row["booked_notional"] for row in actions)), "actions": actions,
               "boundary_split": bool(boundary_split)}


def summary(folder):
    positions = pl.read_parquet(folder / "positions.parquet")
    orders = pl.read_parquet(folder / "orders.parquet").with_columns(
        (pl.col("avgPrice") * pl.col("executedQty")).alias("notional"))
    matched = pl.read_parquet(folder / "matched.parquet")
    closed = positions.filter(pl.col("status") == "All Closed").with_columns(
        ((pl.col("closed") - pl.col("opened")) / 60_000).alias("holding_min"),
        (pl.col("maxOpenInterest") * pl.col("avgCost")).alias("peak_notional_proxy"),
        (pl.col("closedVolume") / pl.col("maxOpenInterest")).alias("volume_over_peak"),
    )
    openings = {(r["symbol"], r["side"], r["opened"]) for r in positions.to_dicts()}
    result = {
        "orders": {"n": len(orders), "symbols": orders["symbol"].n_unique(), "utc": span(orders["orderUpdateTime"]),
                   "notional": distribution(orders["notional"]), "side_counts": orders.group_by("side").len().to_dicts(),
                   "daily_rows": orders.with_columns(pl.from_epoch("orderUpdateTime", time_unit="ms").dt.date().cast(pl.String).alias("date"))
                   .group_by("date").len().sort("date").to_dicts(),
                   "types": orders.group_by("type", "positionSide").len().to_dicts(),
                   "time_difference_ms": distribution(orders["orderUpdateTime"] - orders["orderTime"])},
        "position_rows": {"n": len(positions), "unique_position_ids": positions["positionId"].n_unique(),
                          "symbols": positions["symbol"].n_unique(), "updated": span(positions["updateTime"]),
                          "closed_summary": {}},
        "market": {},
    }
    for side in ("Long", "Short"):
        group = closed.filter(pl.col("side") == side)
        pnl = np.sort(group["closingPnl"].to_numpy())
        opposite = "Short" if side == "Long" else "Long"
        result["position_rows"]["closed_summary"][side] = {
            "n": len(group), "win_rate": float((group["closingPnl"] > 0).mean()), "pnl_sum": group["closingPnl"].sum(),
            "pnl": distribution(group["closingPnl"]), "leverage": distribution(group["leverage"].cast(pl.Float64)),
            "holding_min": distribution(group["holding_min"]), "peak_notional_proxy": distribution(group["peak_notional_proxy"]),
            "volume_over_peak": distribution(group["volume_over_peak"]),
            "share_volume_above_peak": float((group["volume_over_peak"] > 1.00001).mean()),
            "pnl_best_five": float(pnl[-5:].sum()), "pnl_worst_five": float(pnl[:5].sum()),
            "opposite_open_at_close": sum(
                any((symbol, opposite, closed_ms + delta) in openings for delta in (-1, 0, 1))
                for symbol, closed_ms in group.select("symbol", "closed").iter_rows()
            ),
        }
    life = list(lifecycles(positions, orders))
    actions = [action for position in life for action in position["actions"]]
    if actions:
        pl.from_dicts(actions).write_parquet(folder / "reconciled-actions.parquet")
    result["reconciled"] = {
        "lifecycles": len(life), "symbols": len({x["symbol"] for x in life}), "adds": distribution([x["adds"] for x in life]),
        "reduces": distribution([x["reduces"] for x in life]), "peak_notional": distribution([x["peak_notional"] for x in life]),
        "boundary_splits": sum(x["boundary_split"] for x in life),
        "actions": {kind: {field: distribution([a[field] for a in actions if a["action"] == kind])
                           for field in ("size", "from_cost_bps", "from_last_add_bps", "booked_notional")}
                    for kind in ("open", "add", "reduce", "close")},
    }
    for kind in ("entry", "exit", "order"):
        pop = matched.filter(pl.col("kind") == kind)
        observed = pop.filter(~pl.col("control"))
        market = observed.filter(pl.col("ret_1m").is_finite())
        m = {"in_range": len(observed), "symbol_in_archive": int(observed["market_symbol_available"].sum()),
             "matched_1m": len(market), "matched_symbols": market["symbol"].n_unique(), "contexts": {}}
        if kind == "order":
            fills = observed.filter(pl.col("event_low").is_finite())
            priced = fills.with_columns(
                ((pl.col("price") >= pl.col("event_low") * .99999)
                 & (pl.col("price") <= pl.col("event_high") * 1.00001)).alias("inside"))
            m["event_bar_price_inside"] = {"n": len(priced), "share": priced["inside"].mean()}
            m["fill_fraction_of_bar_quote"] = distribution(priced["notional"] / priced["event_quote_volume"])
            m["fill_size_by_absolute_1m_move"] = {
                str(lo): distribution(
                    market.filter(pl.col("ret_1m").abs().is_between(lo, hi, closed="left"))["notional"]
                ) for lo, hi in ((0., .002), (.002, .005), (.005, .01), (.01, np.inf))
            }
        else:
            for direction in (1, -1):
                for split in ("all", "win", "loss") if kind == "exit" else ("all",):
                    group = pop.filter(pl.col("side") == direction)
                    if split != "all":
                        group = group.filter((pl.col("pnl") > 0) if split == "win" else (pl.col("pnl") < 0))
                    key = f"{direction}/{split}"
                    measures = m["contexts"][key] = {}
                    for label, frame in (("event", group.filter(~pl.col("control"))),
                                         ("prior_day", group.filter(pl.col("control")))):
                        measures[label] = metrics(frame, direction)
                    if split == "all":
                        measures["paired"] = paired_context(group, direction)
        if kind == "order" and actions:
            labels = pl.from_dicts(actions).select("symbol", pl.col("timestamp").alias("ms"), "action",
                                                   pl.col("side").alias("position_side"))
            unique = labels.filter(~pl.struct("symbol", "ms").is_duplicated())
            order_events = market.filter(~pl.struct("symbol", "ms").is_duplicated()).join(unique, on=["symbol", "ms"])
            m["booked_action"] = {}
            for action in ("open", "add", "reduce", "close"):
                group = order_events.filter(pl.col("action") == action)
                m["booked_action"][action] = metrics(group, side_col="side") | {
                    "symbols": group.group_by("symbol").len().sort("len", descending=True).to_dicts()
                }
            adjustments = order_events.filter(pl.col("action").is_in(("add", "reduce")))
            largest = (adjustments.group_by("symbol").len().sort(["len", "symbol"], descending=[True, False])
                       .get_column("symbol").head(1).to_list())
            m["booked_adjustments_by_signed_1m_move"] = {
                subset: [
                    {"band": band, "adds": g.filter(pl.col("action") == "add").height, "total": g.height}
                    for band, lo, hi in (("below -50 bp", -np.inf, -.005), ("-50 to 0 bp", -.005, 0.),
                                         ("0 to 50 bp", 0., .005), ("at least 50 bp", .005, np.inf))
                    for g in [frame.filter((pl.col("ret_1m") * pl.col("position_side")).is_between(lo, hi, closed="left"))]
                ] for subset, frame in (("all", adjustments), ("exclude_largest", adjustments.filter(~pl.col("symbol").is_in(largest))))
            } | {"largest": largest}
        result["market"][kind] = m
    (folder / "summary.json").write_text(json.dumps(result, indent=2), encoding="utf-8")


def metrics(frame, direction=1, *, side_col=None):
    sign = frame[side_col].to_numpy() if side_col else direction
    result = {}
    for h in ("15s", "1m", "5m", "15m", "1h", "4h"):
        ret = frame[f"ret_{h}"].to_numpy()
        btc = frame[f"btc_ret_{h}"].to_numpy()
        fwd = frame[f"fwd_{h}"].to_numpy()
        result[f"signed_ret_{h}_bps"] = distribution(sign * ret * 10_000)
        result[f"signed_btc_adjusted_{h}_bps"] = distribution(sign * (ret - btc) * 10_000)
        result[f"signed_fwd_{h}_bps"] = distribution(sign * fwd * 10_000)
    for prefix in ("activity", "flow", "quote", "vol"):
        for h in ("1m", "5m", "15m", "1h"):
            vals = frame[f"{prefix}_{h}"].to_numpy()
            result[f"{prefix}_{h}"] = distribution(vals * sign if prefix == "flow" else vals)
    return result


def paired_context(frame, direction):
    event = frame.filter(~pl.col("control"))
    control = frame.filter(pl.col("control"))
    names = ("ret_1m", "ret_5m", "activity_1m")
    pair = event.select("position", *names).join(control.select("position", *names), on="position", suffix="_prior")
    values = {k: distribution((pair[k] - pair[k + "_prior"]) * (direction * 10_000 if k.startswith("ret") else 1))
              for k in names}
    daily = (event.filter(pl.col("ret_1m").is_finite())
             .group_by(pl.from_epoch("ms", time_unit="ms").dt.date())
             .agg((pl.col("ret_1m") * direction).median().alias("ret"), pl.len().alias("n"))
             .filter(pl.col("n") >= 5))
    btc = event.filter(pl.col("ret_5m").is_finite() & pl.col("btc_ret_5m").is_finite())
    values["btc_5m"] = {"n": len(btc), "correlation": float(np.corrcoef(btc["ret_5m"], btc["btc_ret_5m"])[0, 1])}
    counts = event.filter(pl.col("ret_1m").is_finite()).group_by("symbol").len().sort(
        ["len", "symbol"], descending=[True, False])
    leading = counts["symbol"].head(3).to_list()
    values["robustness"] = {"days_with_at_least_five": len(daily), "days_with_negative_median": int((daily["ret"] < 0).sum()),
                            "top_three_symbols": leading,
                            "ex_top_three_signed_ret_1m_bps": distribution(
                                event.filter(~pl.col("symbol").is_in(leading))["ret_1m"] * direction * 10_000)}
    return values


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("folder", type=Path)
    summary(parser.parse_args().folder)
