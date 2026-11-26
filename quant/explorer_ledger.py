"""Read-side reconstruction of native fills, funding, and position lifecycles."""

from collections import defaultdict
from heapq import merge
from math import isclose

import numpy as np

from simulator.feed import TRADED

EPSILON = 1e-11
REASONS = {
    0: "Filled", 1: "Partial fill", 2: "Risk limit",
    3: "Lot, participation, or minimum notional",
}


def classify(row):
    if row["filled"] == 0:
        return "rejected"
    before = row["after"] - row["filled"]
    after = row["after"]
    if abs(before) <= EPSILON:
        return "long_entry" if after > 0 else "short_entry"
    if abs(after) <= EPSILON:
        return "long_exit" if before > 0 else "short_exit"
    if before * after < 0:
        return "reversal"
    return "scale_out" if row["closed"] else "scale_in"


def execution(row, step, lag):
    """Expose native clocks and accounting fields without interpolating the ledger."""
    filled = row["filled"]
    kind = classify(row)
    return row | {
        "id": f"{row['symbol']}:{row['ms']}", "type": kind,
        "side": "buy" if row["requested"] > 0 else "sell",
        "signal_bar_start": row["decision"] - step - lag,
        "execution_end": row["bar_start"] + step,
        "filled_notional": abs(filled) * row["price"] if filled else 0,
        "partial": row["reason"] == 1, "reason_label": REASONS.get(row["reason"], "Unknown"),
    }


def reconstruct(symbol, fills, funding, terminal, end_ms):
    """Group one symbol's ordered native records without recalculating realized PnL."""
    current = None
    positions = []
    ledger = []
    quantity = 0.0
    for row, kind in merge(
        ((row, 1) for row in fills), ((row, 0) for row in funding),
        key=lambda pair: (pair[0]["ms"], pair[1]),
    ):
        if kind == 0:
            if current is None or not isclose(row["qty"], quantity, abs_tol=EPSILON):
                raise ValueError("Funding quantity disagrees with the native fill ledger")
            current["funding"] += row["payment"]
            current["timeline"].append(row | {"type": "funding"})
            continue
        delta, closed, after = row["filled"], row["closed"], row["after"]
        amount = abs(delta)
        if not amount or not isclose(quantity + delta, after, abs_tol=EPSILON):
            raise ValueError("Native fill quantities do not reconcile")
        if not 0 <= closed <= min(abs(quantity), amount) + EPSILON:
            raise ValueError("Native closed quantity exceeds the held or filled quantity")
        opened = amount - closed
        reverses = quantity * after < 0
        record = row | {"positions": []}

        def allocate(position, role, allocation):
            ratio = allocation / amount
            position["fees"] += row["fee"] * ratio
            position["slippage"] += row["slippage"] * ratio
            position["executions"] += 1
            position["timeline"].append(row | {
                "allocation": role, "allocated_qty": allocation,
                "allocated_fee": row["fee"] * ratio,
                "allocated_slippage": row["slippage"] * ratio,
                "allocated_realized": row["realized"] if role == "close" else 0.0,
            })
            if position["id"] not in record["positions"]:
                record["positions"].append(position["id"])
            else:
                position["executions"] -= 1

        if closed > EPSILON:
            if current is None:
                raise ValueError("Native reduction has no open position")
            allocate(current, "close", closed)
            current["closed_qty"] += closed
            current["exit_value"] += closed * row["price"]
            current["gross_realized"] += row["realized"]
            if after == 0 or reverses:
                current["exit_ms"] = row["ms"]
                current = None
        if opened > EPSILON:
            if current is None:
                current = {
                    "id": f"{symbol}-{len(positions) + 1}", "symbol": symbol,
                    "side": "long" if delta > 0 else "short", "entry_ms": row["ms"],
                    "exit_ms": None, "opened_qty": 0.0, "closed_qty": 0.0,
                    "max_qty": 0.0, "entry_value": 0.0, "exit_value": 0.0,
                    "gross_realized": 0.0, "fees": 0.0, "funding": 0.0, "slippage": 0.0,
                    "executions": 0, "timeline": [], "max_notional": None,
                    "mfe": None, "mae": None, "sampled_ticks": 0, "held_ticks": 0,
                }
                positions.append(current)
            allocate(current, "open", opened)
            current["opened_qty"] += opened
            current["entry_value"] += opened * row["price"]
        quantity = after
        if current is not None:
            current["max_qty"] = max(current["max_qty"], abs(after))
            record["active_position"] = current["id"]
        ledger.append(record)

    for position in positions:
        end = position["exit_ms"]
        position.update({
            "state": "open" if end is None else "closed",
            "duration_ms": (end_ms if end is None else end) - position["entry_ms"],
            "average_entry": position["entry_value"] / position["opened_qty"],
            "average_exit": position["exit_value"] / position["closed_qty"] if position["closed_qty"] else None,
            "net_pnl": position["gross_realized"] - position["fees"] + position["funding"],
            "unrealized": terminal["unrealized"] if position is current else None,
            "terminal_qty": quantity if position is current else 0.0,
            "terminal_entry": ledger[-1]["entry"] if position is current else None,
        })
        position["return_on_opening_notional"] = (
            position["net_pnl"] + (position["unrealized"] or 0)
        ) / position["entry_value"]
    totals = {
        "qty": quantity, "realized": sum(p["gross_realized"] for p in positions),
        **{name: sum(p[name] for p in positions) for name in ("fees", "funding", "slippage")},
    }
    for field, value in totals.items():
        if not isclose(value, terminal[field], rel_tol=1e-9, abs_tol=1e-7):
            raise ValueError(f"Reconstructed {field} disagrees with the native report")
    if current is not None and not isclose(current["terminal_entry"], terminal["entry"], abs_tol=1e-7):
        raise ValueError("Terminal average entry disagrees with the native report")
    return positions, ledger, totals


