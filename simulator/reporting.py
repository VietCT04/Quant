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


def performance(folder, settings, final, positions, feed):
    wallet = settings.initial_wallet
    eq = pl.read_parquet(folder / "equity.parquet")
    eq = eq.with_columns(((pl.col("ms") - settings.lag - 1) // DAY).alias("day"))
    days = eq.group_by("day", maintain_order=True).agg(pl.col("equity").last())
    endpoint = np.r_[wallet, days["equity"].to_numpy()]
    returns = np.divide(endpoint[1:], endpoint[:-1],
                        out=np.full(len(endpoint)-1, np.nan), where=endpoint[:-1] > 0) - 1
    complete = returns[np.isfinite(returns)]
    factors = max((feed.end - feed.start) / DAY, 1 / 86400)
    equity = final["equity"]
    ann = (equity / wallet) ** (365 / factors) - 1 if equity > 0 and factors >= 1 else None
    ret = equity / wallet - 1
    dd = eq["risk_drawdown"].max()
    from quant.performance import sharpe as sharpe_ratio, sortino as sortino_ratio
    sharpe = sharpe_ratio(complete).item() if len(complete) > 1 else None
    sortino = sortino_ratio(complete).item() if np.any(complete < 0) else None
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
        "bar_range_ms": [feed.start, feed.end], "symbols": list(feed.symbols),
        "sampling": "last UTC daily snapshot, initial wallet as the first denominator, 365 days/year",
        "sampling_count": len(complete), "end": final,
        "net_pnl": equity - wallet,
        "gross_pnl_before_execution_cost_fees_and_funding": equity - wallet + final["fees"] + final["slippage"] - final["funding"],
        "total_return": ret, "annualized_return": plain(ann), "sharpe": plain(sharpe), "sortino": plain(sortino),
        "maximum_drawdown": dd, "calmar": plain(ann/dd) if ann is not None and dd and len(days) > 1 else None,
        "closed_reductions": closed | {
            "profit_factor": closed["wins_sum"] / -closed["losses_sum"] if closed["losses_sum"] < 0 else None,
            "win_rate": closed["wins"] / closed["count"] if closed["count"] else None,
            "top5_winner_share": closed["top5"] / closed["wins_sum"] if closed["wins_sum"] > 0 else None,
            "net_without_top5": closed["net_closed"] - closed["top5"],
        },
        "orders": sum(x["orders"] for x in states), "fills": sum(x["fills"] for x in states),
        "open_position_count": sum(x["qty"] != 0 for x in states),
        "long_short": sides, "per_symbol": states, "monthly_marked_pnl": monthly,
        "execution_costs": {"fees": final["fees"], "spread_slippage_impact": final["slippage"], "funding": final["funding"]},
        "funding_warnings": feed.funding_warnings,
        "mark_warnings": [x["symbol"] for x in states if x["mark_missing_slots"]],
        "indicative_equity_warning": any(x["held_unmarked_ticks"] for x in states),
        "outputs": {"order_reason_codes": {"0": "filled", "1": "partial", "2": "risk", "3": "lot, participation or minimum"}},
    }
