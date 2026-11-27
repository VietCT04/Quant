from __future__ import annotations

import math
from datetime import UTC, datetime

import numpy as np
import polars as pl
import pyarrow as pa
import pyarrow.parquet as pq

from marketdata.model import DAY, replace_file, write_json


def plain(value):
    if isinstance(value, np.generic):
        value = value.item()
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return value


def record(row):
    return {field: plain(row[field]) for field in row.dtype.names}


class Logs:
    def __init__(self, folder, symbols):
        self.folder, self.symbols = folder, symbols
        self.products = {}
        folder.mkdir(parents=True, exist_ok=True)
        if (folder / "report.json").exists():
            raise ValueError(f"Run report already exists in {folder}")

    def append(self, name, block):
        cols = {}
        for field in block.dtype.names:
            cols[field] = pa.array(block[field], from_pandas=True)
        board = pa.table(cols)
        if name not in self.products:
            file = self.folder / f"{name}.part"
            self.products[name] = pq.ParquetWriter(file, board.schema, compression="zstd")
        if len(block):
            self.products[name].write_table(board, row_group_size=65536)

    def close(self, commit):
        for name, writer in self.products.items():
            writer.close()
            if commit:
                replace_file(self.folder / f"{name}.part", self.folder / f"{name}.parquet")


class SummaryLogs:
    """Retain small summary ledgers in memory and spill large ones to the audit format."""

    def __init__(self, folder, symbols, limit):
        self.folder, self.symbols, self.limit = folder, symbols, limit
        self.chunks = {}
        self.bytes = 0
        self.disk = None

    def append(self, name, block):
        if self.disk is None and self.bytes + block.nbytes > self.limit:
            self.disk = Logs(self.folder, self.symbols)
            for key, chunks in self.chunks.items():
                for chunk in chunks:
                    self.disk.append(key, chunk)
            self.chunks.clear()
        if self.disk is not None:
            self.disk.append(name, block)
        else:
            chunks = self.chunks.setdefault(name, [])
            if len(block) or not chunks:
                chunks.append(block)
                self.bytes += block.nbytes

    def close(self, commit):
        if self.disk is not None:
            self.disk.close(commit)
        if not commit:
            self.chunks.clear()

    def lifecycles(self, positions, end):
        records = None
        if self.disk is None:
            records = []
            for name in ("fills", "funding"):
                chunks = self.chunks[name]
                rows = np.concatenate(chunks) if len(chunks) > 1 else chunks[0]
                records.append(pl.DataFrame({key: rows[key] for key in rows.dtype.names}))
            self.chunks.clear()
        return order_lifecycles(self.folder, self.symbols, positions, end, records=records)


