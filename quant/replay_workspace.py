"""Read-side symbol replay over booked native fills and the public order archive."""

from bisect import bisect_left, bisect_right
from collections import defaultdict
from contextlib import closing
from dataclasses import dataclass, field
from functools import lru_cache
import json
import math
from pathlib import Path
import sqlite3

import polars as pl

from . import data
from .explorer import TIMEFRAMES, _compact, _extrema, _scan, _symbol
from .explorer_ledger import execution, reconstruct
from .validation import RESEARCH_START, RESEARCH_END


STEP = 5000
MARKET = ("open", "high", "low", "close", "volume")
PORTFOLIO = "5140488422016171264"
ARCHIVE = Path(__file__).resolve().parents[1] / "data/research/lead-trader/collector/collector.sqlite"
MARKER_FIELDS = ("id", "ms", "price", "side", "type", "filled_notional", "before", "after", "virtual", "position")
MAX_BUCKETS = 2000


def finite(value):
    return value if value is not None and math.isfinite(value) else None


def position_fields(row):
    timeline = row.get("timeline", ())
    peak = max((abs(r["after"]) * r["price"] for r in timeline if r.get("after") is not None
                and (r["allocation"] == "open" or r["after"] * r["filled"] < 0)), default=None)
    return {**_compact([row])[0], "max_notional": row.get("max_notional") or peak,
            "adds": max(0, sum(a.get("allocation") == "open" for a in timeline) - 1),
            "reductions": max(0, sum(a.get("allocation") == "close" for a in timeline) - (row["exit_ms"] is not None))}


def add_native_fields(ledger, funding):
    realized = fees = cash = entry = 0.
    life_cash = life_fees = life_realized = 0.
    cursor = 0
    for row in ledger:
        while cursor < len(funding) and funding[cursor]["ms"] <= row["ms"]:
            cash += funding[cursor]["payment"]
            life_cash += funding[cursor]["payment"]
            cursor += 1
        before, after = row["after"] - row["filled"], row["after"]
        realized += row["realized"]
        fees += row["fee"]
        last_entry = row["entry"] if after else None
        if (not before or before * after < 0) and after:
            life_fees = row["fee"] * abs(after / row["filled"])
            life_cash = life_realized = 0.
        else:
            life_fees += row["fee"]
            life_realized += row["realized"]
        breakeven = last_entry + (life_fees - life_cash - life_realized) / after if after else None
        row.update(before=before, entry_before=entry if before else None, entry_after=last_entry,
                   breakeven=breakeven,
                   before_notional=before * row["price"], after_notional=after * row["price"],
                   gross_cumulative=realized, net_cumulative=realized - fees + cash, funding_cumulative=cash,
                   position=row.get("active_position") or next(iter(row["positions"]), None),
                   provenance="Native booked ledger")
        entry = last_entry or 0.


def as_reported(row):
    """Hide labels and inventory derived from position summaries published later."""
    return {k: row[k] for k in ("id", "ms", "price", "filled", "side", "filled_notional", "reported_pnl",
                               "reported_cumulative", "symbol_name", "decision") if k in row} | {
        "type": "realized" if row.get("reported_pnl") else "unclassified", "position": None,
        "provenance": "Public order as reported at the replay clock",
    }


