"""Study Binance public evidence without contacting Binance or the collector worker."""

import argparse
from bisect import bisect_left
from collections import defaultdict
from contextlib import closing
from hashlib import sha256
import json
from pathlib import Path
import sqlite3
from types import SimpleNamespace

import numpy as np
import polars as pl
import quant as q

from quant.replay_workspace import ARCHIVE, ReplayWorkspace
from . import align
from .behavior_market import leadership, market, openings, selection, transfer


MINUTE = 60_000
DAY = 24 * 60 * MINUTE


def frozen(source, target):
    if source.resolve() == target.resolve():
        return
    with closing(sqlite3.connect(source.resolve().as_uri() + "?mode=ro", uri=True)) as old, closing(sqlite3.connect(target)) as new:
        old.backup(new)


def read_positions(db):
    positions = []
    for body, in db.execute("SELECT body FROM positions"):
        p = json.loads(body)
        positions.append(p | {"life_id": f"{p['positionId']}:{p['index']}",
                              "direction": 1 if p["side"] == "Long" else -1,
                              "peak_proxy": p["maxOpenInterest"] * p["avgCost"]})
    return positions


def distribution(values):
    x = np.asarray(values, dtype=float)
    x = x[np.isfinite(x)]
    return dict(n=len(x), p10=float(np.quantile(x,.1)), median=float(np.median(x)), p90=float(np.quantile(x,.9))) if len(x) else {"n":0}


def sum_pnl(positions):
    pnl = sorted((float(row["closingPnl"]) for row in positions), reverse=True)
    result = {"count": len(pnl), "total":sum(pnl), "median":float(np.median(pnl)),
              "win_pct":float(np.mean(np.asarray(pnl)>0)), "top5":sum(pnl[:5]), "bottom5":sum(pnl[-5:]),
              "without_top5":sum(pnl[5:]), "without_top20":sum(pnl[20:])}
    for label, sign in (("win",1),("loss",-1)):
        exposed = [r for r in positions if r["closingPnl"]*sign>0]
        result[label] = dict(peak=distribution([r["peak_proxy"] for r in exposed]),
                            hours=distribution([(r["closed"]-r["opened"])/3_600_000 for r in exposed]))
    return result


def archived_orders(db):
    for key, body in db.execute("SELECT id,body FROM orders ORDER BY ms,id"):
        r = json.loads(body)
        yield {"id":key, **r, "clip":r["executedQty"]*r["avgPrice"]}


def coverage_tapes(reader, orders):
    by_sym = sorted({r["symbol"] for r in orders})
    revision = reader.archive_version()
    inventory = []
    life_rows = []
    opportunities = defaultdict(list)
    flow = []
    for symbol in by_sym:
        tape = reader.external_tape(symbol, revision)
        paths = defaultdict(list)
        for row in tape.rows:
            for key in row["positions"]:
                paths[key].append(row)
        for life in tape.lives:
            key = life["id"]
            fills = paths[key]
            if not fills:
                continue
            if life["completeness"] == "complete":
                order_marks = []
                peak = 0.
                for row in fills:
                    after = 0. if row["id"] == life["closing_execution_id"] else row["after"]
                    before = row.get("before")
                    if row["id"] == life["opening_execution_id"]:
                        kind = "open"
                        quantity = abs(after)
                    elif row["id"] == life["closing_execution_id"]:
                        kind = "close"
                        quantity = abs(before)
                    else:
                        kind = {"scale_in":"add", "scale_out":"reduce"}[row["type"]]
                        quantity = abs(row["filled"])
                    peak = max(peak,abs(after))
                    order_marks.append({"id":row["id"], "life_id":key, "symbol":symbol, "ms":row["ms"],
                                        "action":kind, "clip":quantity*row["price"], "price":row["price"],
                                        "after":after, "before":0. if kind=="open" else before,
                                        "basis_before":row.get("entry_before"), "basis":row.get("entry_after"),
                                        "reversal":row["type"]=="reversal", "confidence":"confirmed"})
                life_rows.append(dict(life_id=key,symbol=symbol,side=life["side"],opened=fills[0]["ms"],closed=fills[-1]["ms"],
                    pnl=life["reported_pnl"], first_clip=order_marks[0]["clip"], peak_qty=peak, peak_proxy=life["max_notional"],
                    additions=sum(r["action"]=="add" for r in order_marks),
                    reductions=sum(r["action"]=="reduce" for r in order_marks),
                    max_add=max((r["clip"] for r in order_marks if r["action"]=="add"),default=0.),
                    added_cash=sum(r["clip"] for r in order_marks if r["action"]=="add"),
                    reversals=sum(r["reversal"] for r in order_marks)))
                flow.extend(order_marks)
                if (reader.market.path / f"symbol={symbol}").is_dir():
                    opportunity(opportunities[symbol],life,order_marks,fills)
            else:
                inventory.append(dict(life_id=key,symbol=symbol,side=life["side"],classification=life["completeness"],
                    orders=len(fills), known_quantity=sum(r.get("after") is not None for r in fills),
                    confirmed_quantity=sum(r["quantity_certainty"]=="confirmed" for r in fills),
                    first_qty=life["first_inventory_before"], opening_qty=life["opening_quantity"],
                    peak_observed_qty=max((abs(r["after"]) for r in fills if r.get("after") is not None and
                                           r["id"]!=life["closing_execution_id"]),default=None),
                    close_pnl=life["reported_pnl"]))
    return inventory, life_rows, flow, opportunities