def sample_marks(feed, ledger, positions, *, fallback):
    """Annotate sampled excursions and peak notional using the native mark inputs."""
    if not ledger:
        return
    by_id = {position["id"]: position for position in positions}
    index = -1
    for times, market, status in feed.blocks(shared=True):
        times = times + feed.step + feed.lag
        mark = market[:, 0, 3]
        if fallback:
            mark = np.where(np.isfinite(mark), mark, np.where(status[:, 0] == TRADED, market[:, 0, 0], np.nan))
        left = 0
        while left < len(times):
            while index + 1 < len(ledger) and ledger[index + 1]["ms"] <= times[left]:
                index += 1
            stop = len(times)
            if index + 1 < len(ledger):
                stop = int(np.searchsorted(times, ledger[index + 1]["ms"]))
            event = ledger[index] if index >= 0 else {}
            position = by_id.get(event.get("active_position"))
            if position is not None:
                subset = mark[left:stop]
                valid = subset[np.isfinite(subset) & (subset > 0)]
                position["held_ticks"] += stop - left
                position["sampled_ticks"] += len(valid)
                if len(valid):
                    def accumulate(key, value, function):
                        old = position[key]
                        position[key] = float(value if old is None else function(old, value))

                    excursion = event["after"] * (valid - event["entry"])
                    accumulate("mfe", excursion.max(), max)
                    accumulate("mae", excursion.min(), min)
                    accumulate("max_notional", abs(event["after"]) * valid.max(), max)
            left = stop


def accounting_at(ledger, funding, timestamps):
    """Piecewise symbol state sampled on the portfolio's booked clocks."""
    qty = entry = realized = fees = cash = 0.0
    i = j = 0
    for stamp in timestamps:
        while i < len(ledger) and ledger[i]["ms"] <= stamp:
            row = ledger[i]
            qty, entry = row["after"], row["entry"]
            realized += row["realized"]
            fees += row["fee"]
            i += 1
        while j < len(funding) and funding[j]["ms"] <= stamp:
            cash += funding[j]["payment"]
            j += 1
        yield qty, entry if qty else None, realized, fees, cash