@dataclass
class SymbolTape:
    rows: list
    lives: list
    funding: list = field(default_factory=list)

    def __post_init__(self):
        self.times = [x["ms"] for x in self.rows]
        self.indices = {x["id"]: i for i, x in enumerate(self.rows)}
        self.positions = {p["id"]: p for p in self.lives}
        self.first = {}
        self.last = {}
        self.cash_times = [row["ms"] for row in self.funding]
        self.cash_totals = [0.]
        for row in self.funding:
            self.cash_totals.append(self.cash_totals[-1] + row["payment"])
        for i, row in enumerate(self.rows):
            for key in row.get("positions", ()):
                self.first.setdefault(key, i)
                self.last[key] = i
        self.lifecycle_starts = sorted(self.first.values())
        self.filtered = lru_cache(maxsize=12)(self._filtered)

    def _filtered(self, selectors):
        q = dict(selectors)
        lo = bisect_left(self.times, int(q.get("from") or -1))
        hi = bisect_right(self.times, int(q.get("to") or 2**63 - 1))
        if len(q) <= int("from" in q) + int("to" in q):
            return range(lo, hi)
        return tuple(i for i in range(lo, hi) if self.matches(self.rows[i], q))

    def matches(self, row, q):
        position = self.positions.get(row.get("position"), {})
        if q.get("action") and row["type"] != q["action"]:
            return False
        if q.get("direction") and (position.get("side") or row.get("direction")) != q["direction"]:
            return False
        if q.get("outcome"):
            result = position.get("net_pnl")
            if result is None or (result > 0 if q["outcome"] == "win" else result < 0) is False:
                return False
        size = row.get("after_notional")
        fields = {"minSize": row.get("filled_notional"), "maxSize": row.get("filled_notional"),
                  "minPosition": abs(size) if size is not None else None,
                  "minMinutes": position.get("duration_ms", 0) / 60_000 if position.get("duration_ms") is not None else None,
                  "minAdds": position.get("adds"), "minReduces": position.get("reductions")}
        return all(value is not None and (value <= float(q[key]) if key == "maxSize" else value >= float(q[key]))
                   for key, value in fields.items() if key in q)

    def scoped(self, q):
        keys = ("from", "to", "action", "direction", "outcome", "minSize", "maxSize", "minPosition",
                "minMinutes", "minAdds", "minReduces")
        # Eventual lifecycle outcomes and counts are hidden during causal review.
        future = {"outcome", "minMinutes", "minAdds", "minReduces", "direction"} if "clock" in q else set()
        if future and self.rows and "reported_pnl" in self.rows[0]:
            future.update(("action", "minPosition"))
        return self.filtered(tuple((k, q[k]) for k in keys if q.get(k) and k not in future))

    def describe(self, index, scope, clock=None):
        row = self.rows[index]
        rank = bisect_left(scope, index)
        life = self.positions.get(row.get("position"))
        if life and clock is not None:
            life = {k: life[k] for k in ("id", "symbol", "side", "entry_ms") if k in life}
        previous = self.rows[index - 1] if index else None
        if row.get("before") is not None:
            before = abs(row["before"])
            percent = (abs(row["filled"]) / before) * 100 if before else None
            if row["type"] == "reversal":
                percent = 100.
        else:
            percent = None
        if clock is not None and "reported_pnl" in row:
            row, life, percent = as_reported(row), None, None
        return {**row, "ordinal": index, "rank": rank if rank < len(scope) and scope[rank] == index else None,
                "previous_ms": previous["ms"] if previous else None, "position_info": life,
                "adjustment_pct": percent}

    def navigate(self, q):
        scope = self.scoped(q)
        if not scope:
            return {"row": None, "total": 0}
        current = self.indices.get(q.get("id"))
        direction = q.get("directionStep", "select")
        index = current
        if direction in {"first", "last", "rank"}:
            i = 0 if direction == "first" else len(scope) - 1 if direction == "last" else int(q["rank"])
            index = scope[min(max(0, i), len(scope) - 1)]
        elif direction in {"previous", "next"}:
            i = (bisect_left(scope, current) - 1 if direction == "previous" else bisect_right(scope, current)) if current is not None else (
                len(scope) - 1 if direction == "previous" else 0)
            index = scope[i] if 0 <= i < len(scope) else current
        elif direction in {"previousPosition", "nextPosition"}:
            if current is None:
                index = self.lifecycle_starts[0] if self.lifecycle_starts else scope[0]
            else:
                active = self.first.get(self.rows[current].get("position"), current)
                i = bisect_left(self.lifecycle_starts, active) - 1 if direction == "previousPosition" else bisect_right(self.lifecycle_starts, active)
                index = self.lifecycle_starts[i] if 0 <= i < len(self.lifecycle_starts) else current
        elif q.get("position"):
            index = self.first.get(q["position"])
        elif q.get("timestamp"):
            seek = bisect_left(scope, bisect_left(self.times, int(q["timestamp"])))
            index = scope[min(seek, len(scope) - 1)]
        elif not q.get("id"):
            index = scope[0]
        if index is None:
            return {"row": None, "total": len(scope)}
        clock = self.rows[index]["ms"] if "clock" in q else None
        return {"row": self.describe(index, scope, clock), "total": (bisect_right(scope, index) if clock is not None else len(scope))}

    def page(self, q):
        scope = self.scoped(q)
        through = int(q["clock"]) if "clock" in q else None
        chosen = self.indices.get(q.get("selected"))
        ceiling = len(self.rows) if through is None else (
            chosen + 1 if chosen is not None and self.times[chosen] == through else bisect_right(self.times, through))
        n = bisect_left(scope, ceiling)
        offset = max(0, int(q.get("offset", 0)))
        limit = min(200, max(1, int(q.get("limit", 120))))
        return {"rows": [self.describe(i, scope, through) for i in scope[offset:min(n, offset + limit)]], "total": n}