def opportunity(target, life, marks, fills):
    first, last = marks[0]["ms"], marks[-1]["ms"]
    times = [r["ms"] for r in marks]
    for t in range((first//MINUTE+1)*MINUTE,last+1,MINUTE):
        i, j = bisect_left(times,t), bisect_left(times,t+MINUTE)
        prior = marks[i-1]
        if fills[i-1].get("inventory_until",0)<t or not prior["after"] or not prior["basis"]:
            continue
        kinds = {r["action"] for r in marks[i:j]}
        if "close" in kinds:
            action = "reverse" if marks[-1]["reversal"] else "close"
        else:
            action = "hold" if not kinds else next(iter(kinds)) if len(kinds)==1 else "mixed"
        target.append(dict(life_id=life["id"],symbol=life["symbol"],side=1 if life["side"]=="long" else -1,
            ms=t,action=action,qty=prior["after"],basis=prior["basis"],elapsed_ms=t-first,since_fill_ms=t-prior["ms"]))


def market_opportunities(dataset, cohorts):
    out = []
    for symbol, rows in cohorts.items():
        found = align.sample(dataset,symbol,[r["ms"] for r in rows])
        for j, obs in found.items():
            if not np.isfinite(obs["ret_1m"]) or not np.isfinite(obs["observable_close"]):
                continue
            row = rows[j]
            side = row["side"]
            out.append(row | {"signed_prior_minute":1e4*side*obs["ret_1m"],
                "signed_prior_5m":1e4*side*obs["ret_5m"],
                "signed_flow":side*obs["flow_1m"],
                "signed_inventory_mark_bps":1e4*side*(obs["observable_close"]/row["basis"]-1),
                "activity":obs["activity_1m"],"realized_vol_5m":obs["vol_5m"],
                "marked_inventory_abs":abs(row["qty"])*obs["observable_close"]})
    return out


def close_to_open(positions):
    heads = {(r["symbol"],r["direction"],r["opened"]) for r in positions}
    return {r["life_id"] for r in positions if r.get("closed") and any(
        (r["symbol"],-r["direction"],r["closed"]+d) in heads for d in (-1,0,1))}


def table(out, name, rows):
    if rows:
        pl.from_dicts(rows,infer_schema_length=None,strict=False).write_parquet(out / (name+".parquet"))


def basic(positions, orders, inventory, lives, flow):
    closed = [r for r in positions if r["status"]=="All Closed" and r["closed"]]
    by_side = {side:sum_pnl([r for r in closed if r["side"]==side]) for side in ("Long","Short")}
    symbol_pnl = defaultdict(float)
    for r in closed:
        symbol_pnl[r["symbol"]]+=r["closingPnl"]
    top = sorted(closed,key=lambda r:r["closingPnl"],reverse=True)
    return dict(positions=len(positions), closed=len(closed), orders=len(orders), closed_symbols=len(symbol_pnl),
                order_symbols=len({r["symbol"] for r in orders}), paired_boundaries=len(close_to_open(closed + [p for p in positions if p["status"]!="All Closed"])),
                complete_lives=len(lives), partial_lives=len(inventory), confirmed_single_order_reversals=len({r["id"] for r in flow if r["reversal"]}),
                clips=distribution([r["clip"] for r in orders]), outcomes=by_side,
                top_symbols=sorted(symbol_pnl.items(),key=lambda v:v[1],reverse=True)[:10],
                bottom_symbols=sorted(symbol_pnl.items(),key=lambda v:v[1])[:10],
                selected_closed={s:sum(r["symbol"]==s for r in closed) for s in ("BTCUSDT","ETHUSDT","SOLUSDT","BNBUSDT","XRPUSDT","DOGEUSDT","BTWUSDT")},
                small_clip_large_lives=distribution([r["first_clip"] for r in lives if r["peak_proxy"]>=1000]),
                best=[r["life_id"] for r in top[:10]], worst=[r["life_id"] for r in top[-10:]])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("out",type=Path)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--market-only",action="store_true",help="Refresh market joins using the frozen position table")
    mode.add_argument("--inventory-only",action="store_true",help="Rebuild trajectory and minute evidence from the frozen SQLite archive")
    parser.add_argument("--archive",type=Path,default=ARCHIVE)
    parser.add_argument("--hourly",type=Path,default=Path("data/research/lead-trader/market-hourly"))
    parser.add_argument("--audited",type=Path,default=Path("data/binance/um/bars_5s"))
    args=parser.parse_args()
    out=args.out
    out.mkdir(parents=True,exist_ok=True)
    if args.market_only:
        run_market(out, args.hourly, pl.read_parquet(out/"positions.parquet").to_dicts())
        return
    snapshot=out/"archive.sqlite"
    if not args.inventory_only:
        frozen(args.archive,snapshot)
    reader=ReplayWorkspace(SimpleNamespace(source=args.audited),snapshot)
    with closing(reader.archive_db()) as db:
        orders=list(archived_orders(db))
        positions=read_positions(db)
        windows=list(map(list,db.execute("SELECT lo,hi FROM tasks WHERE kind='order' AND state='complete' ORDER BY lo")))
    for name, rows in (("orders",orders),("positions",positions)):
        table(out,name,rows)
    print("Archived orders and positions",len(orders),len(positions),flush=True)
    inventory,lives,flow,cohorts=coverage_tapes(reader,orders)
    for name, rows in (("partial-trajectories",inventory),("complete-trajectories",lives),("confirmed-legs",flow)):
        table(out,name,rows)
    print("Complete trajectories and potential minute opportunities",len(lives),sum(map(len,cohorts.values())),flush=True)
    table(out,"minutes",market_opportunities(q.data.open(args.audited,universe=None),cohorts))
    summary=basic(positions,orders,inventory,lives,flow)
    summary.update(snapshot_sha256=sha256(snapshot.read_bytes()).hexdigest(), archive_revision=reader.archive_version(),
                   order_first=orders[0]["orderUpdateTime"],order_last=orders[-1]["orderUpdateTime"],
                   position_first=min(p["updateTime"] for p in positions),position_last=max(p["updateTime"] for p in positions),
                   order_windows=windows)
    (out/"archive-summary.json").write_text(json.dumps(summary,indent=2,allow_nan=False),encoding="utf-8")
    if not args.inventory_only:
        run_market(out, args.hourly, positions)


def run_market(out, hourly, positions):
    symbols,f,future,btc,origin=market(hourly)
    print("Matched market universe",len(symbols),flush=True)
    for name, rows in (("asset-events",selection(positions,symbols,f)),("opening-events",openings(positions,symbols,f,future)),
                       ("market-markouts",transfer(symbols,f,future,btc,origin)),
                       ("market-leadership",leadership(symbols,f,future,origin))):
        table(out,name,rows)
        print(name,len(rows),flush=True)


if __name__=="__main__":
    main()
