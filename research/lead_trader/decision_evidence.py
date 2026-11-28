"""Causal covariates and separate decision risk sets for the public lead-trader archive."""

from bisect import bisect_left, bisect_right
from collections import defaultdict
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import urlopen
from zipfile import ZipFile
from io import BytesIO
import time

import numpy as np
import polars as pl

from . import align
from .behavior_market import DAY, HOUR, FEATURES, lag, market, spells, trailing
from .behavior_study import MINUTE

MAJORS = {"BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "XRPUSDT"}
HIST = ("prior_orders_15m","prior_orders_60m","last_gap","last_order_usdt","last_order_realized",
        "prior_realized_15m","last_side","prior_buy_fraction","prior_fill_move","position_age","direction")
STATE = ("exposure","signed_quantity","distance_to_cost","inventory_change_15m","peak_fraction",
         "inventory_return","inventory_mark_pnl")
FAST = ("ret_15s","ret_1m","ret_5m","ret_15m","ret_1h","acceleration_1m","range_rebound5m","range_drawdown5m",
        "flow_1m","flow_5m","activity_1m","activity_5m","vol_5m","trade_intensity1m","trade_accel1m")
HOURLY = (*FEATURES, "notional_jump","acceleration1h","day_rebound","day_drawdown","week_return",
          "quote_rel_day","trade_rel_day","oi","oi_change","funding","alt_residual","btc_beta","btc_beta_change",
          "alt_beta","alt_beta_change","alt_corr","coin_vol","alt24")
CARRY = ("prior_episodes","known_active_days","last_reported_activity_days")


def positive_log(x):
    return float(np.log1p(max(0., x)))


def folded_beta(x,y,n):
    ok=np.isfinite(x)&np.isfinite(y)
    x,y=(np.where(ok,a,np.nan) for a in (x,y))
    a,b,aa,bb,ab=(trailing(v,n) for v in (x,y,x*x,y*y,x*y))
    cov=ab-a*b/n
    v=bb-b*b/n
    vx=aa-a*a/n
    with np.errstate(invalid="ignore",divide="ignore"):
        return np.where(v>0,cov/v,np.nan), np.where((v>0)&(vx>0),cov/np.sqrt(v*vx),np.nan)


def aux_lookup(symbols, funding_dir, metrics_dir):
    fund={}
    metrics={}
    for sym in symbols:
        path=funding_dir/(sym+".parquet")
        if path.exists():
            df=pl.read_parquet(path)
            millisecond=next((k for k in ("funding_time_ms","fundingTime","timestamp_ms","timestamp","time","ms") if k in df.columns),None)
            value=next((k for k in ("funding_rate","fundingRate","rate") if k in df.columns),None)
            if millisecond and value:
                df=df.sort(millisecond)
                fund[sym]=(df[millisecond].to_numpy(),df[value].to_numpy())
        path=metrics_dir/(sym+".parquet")
        if path.exists():
            df=pl.read_parquet(path).sort("ms")
            metrics[sym]=(df["ms"].to_numpy(),df["oi"].to_numpy())
    return fund,metrics


def public_metrics(target, symbols, dates):
    """Download small official daily metric archives only, with serial conservative pacing."""
    target.mkdir(parents=True,exist_ok=True)
    base="https://data.binance.vision/data/futures/um/daily/metrics"
    from urllib.parse import quote
    results={}
    for sym in symbols:
        rows=[]
        for day in dates:
            safe=quote(sym,safe="")
            path=target/f"{sym}-{day}.zip"
            if not path.exists():
                try:
                    time.sleep(.2)
                    with urlopen(f"{base}/{safe}/{safe}-metrics-{day}.zip",timeout=10) as response:
                        path.write_bytes(response.read())
                except HTTPError as error:
                    results[f"{sym}/{day}"]=error.code
                    if error.code in (418,429):
                        return results
                    continue
                except (URLError,TimeoutError) as error:
                    results[f"{sym}/{day}"]=str(error)
                    return results
            with ZipFile(path) as z, z.open(z.namelist()[0]) as f:
                t=pl.read_csv(f,columns=["create_time","sum_open_interest"]).drop_nulls()
            t=t.with_columns(pl.col("create_time").str.to_datetime(time_zone="UTC").dt.epoch("ms").alias("ms"))
            rows.append(t.select("ms",pl.col("sum_open_interest").cast(pl.Float64).alias("oi")).filter(pl.col("oi")>0))
            results[f"{sym}/{day}"]=t.height
        if rows:
            pl.concat(rows).unique("ms").sort("ms").write_parquet(target/(sym+".parquet"))
    return results


class Hourly:
    def __init__(self,folder, funding, metrics, extra=None):
        self.symbols,self.fields,_,_,self.origin=market(folder,extra)
        self.index={s:j for j,s in enumerate(self.symbols)}
        self.n=self.fields["q24"].shape[1]
        self.funding,self.metrics=aux_lookup(self.symbols,funding,metrics)
        self.populate(folder,extra)

    def populate(self,folder,extra):
        n=self.n
        shape=(len(self.symbols),n)
        cl, high, low, quote, trades=(np.full(shape,np.nan,dtype="f4") for _ in range(5))
        for j,s in enumerate(self.symbols):
            path=folder/(s+".parquet")
            if not path.exists() and extra is not None:
                path=extra/(s+".parquet")
            t=pl.read_parquet(path,columns=["ms","close","high","low","quote","trades"])
            ix=(t["ms"].to_numpy()-self.origin)//HOUR
            ok=(ix>=0)&(ix<n)
            for name,arr in zip(("close","high","low","quote","trades"),(cl,high,low,quote,trades)):
                arr[j,ix[ok]]=t[name].to_numpy()[ok]
        self.close=cl
        with np.errstate(invalid="ignore",divide="ignore"):
            logs=np.log(cl)
            raw=np.column_stack((np.full(shape[0],np.nan),np.diff(logs,axis=1)))
        alt=np.array([s not in MAJORS for s in self.symbols])
        clipped=np.clip(raw,-.30,.30)
        sums=np.nansum(clipped[alt],axis=0)
        counts=np.isfinite(clipped[alt]).sum(axis=0)
        btc=raw[self.index["BTCUSDT"]]
        for k in HOURLY:
            if k not in self.fields and k not in {"funding","oi","oi_change"}:
                self.fields[k]=np.full(shape,np.nan,dtype="f4")
        with np.errstate(invalid="ignore",divide="ignore"):
            for j in range(shape[0]):
                used=alt[j]&np.isfinite(clipped[j])
                bench=np.where(counts-used>=30,(sums-np.where(used,clipped[j],0))/(counts-used),np.nan)
                alt_beta,alt_corr=folded_beta(raw[j],bench,168)
                btc_beta,_=folded_beta(raw[j],btc,168)
                residual=raw[j]-lag(alt_beta,24)*bench
                ahead24=trailing(bench,24)
                top=pl.Series(high[j]).fill_nan(None).rolling_max(24).to_numpy()
                bottom=pl.Series(low[j]).fill_nan(None).rolling_min(24).to_numpy()
                q24=self.fields["q24"][j]
                t24=trailing(trades[j],24)
                data={
                    "alt24":np.expm1(ahead24),"alt_residual":np.expm1(trailing(residual,24)),
                    "btc_beta":btc_beta,"btc_beta_change":btc_beta-lag(btc_beta,168),
                    "alt_beta":alt_beta,"alt_beta_change":alt_beta-lag(alt_beta,168),"alt_corr":alt_corr,
                    "acceleration1h":raw[j]-lag(raw[j],1),"day_rebound":cl[j]/bottom-1.,
                    "day_drawdown":cl[j]/top-1.,"week_return":np.expm1(logs[j]-lag(logs[j],168)),
                    "quote_rel_day":q24/(lag(trailing(quote[j],168),24)/7),
                    "trade_rel_day":t24/(lag(trailing(trades[j],168),24)/7),
                    "notional_jump":quote[j]/(lag(q24,1)/24),"coin_vol":np.sqrt(trailing(residual**2,24)),
                }
                for key,col in data.items():
                    self.fields[key][j]=np.where(np.isfinite(col),col,np.nan)

    def observation(self,sym,t):
        j=self.index.get(sym)
        i=(t-self.origin-1)//HOUR-1
        if j is None or not 0<=i<self.n or not np.isfinite(self.fields["q_rank"][j,i]):
            return {}
        data={key:float(self.fields[key][j,i]) for key in self.fields if key in HOURLY}
        data["mark_hour"]=float(self.close[j,i])
        if sym in self.funding:
            times,values=self.funding[sym]
            a=bisect_left(times,t)-1
            if a>=0 and t-times[a]<=DAY:
                data["funding"]=float(values[a])
        if sym in self.metrics:
            times,values=self.metrics[sym]
            a,b=(bisect_left(times,t-offset)-1 for offset in (5*MINUTE,65*MINUTE))
            if b>=0 and t-times[a]<=30*MINUTE and t-times[b]<=90*MINUTE and values[b]>0:
                data["oi"]=float(values[a])
                data["oi_change"]=float(values[a]/values[b]-1)
        return data


def symbol_daily(positions,hourly):
    episodes=defaultdict(list)
    for z in spells(positions):
        episodes[z["symbol"]].append(z)
    by_symbol=defaultdict(list)
    for p in positions:
        by_symbol[p["symbol"]].append(p)
    for rs in by_symbol.values():
        rs.sort(key=lambda p:p["opened"])
    first=(min(p["updateTime"] for p in positions)+8*DAY)//DAY*DAY
    last=(max(p["updateTime"] for p in positions)-7*DAY)//DAY*DAY
    for t in range(first,last,DAY):
        for sym in hourly.symbols:
            obs=hourly.observation(sym,t)
            if not obs:
                continue
            ep=episodes[sym]
            existing=[z for z in ep if z["start"]<t]
            rs=by_symbol[sym]
            recent=[p for p in rs if p["opened"]<t and (not p["closed"] or p["closed"]>=t-7*DAY)]
            # Prior report activity uses actual opens and closes at or before the clock.
            prior=max([p["opened"] for p in rs if p["opened"]<t]+
                      [p["closed"] for p in rs if p["closed"] and p["closed"]<t],default=None)
            meta=dict(ms=t,symbol=sym,prior_episodes=len(existing),
                      known_active_days=positive_log((t-existing[-1]["start"])/DAY) if existing else np.nan,
                      last_reported_activity_days=positive_log((t-prior)/DAY) if prior else np.nan)
            if not recent:
                incoming=next((z for z in ep if t<=z["start"]<t+DAY),None)
                yield meta|obs|dict(risk="activation",target=int(incoming is not None),
                                    entry_side=(incoming and next((p["direction"] for p in rs if p["opened"]==incoming["start"]),None)))
            elif any(p["opened"]<t and (not p["closed"] or p["closed"]>=t) for p in recent):
                end=any(t<=z["end"]<t+DAY and not z["partial"] for z in ep)
                yield meta|obs|dict(risk="deactivation",target=int(end),entry_side=None)


def rfeatures(r,side):
    return {k:(float(r[k])*side if k.startswith(("ret_","flow_","acceleration")) else float(r[k]))
            for k in FAST if k in r}


def pre_order(orders,t,known_from):
    # An archive gap invalidates counts over any lookback that crosses it.
    recent=[r for r in orders if t-r["orderUpdateTime"]<=15*MINUTE]
    last=orders[-1] if orders else None
    res=dict(prior_orders_15m=len(recent),prior_orders_60m=len(orders),
             prior_buy_fraction=sum(r["side"]=="BUY" for r in orders)/len(orders) if orders else np.nan,
             prior_realized_15m=float(sum(r["totalPnl"] for r in recent)))
    if known_from>t-HOUR:
        res.pop("prior_orders_60m")
        res.pop("prior_buy_fraction")
    if known_from>t-15*MINUTE:
        res.pop("prior_orders_15m")
        res.pop("prior_realized_15m")
    if last:
        res.update(last_gap=positive_log((t-last["orderUpdateTime"])/1000),
                   last_order_usdt=positive_log(last["clip"]),last_side=1 if last["side"]=="BUY" else -1,
                   last_order_realized=float(np.arcsinh(last["totalPnl"])))
    return res,last


def history_at(raw,times,t,windows,starts):
    index=bisect_right(starts,t)-1
    if index<0 or t>=windows[index][1]:
        return {},None
    known=windows[index][0]
    return pre_order(raw[bisect_left(times,max(t-HOUR,known)):bisect_left(times,t)],t,known)


def reported_openings(positions,hourly):
    opposites={(p["symbol"],-p["direction"],p["closed"]) for p in positions if p["closed"]}
    start=min(p["updateTime"] for p in positions)+7*DAY
    for p in positions:
        stamp=p["opened"]
        if stamp<start or any((p["symbol"],p["direction"],stamp+delta) in opposites for delta in (-1,0,1)):
            continue
        seen=hourly.observation(p["symbol"],stamp)
        if seen:
            yield seen | dict(symbol=p["symbol"],ms=stamp,life_id=p["life_id"],target=int(p["direction"]<0))


def grouped_frames(source):
    folder=Path(source)
    pos=pl.read_parquet(folder/"positions.parquet").to_dicts()
    orders=pl.read_parquet(folder/"orders.parquet").to_dicts()
    legs=pl.read_parquet(folder/"confirmed-legs.parquet").to_dicts()
    complete=pl.read_parquet(folder/"complete-trajectories.parquet").to_dicts()
    by_ord=defaultdict(list)
    by_leg=defaultdict(list)
    by_complete=defaultdict(list)
    for r in orders: by_ord[r["symbol"]].append(r)
    for r in legs: by_leg[r["life_id"]].append(r)
    for r in complete: by_complete[r["symbol"]].append(r)
    return pos,by_ord,by_leg,by_complete


def candidate_clocks(positions, by_ord, by_leg, by_complete, windows):
    """Return one minute of future action and execution-only size labels on disjoint clocks."""
    opens=defaultdict(list)
    starts=[lo for lo,_ in windows]
    for p in positions:
        opens[p["symbol"]].append(p)
    for sym,lives in by_complete.items():
        raw=by_ord[sym]
        times=[r["orderUpdateTime"] for r in raw]
        raw_by_id={o["id"]:o for o in raw}
        rows=[]
        for life in lives:
            legs=by_leg[life["life_id"]]
            ts=[k["ms"] for k in legs]
            side=1 if life["side"]=="long" else -1
            peak_prefix=np.maximum.accumulate([abs(r["after"]) for r in legs])
            for t in range((ts[0]//MINUTE+1)*MINUTE,ts[-1]+1,MINUTE):
                i=bisect_left(ts,t)-1
                end=bisect_left(ts,t+MINUTE)
                kind={k["action"] for k in legs[i+1:end]}
                label=("reverse" if legs[-1]["reversal"] else "close") if "close" in kind else (
                    "hold" if not kind else next(iter(kind)) if len(kind)==1 else "mixed")
                rows.append(clock(life,legs,ts,peak_prefix,raw,times,t,side,label,"hazard",i,windows,starts))
            for j,leg in enumerate(legs):
                t=min(leg["ms"],raw_by_id[leg["id"]]["orderTime"])
                i=bisect_left(ts,t)-1
                kind=leg["action"]
                label="reverse_open" if kind=="open" and leg["reversal"] else "reverse_close" if kind=="close" and leg["reversal"] else kind
                row=clock(life,legs,ts,peak_prefix,raw,times,t,side,label,"size",i,windows,starts)
                whole=raw_by_id[leg["id"]]
                row.update(order_usdt=whole["clip"],leg_usdt=leg["clip"], after_usdt=abs(leg["after"])*leg["price"],
                           after_booked=leg["after"],decision_book_ms=leg["ms"],order_id=leg["id"])
                if j==0 and leg["clip"]<=20:
                    row["growth"]=int(life["peak_proxy"]>=100)
                rows.append(row)
        # Verified flat interval between a pure exit and the next pure lifecycle start.
        # Certified order windows alone do not establish the starting balance.
        pure=[(r["ms"],r) for life in lives for r in by_leg[life["life_id"]]
              if r["action"]=="close" and not r["reversal"]]
        for boundary,_ in sorted(pure,key=lambda p:p[0]):
            j=bisect_right(times,boundary)
            k=bisect_right(starts,boundary)-1
            if k<0 or boundary>=windows[k][1]:
                continue
            end=windows[k][1]
            nxt=raw[j] if j<len(raw) and times[j]<end else None
            next_ms=nxt["orderUpdateTime"] if nxt else end
            matched=[p for p in opens[sym] if abs(p["opened"]-next_ms)<=1 and
                     p["direction"]*(1 if nxt["side"]=="BUY" else -1)==1] if nxt else []
            verified_next=bool(len(matched)==1 and nxt["totalPnl"]==0)
            for stamp in range((boundary//MINUTE+1)*MINUTE,next_ms+1,MINUTE):
                if next_ms<stamp+MINUTE:
                    if not verified_next:
                        continue
                    label="long_entry" if matched[0]["direction"]>0 else "short_entry"
                else:
                    label="hold_flat"
                if stamp+MINUTE>end and label=="hold_flat":
                    continue
                hist,_=history_at(raw,times,stamp,windows,starts)
                rows.append(hist|dict(symbol=sym,ms=stamp,direction=0,risk="flat",action=label,
                    life_id=f"{sym}:{boundary}",position_age=positive_log((stamp-boundary)/1000)))
        yield sym, rows


def clock(life,legs,ts,peak,raw,times,t,side,action,risk,i,windows,starts):
    hist,last=history_at(raw,times,t,windows,starts)
    row=hist|dict(symbol=life["symbol"],life_id=life["life_id"],ms=t,action=action,risk=risk,direction=side)
    if i>=0:
        prev=legs[i]
        past_idx=bisect_left(ts,t-15*MINUTE)-1
        basis=prev["basis"]
        row.update(position_age=positive_log((t-ts[0])/1000), booked_before=float(prev["after"]),
                   entry_basis=basis,peak_fraction=abs(prev["after"])/peak[i] if peak[i]>0 else np.nan,
                   qty_change=float(prev["after"]-(legs[past_idx]["after"] if past_idx>=0 else 0.)))
    if last:
        row["last_price"]=last["avgPrice"]
    return row


def finalize_clock(rows,hourly,dataset,symbol):
    unique=sorted({r["ms"] for r in rows})
    vals={i:hourly.observation(symbol,t) for i,t in enumerate(unique)}
    fast={}
    if (dataset.path/f"symbol={symbol}").is_dir():
        fast=align.sample(dataset,symbol,unique)
    mapping={stamp:i for i,stamp in enumerate(unique)}
    for r in rows:
        i=mapping[r["ms"]]
        f=fast.get(i,{})
        direction=r["direction"]
        market=vals[i]
        signed=direction if direction else 1
        state={}
        observed=f.get("observable_close",market.get("mark_hour"))
        if observed is None or not np.isfinite(observed):
            observed=market.get("mark_hour")
        if observed is not None and np.isfinite(observed):
            if r.get("last_price"):
                state["prior_fill_move"]=signed*(observed/r["last_price"]-1)
            if r.get("booked_before") and r.get("entry_basis"):
                quantity=r["booked_before"]
                state.update(exposure=positive_log(abs(quantity)*observed),signed_quantity=float(np.arcsinh(quantity)),
                             distance_to_cost=signed*(observed/r["entry_basis"]-1),
                             inventory_mark_pnl=float(np.arcsinh(quantity*(observed-r["entry_basis"]))),
                             inventory_change_15m=float(np.arcsinh(signed*r["qty_change"]*observed)))
        yield r|market|rfeatures(f,signed)|state|dict(has_hourly=bool(market),
                       has_fast=bool(f and np.isfinite(f.get("ret_1m",np.nan))))


def reversal_context(rows):
    # Reversal size is an incoming target with a real outgoing inventory at decision time.
    # Two exact lifecycle legs can identify it without using any post-order state.
    closes=defaultdict(list)
    for row in rows:
        if row["risk"]=="size" and row["action"]=="reverse_close":
            key=row.get("order_id") or (row["symbol"],row["decision_book_ms"])
            closes[key].append(row)
    for row in rows:
        if row["risk"]=="size" and row["action"]=="reverse_open":
            key=row.get("order_id") or (row["symbol"],row["decision_book_ms"])
            prior=closes[key]
            if len(prior)==1 and prior[0]["ms"]==row["ms"] and prior[0]["direction"]==-row["direction"]:
                for field in (*STATE,"position_age"):
                    if prior[0].get(field) is not None:
                        row[field]=prior[0][field]
                row["outgoing_context"]=True
    return rows