class ReplayWorkspace:
    def __init__(self, explorer, archive=ARCHIVE):
        self.explorer = explorer
        self.archive = Path(archive)
        self.market = data.open(explorer.source, universe=None)

    def archive_db(self):
        if not self.archive.is_file():
            raise ValueError("The local public order archive is unavailable")
        db = sqlite3.connect(self.archive.resolve().as_uri() + "?mode=ro", uri=True, timeout=5)
        db.execute("PRAGMA query_only=ON")
        return db

    def archive_version(self):
        # A refresh can include an old backfill that does not move the latest timestamp.
        with closing(self.archive_db()) as db:
            return tuple(db.execute("SELECT (SELECT count(*) FROM orders), (SELECT max(id) FROM pages)").fetchone())

    @lru_cache(maxsize=2)
    def native_tape(self, job, detail_id, symbol):
        ex = self.explorer
        record, detailed = ex.record(job), ex.record(detail_id)
        spec = record["specification"]
        _, index = _symbol(spec, {"symbol": symbol})
        paths = ex._paths(detailed)
        report = detailed["outcome"]["metrics"]
        settings = report["settings"]
        interval, lag = settings["interval"], settings["lag"]
        fills = sorted(ex._records(paths["fills"], index), key=lambda row: (row["ms"], row.get("id") or 0))
        rows = [execution(row, interval, lag) | {"symbol_name": symbol} for row in fills]
        funding = sorted(ex._records(paths["funding"], index), key=lambda row: row["ms"])
        positions, ledger, _ = reconstruct(symbol, rows, funding, report["per_symbol"][index], spec["end_ms"] + lag)
        add_native_fields(ledger, funding)
        return SymbolTape(ledger, [position_fields(row) for row in positions], funding)

    @lru_cache(maxsize=2)
    def external_tape(self, symbol, revision):
        del revision
        from research.lead_trader.summarize import lifecycles

        with closing(self.archive_db()) as db:
            db.execute("BEGIN")
            raw = db.execute("SELECT id,body FROM orders WHERE symbol=? ORDER BY ms,id", (symbol,)).fetchall()
            reports = [json.loads(r[0]) for r in db.execute("SELECT body FROM positions WHERE symbol=?", (symbol,))]
            windows = db.execute("SELECT lo,hi FROM tasks WHERE kind='order' AND state='complete' ORDER BY lo").fetchall()
        coverage = []
        for lo, hi in windows:
            if coverage and lo <= coverage[-1][1]:
                coverage[-1][1] = max(coverage[-1][1], hi)
            else:
                coverage.append([lo, hi])
        order_records = [json.loads(text) for _, text in raw]
        by_time = defaultdict(list)
        for ix, order in enumerate(order_records):
            by_time[order["orderUpdateTime"]].append(ix)
        allocations = defaultdict(list)
        lives = []
        lookup = {f"{r['positionId']}:{r['index']}": r for r in reports}
        if raw and reports:
            for path in lifecycles(pl.from_dicts(reports), pl.from_dicts(order_records)):
                actions = path["actions"]
                if not any(lo <= actions[0]["timestamp"] and actions[-1]["timestamp"] < hi for lo, hi in coverage):
                    continue
                if any(len(by_time[act["timestamp"]]) != 1 for act in actions):
                    continue
                key = actions[0]["id"]
                side = path["side"]
                opened, closed = lookup[key]["opened"], lookup[key]["closed"]
                for act in actions:
                    ix, = by_time[act["timestamp"]]
                    price = order_records[ix]["avgPrice"]
                    allocations[ix].append((key, side, act, price))
                lives.append(dict(id=key, symbol=symbol, side="long" if side > 0 else "short", state="closed",
                                  entry_ms=opened, exit_ms=closed, duration_ms=closed - opened, reported_pnl=path["pnl"],
                                  max_notional=path["peak_notional"], executions=len(actions), adds=path["adds"],
                                  reductions=path["reduces"], provenance="Reconstructed in verified order interval"))
        rows = []
        accum = 0.
        for ix, ((key, _), order) in enumerate(zip(raw, order_records)):
            price, qty = order["avgPrice"], order["executedQty"]
            sign = 1 if order["side"] == "BUY" else -1
            total = qty * price
            pnl = order.get("totalPnl")
            accum += pnl or 0.
            legs = allocations[ix]
            row = dict(id=key, ms=order["orderUpdateTime"], decision=order["orderTime"], symbol_name=symbol,
                       price=price, filled=sign * qty, filled_notional=total, side=order["side"].lower(),
                       type="realized" if pnl else "unclassified", reported_pnl=pnl, reported_cumulative=accum,
                       provenance="Reported execution; inventory unknown", positions=[])
            if len(legs) == 2 and math.isclose(sum(a[2]["size"] for a in legs), total, rel_tol=1e-7):
                old, new = sorted(legs, key=lambda a: a[2]["action"] != "close")
                if old[2]["action"] == "close" and new[2]["action"] == "open" and old[1] == -new[1]:
                    before, after = old[1] * old[2]["size"] / price, new[1] * new[2]["size"] / price
                    row.update(type="reversal", before=before, after=after, positions=[old[0], new[0]], position=new[0],
                               provenance="Reported execution; both reversal quantities reconciled")
            elif len(legs) == 1 and math.isclose(legs[0][2]["size"], total, rel_tol=1e-7):
                pos, side, action, _ = legs[0]
                after = side * (action["booked_notional"] / price if action["action"] != "close" else 0.)
                before = after - sign * qty
                direction = "long" if side > 0 else "short"
                kind = {"open": f"{direction}_entry", "add": "scale_in", "reduce": "scale_out",
                        "close": f"{direction}_exit"}[action["action"]]
                displacement = action["from_cost_bps"]
                cost = price / (1 + side * displacement / 10_000) if displacement is not None else None
                new_cost = (abs(before) * cost + total) / abs(after) if kind == "scale_in" and cost is not None and after else (
                    price if action["action"] == "open" else cost if after else None)
                row.update(type=kind, before=before, after=after, direction=direction, position=pos, positions=[pos],
                           entry_before=cost, entry_after=new_cost,
                           provenance="Reported execution; inventory quantity reconciled")
            if row.get("before") is not None:
                row["before_notional"], row["after_notional"] = row["before"] * price, row["after"] * price
            rows.append(row)
        confirmed = {p["id"] for p in lives}
        for key, report in lookup.items():
            if key in confirmed:
                continue
            closed, opened = report.get("closed"), report["opened"]
            lives.append({"id": key, "symbol": symbol, "side": report["side"].lower(), "entry_ms": opened,
                          "exit_ms": closed, "state": "reported only", "duration_ms": closed - opened if closed else None,
                          "reported_pnl": report.get("closingPnl"),
                          "provenance": "Published position summary; execution path unavailable"})
        return SymbolTape(rows, sorted(lives, key=lambda row: row["entry_ms"]))

    @lru_cache(maxsize=2)
    def external_meta(self, revision):
        with closing(self.archive_db()) as db:
            lo, hi = db.execute("SELECT min(ms),max(ms) FROM orders").fetchone()
            symbols = [r[0] for r in db.execute("""SELECT symbol FROM (
                SELECT symbol, count(*) AS fills FROM orders GROUP BY symbol
                UNION ALL SELECT symbol,0 FROM positions GROUP BY symbol
            ) GROUP BY symbol ORDER BY sum(fills) DESC,symbol""")]
            spans = []
            for a, b in db.execute("SELECT lo,hi FROM tasks WHERE kind='order' AND state='complete' ORDER BY lo"):
                if spans and spans[-1][1] >= a:
                    spans[-1][1] = max(b, spans[-1][1])
                else:
                    spans.append([a, b])
        if not symbols:
            raise ValueError("The public order archive is empty")
        return {"start": lo // STEP * STEP - 90 * 60_000, "end": (hi // STEP + 1) * STEP + 90 * 60_000,
                "symbols": symbols, "source": "external", "job": "binance", "detailed_job": "binance",
                "portfolio_id": PORTFOLIO, "revision": str(revision), "funding_warnings": [],
                "order_coverage": spans, "order_end": hi}

    @lru_cache(maxsize=512)
    def market_volume(self, symbol, lo, hi):
        left = max(RESEARCH_START, lo // STEP * STEP + (STEP if lo % STEP else 0))
        right = min(RESEARCH_END, hi // STEP * STEP)
        if left >= right:
            return None, 0., (right < hi)
        paths = list(self.market._paths(symbol, left, right))
        if not paths:
            return None, 0., False
        volume, count = (pl.scan_parquet(paths).filter(pl.col("timestamp_ms").is_between(left, right, closed="left"))
                         .select(pl.col("quote_volume").sum(), pl.len()).collect(engine="streaming").row(0))
        return (volume if count else None), count / max(1, (hi - lo) // STEP), False

    @lru_cache(maxsize=24)
    def watch(self, job, detail_id, revision, lo, hi, end, causal):
        if job == "binance":
            meta = self.external_meta(revision)
            with closing(self.archive_db()) as db:
                volumes = {r[0]: tuple(r[1:]) for r in db.execute("""
                    SELECT symbol,count(*),sum(json_extract(body,'$.avgPrice')*json_extract(body,'$.executedQty')),
                           sum(json_extract(body,'$.totalPnl'))
                    FROM orders WHERE ms>=? AND ms<=? GROUP BY symbol""", (lo, end))}
                counts = dict(db.execute("""SELECT symbol,count(*) FROM positions WHERE
                    json_extract(body,'$.status')='All Closed' AND json_extract(body,'$.closed') BETWEEN ? AND ?
                    GROUP BY symbol""", (lo, end)))
        else:
            record = self.explorer.record(job)
            meta = self.explorer.meta(record)
            volumes, counts = {}, {}
            if detail_id:
                detailed = self.explorer.record(detail_id)
                paths = self.explorer._paths(detailed)
                values = (_scan(paths["fills"], lo, end + 1).group_by("symbol").agg(
                    pl.len(), (pl.col("price") * pl.col("filled").abs()).sum(),
                    (pl.col("realized").sum() - pl.col("fee").sum()).alias("result"),
                    (pl.col("closed").gt(0) & ((pl.col("after") == 0)
                      | ((pl.col("after") - pl.col("filled")) * pl.col("after") < 0))).sum().alias("positions"))
                    .collect(engine="streaming"))
                volumes = {meta["symbols"][i]: (n, volume, pnl) for i, n, volume, pnl, _ in values.iter_rows()}
                counts = {meta["symbols"][i]: n for i, _, _, _, n in values.iter_rows()}
                funds = _scan(paths["funding"], lo, end + 1).group_by("symbol").agg(pl.col("payment").sum()).collect()
                for i, cash in funds.iter_rows():
                    name = meta["symbols"][i]
                    n, value, pnl = volumes.get(name, (0, 0., 0.))
                    volumes[name] = (n, value, pnl + cash)
        rows = []
        for symbol in meta["symbols"]:
            volume, coverage, _ = self.market_volume(symbol, lo, hi)
            count, executed, pnl = volumes.get(symbol, (0, 0., None))
            if causal and job == "binance" and not count:
                continue
            rows.append(dict(symbol=symbol, market=volume, coverage=coverage, count=count,
                             executed=executed, positions=counts.get(symbol, 0), pnl=pnl))
        return {"start": lo, "end": hi, "rows": rows}

    @lru_cache(maxsize=48)
    def candles(self, symbol, lo, hi, size, through):
        limit = min(RESEARCH_END, hi, ((through - self.market.availability_lag_ms) // STEP) * STEP if through is not None else hi)
        begin = max(RESEARCH_START, lo)
        completed = (limit // size) * size
        rows = {}
        if begin < completed and list(self.market._paths(symbol, begin, completed)):
            source = data.select(self.market, symbols=[symbol], start=begin, end=completed, fields=MARKET)
            blocks = data.iter_blocks(source)
            for block in (data.resample(blocks, size) if size != STEP else blocks):
                for i, stamp in enumerate(block.timestamps_ms):
                    status = int(block.status[i, 0])
                    row = dict(t=int(stamp), status=status, available_ms=int(block.available_at_ms[i]))
                    if status == 0:
                        row.update({k: float(block.values[k][i, 0]) for k in MARKET})
                    elif status == 1:
                        row["volume"] = 0.
                    rows[int(stamp)] = row
        left = max(begin, completed)
        if left < limit and list(self.market._paths(symbol, left, limit)):
            source = data.select(self.market, symbols=[symbol], start=left, end=limit, fields=MARKET)
            blocks = list(data.iter_blocks(source, block_steps=20_000))
            if blocks:
                import numpy as np
                flags = np.concatenate([b.status[:, 0] for b in blocks])
                traded = flags == 0
                flag = 3 if (flags == 3).any() else 4 if (flags == 4).all() else 2 if (flags >= 2).any() else 0 if traded.any() else 1
                stamp = left // size * size
                row = dict(t=stamp, status=flag, available_ms=int(blocks[-1].available_at_ms[-1]), partial=True)
                if flag == 0:
                    x = {k: np.concatenate([b.values[k][:, 0] for b in blocks])[traded] for k in MARKET}
                    row.update(open=float(x["open"][0]), high=float(max(x["high"])), low=float(min(x["low"])),
                               close=float(x["close"][-1]), volume=float(sum(x["volume"])))
                rows[stamp] = row
        till = hi if through is None else min(hi, (through // size + 1) * size)
        return {"start": lo, "end": hi, "step": size, "rows": [
            rows.get(t) or {"t": t, "status": 2, "available_ms": None} for t in range(lo, till, size)]}

    def window(self, job, original, detail, tape, symbol, q, meta):
        size = TIMEFRAMES[q.get("tf", "1m")] * 1000
        lo = int(q["start"]) // size * size
        hi = -(-int(q["end"]) // size) * size
        if lo >= hi or (hi - lo) // size > MAX_BUCKETS or hi < meta["start"] - size or lo > meta["end"] + size:
            raise ValueError("Choose at most 2,000 candle buckets around the recorded range")
        through = int(q["clock"]) if "clock" in q else None
        candles = self.candles(symbol, lo, hi, size, through)
        left, right = bisect_left(tape.times, lo), bisect_left(tape.times, hi)
        selected = tape.indices.get(q.get("selected"))
        if through is not None:
            right = min(right, selected + 1 if selected is not None and tape.times[selected] == through
                        else bisect_right(tape.times, through))
        limit = 30_000
        marker_rows = tape.rows[left:min(right, left + limit)]
        if selected is not None and left + limit <= selected < right:
            marker_rows = [*marker_rows, tape.rows[selected]]
        if job == "binance" and through is not None:
            marker_rows = [as_reported(row) for row in marker_rows]
        markers = [{k: row[k] for k in MARKER_FIELDS if k in row} for row in marker_rows]
        series = self.symbol_series(job, original, detail, tape, symbol, candles, through, selected, q)
        features = {"features": [], "series": {}}
        if original and (detail or not original["specification"].get("strategy")) and q.get("features") == "1":
            query = {"symbol": symbol, "tf": q.get("tf", "1m"), "start": max(lo, meta["start"]),
                     "end": min(hi, meta["end"], through if through is not None else hi)}
            if query["start"] < query["end"]:
                ex = self.explorer
                try:
                    features = ex.trace_series(original, detail, query) if original["specification"].get("strategy") else ex.feature_series(original, query)
                except ValueError as error:
                    features = {"features": [], "series": {}, "error": str(error)}
                if through is not None:
                    features = features | {"series": {k: [p for p in values if len(p) > 3 and p[3] is not None and p[3] <= through]
                                                        for k, values in features["series"].items()}}
        return {"candles": candles, "markers": markers, "marker_total": max(0, right - left), "series": series,
                "features": features}

    def symbol_series(self, job, original, detail, tape, symbol, candles, through, selected, q):
        points = {k: [] for k in ("qty", "notional", "entry", "initial", "breakeven", "unrealized", "realized", "net", "funding")}
        native = job != "binance"
        step = candles["step"]
        tape_times = tape.times
        position = self.explorer._paths(detail) if detail else {}
        native_marks = {}
        if native and detail and candles["rows"]:
            # Native marked values use the simulator's published mark provenance.
            lo = max(candles["start"], original["specification"]["start_ms"])
            lag = detail["outcome"]["metrics"]["settings"]["lag"]
            hi = min(candles["end"], original["specification"]["end_ms"],
                     (through - lag) // STEP * STEP if through is not None else candles["end"])
            if lo < hi and (hi - lo) // STEP <= 3_000_000:
                _, index = _symbol(original["specification"], {"symbol": symbol})
                mark = self.mark_series(original["id"], detail["id"], symbol, index, lo, hi, step)
                native_marks = {t: p for t, p in mark}
        for candle in candles["rows"]:
            stamp = candle["t"]
            clock = min(stamp + step - 1, through) if through is not None else stamp + step - 1
            i = bisect_right(tape_times, clock) - 1
            if through == clock and selected is not None and tape.times[selected] == clock:
                i = min(i, selected)
            event = tape.rows[i] if i >= 0 else {}
            life = tape.positions.get(event.get("position"), {})
            qty = event.get("after", 0. if native and detail and not event else None)
            if not native and (through is not None or (qty is not None and (not life or clock > life.get("exit_ms", 0)))):
                qty = None
            entry = event.get("entry_after") if qty else None
            mark = native_marks.get(stamp) if native else candle.get("close")
            notional = qty * mark if qty is not None and mark is not None else 0. if qty == 0 else None
            unrealized = qty * (mark - entry) if qty is not None and entry is not None and mark is not None else 0. if qty == 0 else None
            initial = tape.rows[tape.first[life["id"]]]["price"] if qty and life.get("id") in tape.first else None
            cash = tape.cash_totals[bisect_right(tape.cash_times, clock)] if native and detail else None
            net = event.get("net_cumulative", 0.) + cash - event.get("funding_cumulative", 0.) if cash is not None else None
            values = (qty, notional, entry, initial, event.get("breakeven"), unrealized,
                      event.get("gross_cumulative", 0.) if cash is not None else event.get("reported_cumulative"),
                      net, cash)
            for name, val in zip(points, values):
                points[name].append([stamp, finite(val)])
        if position and q.get("equity") == "1" and candles["rows"]:
            lo, hi = candles["start"], min(candles["end"], through + 1 if through is not None else candles["end"])
            if lo < hi:
                equity = _extrema(_scan(position["equity"], lo, hi, columns=["ms", "equity"]), ("equity",), lo, hi)["equity"]
                points["equity"] = [[t, value] for t, value in {ms // step * step: value for ms, value in equity}.items()]
        if candles["rows"]:
            lo = candles["rows"][0]["t"]
            hi = (candles["rows"][-1]["t"] + step - 1) if through is None else through
            left, right = bisect_left(tape_times, lo), bisect_right(tape_times, hi)
            if selected is not None and through is not None and tape.times[selected] == through:
                right = min(right, selected + 1)
            if right - left <= 30_000 and (not native or detail):
                self.transitions(points, tape, lo, hi, left, right, native, through, q, step)
        return points

    @staticmethod
    def transitions(points, tape, lo, hi, left, right, native, through, q, step):
        raw = tape.rows[left:right]
        prior = tape.rows[left - 1] if left else {}
        mapping = (("qty", "after"), ("entry", "entry_after"), ("breakeven", "breakeven"),
                   ("net", "net_cumulative"), ("realized", "gross_cumulative" if native else "reported_cumulative"))
        for field, key in mapping:
            if not native and field != "realized":
                continue  # Keep unobserved inventory intervals discontinuous.
            initial = prior.get(key, 0. if field in {"qty", "realized", "net"} and native else None)
            series = [[lo, initial], *[[row["ms"], row.get(key)] for row in raw]]
            series.append([hi, series[-1][1]])
            points[field + "_events"] = series
        if q.get("connections") == "1":
            connected = defaultdict(dict)
            for row in raw:
                if through is None or native:
                    for key in row.get("positions", ()):
                        connected[key][row["ms"] // step * step] = row["price"]
            for key, values in connected.items():
                if len(values) > 1:
                    points["connection:" + key] = [[ms, value] for ms, value in values.items()]

    @lru_cache(maxsize=24)
    def mark_series(self, job, detail_id, symbol, index, lo, hi, size):
        import numpy as np
        from simulator.feed import MarketDataFeed, TRADED

        ex = self.explorer
        settings = ex.record(detail_id)["outcome"]["metrics"]["settings"]
        feed = MarketDataFeed(ex.selection(ex.record(job)).dataset, [symbol], lo, hi, completed_clock="quantity_step" in settings)
        feed.read_threads = False
        values = {}
        for times, market, flags in feed.blocks(shared=True):
            marks = market[:, 0, 3]
            if settings["trade_mark_fallback"]:
                marks = np.where(np.isfinite(marks), marks, np.where(flags[:, 0] == TRADED, market[:, 0, 0], np.nan))
            slots = times // size * size
            ends = np.r_[np.flatnonzero(np.diff(slots)), len(slots) - 1]
            values.update((int(slots[j]), finite(float(marks[j]))) for j in ends)
        return tuple(values.items())

    def request(self, job, resource, q):
        original = detailed = revision = None
        if job == "binance":
            revision = self.archive_version()
            meta = self.external_meta(revision)
        else:
            original = self.explorer.record(job)
            meta = self.explorer.meta(original) | {"source": "native"}
            if resource != "meta" and meta.get("detailed_job"):
                detailed = self.explorer.record(meta["detailed_job"])
        if resource == "meta":
            return meta
        if resource == "watch":
            end = min(meta["end"], max(meta["start"], int(q.get("clock", q.get("anchor", meta.get("order_end", meta["end"]))))))
            # Historical buckets use the instant last requested by the viewer, with no live ticker.
            hi = (end - (self.market.availability_lag_ms if "clock" in q else 0)) // STEP * STEP
            seconds = {"1h": 3600, "24h": 86400, "7d": 604800}.get(q.get("period", "24h"))
            lo = meta["start"] if seconds is None else max(meta["start"], hi - seconds * 1000)
            return self.watch(job, meta["detailed_job"], revision, lo, hi, end, "clock" in q)
        symbol = q.get("symbol", meta["symbols"][0])
        if symbol not in meta["symbols"]:
            raise ValueError("Select a symbol in this replay")
        if detailed is None and job != "binance" and resource != "window":
            raise ValueError("Generate a detailed replay to inspect individual native fills")
        tape = self.external_tape(symbol, revision) if job == "binance" else (
            self.native_tape(job, detailed["id"], symbol) if detailed else SymbolTape([], []))
        if resource == "navigate":
            row = tape.navigate(q)
            selected = row["row"]
            if detailed and selected:
                _, index = _symbol(original["specification"], {"symbol": symbol})
                self.explorer.rationale(detailed, [selected], index)
                trace = self.explorer._paths(detailed).get("strategy_trace")
                plots = detailed["specification"].get("strategy", {}).get("plots", ())
                if trace and plots:
                    scan = _scan(trace, symbol=index)
                    names = [p["name"] for p in plots if p["name"] in scan.collect_schema().names()]
                    if names:
                        observed = scan.filter(pl.col("available_ms") == selected["decision"]).select(names).head(1).collect()
                        if observed.height:
                            selected["observations"] = {k: v for k, v in observed.row(0, named=True).items()
                                                        if isinstance(v, (float, int)) and math.isfinite(v)}
                lag = detailed["outcome"]["metrics"]["settings"]["lag"]
                right = (selected["ms"] - lag) // STEP * STEP
                if right > meta["start"]:
                    mark = self.mark_series(job, detailed["id"], symbol, index, right - STEP, right, STEP)
                    if mark and mark[0][1] is not None and selected.get("entry_after") is not None:
                        selected["unrealized"] = selected["after"] * (mark[0][1] - selected["entry_after"])
                if not selected.get("after"):
                    selected["unrealized"] = 0.
            return row
        if resource == "executions":
            return tape.page(q)
        if resource == "window":
            return self.window(job, original, detailed, tape, symbol, q, meta)
        if resource == "lifecycles":
            through = int(q["clock"]) if "clock" in q else None
            if job == "binance" and through is not None:
                return {"rows": [], "total": 0}
            selected = tape.indices.get(q.get("selected"), len(tape.rows))
            rows = tape.lives if through is None else [
                p if p["exit_ms"] is not None and (p["exit_ms"] < through or
                    (p["exit_ms"] == through and tape.last.get(p["id"], selected) <= selected)) else {
                    "id": p["id"], "symbol": symbol, "entry_ms": p["entry_ms"], "side": p["side"], "state": "open"}
                for p in tape.lives if p["entry_ms"] < through or (p["entry_ms"] == through and tape.first.get(p["id"], 0) <= selected)]
            if detailed and through is None and q.get("excursions") == "1":
                exact, *_ = self.explorer._exact_ledger(original, detailed, symbol)
                rows = [position_fields(p) for p in exact]
            offset = max(0, int(q.get("offset", 0)))
            rank = next((i for i, row in enumerate(rows) if row["id"] == q.get("anchor")), None)
            return {"rows": rows[offset:offset + 150], "total": len(rows), "rank": rank}
        raise KeyError(resource)