def performance(folder, settings, final, positions, feed, *, daily=None):
    wallet = settings.initial_wallet
    if daily is None:
        eq = pl.read_parquet(folder / "equity.parquet")
        eq = eq.with_columns(((pl.col("ms") - settings.lag - 1) // DAY).alias("day"))
        days = eq.group_by("day", maintain_order=True).agg(pl.col("equity").last())
        closed = closed_reductions(folder)
    else:
        days = pl.DataFrame({"day": list(daily), "equity": list(daily.values())})
        closed = None
    endpoint = np.r_[wallet, days["equity"].to_numpy()]
    returns = np.divide(endpoint[1:], endpoint[:-1],
                        out=np.full(len(endpoint)-1, np.nan), where=endpoint[:-1] > 0) - 1
    complete = returns[np.isfinite(returns)]
    factors = max((feed.end - getattr(feed, "trading_start", feed.start)) / DAY, 1 / 86400)
    equity = final["equity"]
    ann = (equity / wallet) ** (365 / factors) - 1 if equity > 0 and factors >= 1 else None
    ret = equity / wallet - 1
    dd = final["risk_drawdown"]
    from quant.performance import sharpe as sharpe_ratio, sortino as sortino_ratio
    sharpe = sharpe_ratio(complete).item() if len(complete) > 1 else None
    sortino = sortino_ratio(complete).item() if np.any(complete < 0) else None
    monthly, prior = {}, wallet
    for day, amount in days.iter_rows():
        key = datetime.fromtimestamp(day * DAY / 1000, UTC).strftime("%Y-%m")
        monthly[key] = monthly.get(key, 0) + amount - prior
        prior = amount
    states = []
    for i, row in enumerate(positions):
        fields = record(row)
        fields["symbol"] = feed.symbols[i]
        fields["quote_pnl"] = fields["realized"] + fields["unrealized"] + fields["funding"] - fields["fees"]
        fields["mark_missing_slots"] = int(feed.mark_missing_slots[i])
        fields["bar_states"] = dict(zip(("traded", "empty", "missing", "quarantined", "prelisting"),
                                        feed.status_counts[:, i].tolist()))
        states.append(fields)
    sides = {}
    for side, direction in (("long", 1), ("short", -1)):
        sides[side] = {
            "realized_pnl": sum(x[side + "_realized"] for x in states),
            "funding": sum(x[side + "_funding"] for x in states),
            "fees": sum(x[side + "_fee"] for x in states),
            "spread_slippage_impact": sum(x[side + "_cost"] - x[side + "_fee"] for x in states),
            "open_pnl": sum(x["unrealized"] for x in states if x["qty"] * direction > 0),
        }
    return {
        "bar_range_ms": [getattr(feed, "trading_start", feed.start), feed.end], "symbols": list(feed.symbols),
        "sampling": "last UTC daily snapshot, initial wallet as the first denominator, 365 days/year",
        "sampling_count": len(complete), "end": final,
        "daily_marked_equity": [[day * DAY, amount] for day, amount in days.iter_rows()],
        "net_pnl": equity - wallet,
        "gross_pnl_before_execution_cost_fees_and_funding": equity - wallet + final["fees"] + final["slippage"] - final["funding"],
        "total_return": ret, "annualized_return": plain(ann), "sharpe": plain(sharpe), "sortino": plain(sortino),
        "maximum_drawdown": dd, "calmar": plain(ann/dd) if ann is not None and dd and len(days) > 1 else None,
        "closed_reductions": closed,
        "orders": sum(x["orders"] for x in states), "fills": sum(x["fills"] for x in states),
        "open_position_count": sum(x["qty"] != 0 for x in states),
        "long_short": sides, "per_symbol": states, "monthly_marked_pnl": monthly,
        "execution_costs": {"fees": final["fees"], "spread_slippage_impact": final["slippage"], "funding": final["funding"]},
        "funding_warnings": feed.funding_warnings,
        "mark_warnings": [x["symbol"] for x in states if x["mark_missing_slots"]],
        "indicative_equity_warning": any(x["held_unmarked_ticks"] for x in states),
        "outputs": {"order_reason_codes": {"0": "filled", "1": "partial", "2": "risk", "3": "lot, participation or minimum"}},
    }


def closed_reductions(folder):
    closes = pl.scan_parquet(folder / "fills.parquet").filter(pl.col("closed") > 0)
    p = pl.col("closed_net")
    closed = closes.select(
        pl.len().alias("count"), (p > 0).sum().alias("wins"),
        p.filter(p > 0).sum().alias("wins_sum"), p.filter(p < 0).sum().alias("losses_sum"),
        p.filter(p > 0).mean().alias("average_win"), p.filter(p < 0).mean().alias("average_loss"),
        p.skew(bias=False).alias("skewness"), p.top_k(5).clip(lower_bound=0).sum().alias("top5"),
        pl.col("hold_ms").median().alias("median_hold_ms"), pl.col("hold_ms").quantile(.1).alias("p10_hold_ms"),
        pl.col("hold_ms").quantile(.9).alias("p90_hold_ms"), p.sum().alias("net_closed"),
    ).collect(engine="streaming").row(0, named=True)
    closed = {key: plain(value) for key, value in closed.items()}
    return closed | {
        "profit_factor": closed["wins_sum"] / -closed["losses_sum"] if closed["losses_sum"] < 0 else None,
        "win_rate": closed["wins"] / closed["count"] if closed["count"] else None,
        "top5_winner_share": closed["top5"] / closed["wins_sum"] if closed["wins_sum"] > 0 else None,
        "net_without_top5": closed["net_closed"] - closed["top5"],
    }


def order_lifecycles(folder, symbols, positions, end, *, records=None):
    """Summarize completed lifecycles using the same ledger grouping as Explorer."""
    from quant.explorer_ledger import reconstruct

    net, duration, returns = [], [], []
    details = {}
    entries_by_day = {}
    paths = [folder / f"{table}.parquet" for table in ("fills", "funding")]

    def uncompressed(path):
        with pq.ParquetFile(path) as file:
            m = file.metadata
            return sum(m.row_group(i).total_byte_size for i in range(m.num_row_groups))

    # Read and partition small event logs once instead of decoding every row group per symbol.
    if records is not None:
        grouped = [table.partition_by("symbol", as_dict=True) for table in records]
    else:
        grouped = ([pl.read_parquet(path).partition_by("symbol", as_dict=True) for path in paths]
                   if sum(map(uncompressed, paths)) <= 64 << 20 else None)
    for i, symbol in enumerate(symbols):
        if grouped is None:
            queries = (pl.scan_parquet(path).filter(pl.col("symbol") == i) for path in paths)
            fills, flows = (query.collect(engine="streaming").iter_rows(named=True) for query in queries)
        else:
            fills, flows = (group[(i,)].iter_rows(named=True) if (i,) in group else () for group in grouped)
        entries, _, _ = reconstruct(symbol, fills, flows, record(positions[i]), end, detail=False)
        closed = [row for row in entries if row["state"] == "closed"]
        for row in closed:
            day = row["entry_ms"] // DAY * DAY
            entries_by_day[day] = entries_by_day.get(day, 0) + 1
        net.extend(row["net_pnl"] for row in closed)
        duration.extend(row["duration_ms"] for row in closed)
        returns.extend(row["return_on_opening_notional"] for row in closed)
        details[symbol] = {"completed_positions": len(closed), "net_closed": sum(row["net_pnl"] for row in closed)}
    pnl = np.asarray(net)
    wins, losses = pnl[pnl > 0], pnl[pnl < 0]
    counts, edges = np.histogram(pnl, bins=20) if len(pnl) else ([], [])
    return {"count": len(pnl), "wins": len(wins), "losses": len(losses),
            "entries_by_utc_day": [[day, count] for day, count in sorted(entries_by_day.items())],
            "profit_factor": float(wins.sum() / -losses.sum()) if len(losses) else None,
            "win_rate": len(wins) / len(pnl) if len(pnl) else None,
            "average_win": float(wins.mean()) if len(wins) else None,
            "average_loss": float(losses.mean()) if len(losses) else None,
            "expectancy": float(pnl.mean()) if len(pnl) else None,
            "holding_ms": dict(zip(("p10", "median", "p90"), np.percentile(duration, (10, 50, 90)).tolist())) if duration else {},
            "return_quantiles": dict(zip(("p10", "median", "p90"), np.percentile(returns, (10, 50, 90)).tolist())) if returns else {},
            "net_distribution": {"edges": list(edges), "counts": [int(x) for x in counts]},
            "per_symbol": details}
