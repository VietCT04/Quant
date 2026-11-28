"""Pre-execution features and exact outgoing lifecycle actions."""
import argparse
from bisect import bisect_left
from collections import Counter, defaultdict
from contextlib import closing
from datetime import UTC, datetime
import json
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import polars as pl
import quant as q

from quant.replay_workspace import ReplayWorkspace
from . import align
from .behavior_study import table
from .decision_evidence import grouped_frames

DAY=86400000
STEP=5000
CHOICES={"add","reduce","reverse"}
FIELDS=align.FIELDS
FAST=("ret_5s","ret_15s","ret_1m","ret_5m","ret_15m","ret_1h","acceleration_1m",
      "range_rebound5m","range_drawdown5m","flow_1m","activity_1m","trade_intensity1m","vol_5m")


class MarketTape:
    def __init__(self,symbol,source,extra,first,last):
        self.start=first//DAY*DAY
        end=(last//DAY+1)*DAY
        steps=(end-self.start)//STEP
        self.status=np.full(steps,q.data.MISSING,dtype=np.int8)
        self.values={k:np.full(steps,np.nan) for k in FIELDS}
        self.source=np.zeros(steps,dtype=np.int8)
        left,right=max(self.start,align.START),min(end,align.END)
        if left<right and (source.path/f"symbol={symbol}").is_dir():
            sel=q.data.select(source,start=left,end=right,symbols=(symbol,),fields=FIELDS)
            for b in q.data.iter_blocks(sel,block_steps=32768):
                ix=(b.timestamps_ms-self.start)//STEP
                self.source[ix]=np.where(b.status[:,0]<=q.data.EMPTY,1,0)
                self.status[ix]=b.status[:,0]
                for k in FIELDS:
                    self.values[k][ix]=b.values[k][:,0]
        for day in range(self.start,end,DAY):
            date=datetime.fromtimestamp(day/1000,UTC).date()
            p=extra/f"{symbol}-{date}.parquet"
            if not p.exists():
                continue
            df=pl.read_parquet(p)
            ix=(df["timestamp_ms"].to_numpy()-self.start)//STEP
            take=(ix>=0)&(ix<steps)
            ix=ix[take]
            use=self.source[ix]==0
            self.status[ix[use]]=np.where(df["is_empty"].to_numpy()[take][use],q.data.EMPTY,q.data.TRADED)
            self.source[ix[use]]=2
            for k in FIELDS:
                self.values[k][ix[use]]=df[k].to_numpy()[take][use]
        self.lag=source.availability_lag_ms

    def index(self,stamp):
        return (stamp-self.start-self.lag)//STEP-1

    def observations(self,times):
        ix=self.index(times)
        valid=(self.status<=q.data.EMPTY)&np.isfinite(self.values["close"])
        if not valid.any():
            return {}
        values=self.values
        with np.errstate(divide="ignore",invalid="ignore"):
            features=align.completed_features(times,self.start,self.status,values,self.lag)
        ok=(ix>0)&(ix<len(valid))
        ii=np.clip(ix,1,len(valid)-1)
        ok &= valid[ii]&valid[ii-1]
        close=values["close"]
        features["ret_5s"]=np.divide(close[ii],close[ii-1],out=np.full(len(ix),np.nan),where=ok)-1
        j=np.clip(ii-13,0,len(valid)-1)
        pp=align.prefix(~valid)
        reversal=(ii>=13)&(pp[ii+1]==pp[j])
        features["turn_5s_1m"]=np.where(reversal,
            np.sign(features["ret_5s"])*np.sign(close[ii-1]-close[j]),np.nan)
        return {int(t):dict(**{k:float(features[k][i]) for k in (*FAST,"observable_close","turn_5s_1m")},
                            price_source=int(self.source[ii[i]]))
                for i,t in enumerate(times) if (0<=ix[i]<len(valid) and valid[ix[i]])}

    def extrema(self,opened,price,sign,stamps):
        if not stamps:
            return {}
        start=(opened+STEP-1-self.start)//STEP
        last=min(self.index(max(stamps)),len(self.status)-1)
        if start<0 or last<start or start>=len(self.status):
            return {}
        good=self.status[start:last+1]<=q.data.EMPTY
        vals=self.values["high" if sign>0 else "low"][start:last+1]
        choose=np.maximum if sign>0 else np.minimum
        path=choose.accumulate(np.r_[price,vals])
        covered=np.logical_and.accumulate(good&np.isfinite(vals))
        return {t:float(path[self.index(t)-start+1]) for t in stamps
                if start<=self.index(t)<=last and covered[self.index(t)-start]}


def prebook(legs,raw,i,stamp):
    prior=legs[:i]
    if not prior:
        return {}
    last=prior[-1]
    opened=prior[0]
    sign=1 if opened["after"]>0 else -1
    adds=[p for p in prior if p["action"]=="add"]
    trims=[p for p in prior if p["action"]=="reduce"]
    trailing=[p for p in prior if p["ms"]>=stamp-15*60_000]
    trim_streak=0
    for leg in reversed(prior):
        if leg["action"]!="reduce":
            break
        trim_streak+=1
    peak=max(abs(z["after"]) for z in prior)
    qty=last["after"]
    same=[p for p in reversed(trailing) if p["action"]=="reduce"]
    recent=next(iter(same),None)
    delta=qty-(prior[-len(trailing)-1]["after"] if len(prior)>len(trailing) else 0.)
    return dict(held_side=sign,age_s=(stamp-opened["ms"])/1000,gap_s=(stamp-last["ms"])/1000,
        count_add=len(adds),count_reduce=len(trims),trim_streak=trim_streak,qty=qty,peak_fraction=abs(qty)/peak,
        qty_change_15m=sign*delta,prev_kind="reverse_open" if last["action"]=="open" and last["reversal"] else last["action"],
        prev_cash=last["clip"],prev_raw_cash=raw[last["id"]]["clip"],prev_px=last["price"],
        init_px=opened["price"],cost_px=last["basis"],
        prev_add_px=adds[-1]["price"] if adds else None,prev_reduce_px=trims[-1]["price"] if trims else None,
        best_booked_px=(max if sign>0 else min)(z["price"] for z in prior),
        clip_cover=recent["clip"]/recent["price"]/abs(qty) if recent and qty else np.nan,
        pnl_15m=sum(raw[p["id"]]["totalPnl"] for p in trailing if p["action"]=="reduce"),
        previous_qty=last["before"], previous_ms=last["ms"])


def priced(pre,mark,bench,best):
    out={}
    if not mark or not np.isfinite(mark.get("observable_close",np.nan)):
        return out
    px=mark["observable_close"]
    s=pre["held_side"]
    vol=mark["vol_5m"]
    out.update({k:s*mark[k] if k.startswith(("ret_","flow_","acceleration")) else mark[k] for k in FAST})
    out["turn_5s_1m"]=mark["turn_5s_1m"]
    out["price_source"]=mark["price_source"]
    out["mark_px"]=px
    out["exposure_usdt"]=abs(pre["qty"])*px
    for k,ref in (("from_previous","prev_px"),("from_add","prev_add_px"),("from_reduce","prev_reduce_px"),
                  ("from_initial","init_px"),("from_cost","cost_px"),("from_best_booked","best_booked_px")):
        if pre.get(ref):
            out[k]=s*np.log(px/pre[ref])
    if pre.get("cost_px"):
        out["unrealized_usdt"]=pre["qty"]*(px-pre["cost_px"])
    if best:
        out["from_best_market"]=s*np.log(px/best)
    if vol>0:
        out["prior_displacement_vol"]=out["from_previous"]/vol
    if bench:
        out["btc_relative_1m"]=s*(mark["ret_1m"]-bench["ret_1m"])
        out["btc_relative_5m"]=s*(mark["ret_5m"]-bench["ret_5m"])
    return out


def prepare(source,decisions,market,extra,out):
    _,orders,legs,lives=grouped_frames(source)
    hourly=pl.read_parquet(decisions/"decision-clocks.parquet").filter(pl.col("risk").is_in(["size","hazard"]))
    by_event={(r["life_id"],r["ms"],r["risk"],r["action"]):r for r in hourly.to_dicts()}
    ds=q.data.open(market,universe=None)
    alltimes=hourly["ms"].to_numpy()
    btc=MarketTape("BTCUSDT",ds,extra,int(alltimes.min()),int(alltimes.max()))
    benchmark=btc.observations(np.unique(alltimes))
    events=[]
    minutes=[]
    for symbol,paths in sorted(lives.items()):
        raw={o["id"]:o for o in orders[symbol]}
        todo=[]
        for path in paths:
            life=path["life_id"]
            seq=legs[life]
            ts=[p["ms"] for p in seq]
            if len(set(ts))!=len(ts):
                continue
            # The order with a reversal is counted once on the outgoing side.
            for k,row in enumerate(seq[1:],1):
                order=raw[row["id"]]
                stamp=min(order["orderTime"],row["ms"])
                n=bisect_left(ts,stamp)
                if n!=k:
                    continue
                kind="reverse" if row["action"]=="close" and row["reversal"] else row["action"]
                original="reverse_close" if kind=="reverse" else kind
                prior=prebook(seq,raw,n,stamp)
                result=dict(action=kind,ms=stamp,book_ms=row["ms"],order_id=row["id"],event_cash=row["clip"],
                            whole_cash=order["clip"],fill_px=row["price"],after_qty=row["after"],
                            incoming_qty=abs(order["executedQty"])-abs(prior["qty"]) if kind=="reverse" else np.nan,
                            realized_report=order["totalPnl"],**prior)
                todo.append((life,"size",original,result))
            for stamp in range((ts[0]//60000+1)*60000,ts[-1]+1,60000):
                n=bisect_left(ts,stamp)
                kinds={p["action"] for p in seq[n:bisect_left(ts,stamp+60000)]}
                kind=("reverse" if seq[-1]["reversal"] else "close") if "close" in kinds else (
                      "hold" if not kinds else next(iter(kinds)) if len(kinds)==1 else "mixed")
                todo.append((life,"hazard",kind,dict(action=kind,ms=stamp,**prebook(seq,raw,n,stamp))))
        times=np.unique([r["ms"] for _,_,_,r in todo])
        if not len(times):
            continue
        tape=MarketTape(symbol,ds,extra,int(times[0]),int(times[-1]))
        quotes=tape.observations(times)
        bylife=defaultdict(list)
        for life,_,_,row in todo:
            bylife[life].append(row["ms"])
        extremes={}
        for life,stamps in bylife.items():
            opening=legs[life][0]
            extremes[life]=tape.extrema(opening["ms"],opening["price"],np.sign(opening["after"]),stamps)
        for life,risk,original,row in todo:
            ms=row["ms"]
            marketrow=by_event.get((life,ms,risk,original),{})
            proxy={k:marketrow.get(k) for k in ("r1","r4","r24","relq","vol_rank","alt_residual","residual","oi_change","funding",
                   "prior_orders_15m","last_order_usdt","exposure")}
            proxy["hour_price"]=marketrow.get("mark_hour")
            if proxy["hour_price"] and np.isfinite(proxy["hour_price"]):
                proxy["hour_from_cost"]=row["held_side"]*np.log(proxy["hour_price"]/row["cost_px"]) if row["cost_px"] else np.nan
                proxy["hour_exposure"]=abs(row["qty"])*proxy["hour_price"]
            row.update(proxy)
            row.update(priced(row,quotes.get(ms),benchmark.get(ms),extremes[life].get(ms)))
            row.update(symbol=symbol,life_id=life,risk=risk)
            (events if risk=="size" else minutes).append(row)
    table(out,"executions",events)
    table(out,"minutes",minutes)
    return events,minutes


def reversal_audit(source,out,market):
    reader=ReplayWorkspace(SimpleNamespace(source=market),source/"archive.sqlite")
    revision=reader.archive_version()
    rows,trace=[],[]
    ds=q.data.open(market,universe=None)
    counts=Counter()
    with closing(reader.archive_db()) as db:
        symbols=[r[0] for r in db.execute("SELECT DISTINCT symbol FROM orders")]
    for sym in symbols:
        tape=reader.external_tape(sym,revision)
        counts.update(r["action_certainty"] for r in tape.rows)
        trace_start=len(trace)
        for ix,row in enumerate(tape.rows):
            if row["type"]!="reversal":
                continue
            outgoing,incoming=row["positions"]
            prior=[]
            for p in reversed(tape.rows[max(0,ix-8):ix]):
                if outgoing not in p.get("positions",()):
                    break
                prior.append(p)
            exact=row.get("before") is not None and row["quantity_certainty"]=="confirmed"
            report=tape.positions[outgoing]
            rows.append(dict(symbol=sym,id=row["id"],ms=row["ms"],out=outgoing,incoming=incoming,
                             confirmed=exact,opening_seen=report.get("opening_execution_id") is not None,
                             full_path=report["completeness"]=="complete",prior_linked=len(prior),
                             before=row.get("before"),after=row.get("after"),price=row["price"],cash=row["filled_notional"]))
            for j,p in enumerate([*reversed(prior),row]):
                trace.append(dict(reversal_id=row["id"],step=j,ms=p["ms"],
                                  clock=min(p["ms"],p.get("decision") or p["ms"]),symbol=sym,
                                  life_id=outgoing,order_id=p["id"],action=p["type"],price=p["price"],
                                  before=p.get("before"),after=p.get("after"),certainty=p["quantity_certainty"],
                                  inventory_until=p.get("inventory_until")))
        section=trace[trace_start:]
        times=np.unique([r["clock"] for r in section])
        if len(times):
            sampled=MarketTape(sym,ds,out/"five-second",int(times[0]),int(times[-1])).observations(times)
            for r in section:
                r.update(sampled.get(r["clock"],{}))
    (out/"archive-classification.json").write_text(json.dumps(counts,indent=2),encoding="utf-8")
    table(out,"reversal-audit",rows)
    table(out,"reversal-neighbors",trace)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("out",type=Path)
    parser.add_argument("--source",type=Path,default=Path("data/research/lead-trader/behavior-2026-11-28"))
    parser.add_argument("--decisions",type=Path,default=Path("data/research/lead-trader/decision-study-v2-expanded"))
    parser.add_argument("--market",type=Path,default=Path("data/binance/um/bars_5s"))
    parser.add_argument("--audit",action="store_true")
    a=parser.parse_args()
    a.out.mkdir(parents=True,exist_ok=True)
    if a.audit:
        reversal_audit(a.source,a.out,a.market)
    else:
        e,m=prepare(a.source,a.decisions,a.market,a.out/"five-second",a.out)
        print("Action events",len(e),"verified minute rows",len(m),flush=True)


if __name__=="__main__":
    main()
