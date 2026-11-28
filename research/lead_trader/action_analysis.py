"""Competing action probabilities, validation, reversal geometry and sequence evidence."""
import argparse
from collections import defaultdict, Counter
from hashlib import sha256
import json
from pathlib import Path
from urllib.parse import urlencode

import numpy as np
import polars as pl
from scipy.optimize import minimize
from scipy.special import logsumexp

from .action_evidence import DAY
from .decision_fit import Design, values

ORDER=("add","reduce","reverse")
STATE=("age_s","gap_s","count_add","count_reduce","trim_streak","peak_fraction","abs_qty",
       "marked_size","prev_cash","prev_raw_cash","qty_change_15m","pnl_15m","prior_orders_15m",
       "prev_is_add","prev_is_reduce","prev_is_reversal","prior_clip_cover")
PRICE=("from_previous","from_add","from_reduce","from_initial","from_cost","from_best_booked",
       "from_best_market","inventory_pnl","ret_5s","ret_15s","ret_1m","ret_5m","ret_15m","ret_1h",
       "r4","r24","prior_displacement_vol","turn_5s_1m","range_rebound5m","range_drawdown5m",
       "activity_1m","flow_1m","vol_5m","acceleration_1m","trade_intensity1m","btc_relative_5m","alt_residual")
SIGNALS=(
    ("previous add","prev_is_add",">=",.5),("previous reduce","prev_is_reduce",">=",.5),
    ("previous reverse","prev_is_reversal",">=",.5),("previous fill within 5s","gap_s","<=",5.),
    ("previous fill within 30s","gap_s","<=",30.),("no fill for 5 minutes","gap_s",">=",300.),
    ("at least 3 consecutive trims","trim_streak",">=",3.),
    ("below quarter of prior quantity peak","peak_fraction","<=",.25),("above three quarters of prior peak","peak_fraction",">=",.75),
    ("last reduce clip could cross zero","clip_cover",">=",1.),("last reduce clip below half remaining","clip_cover","<=",.5),
    ("inventory under 25 USDT","exposure_usdt","<=",25.),("inventory above 250 USDT","exposure_usdt",">=",250.),
    ("previous order at least 100 USDT","prev_raw_cash",">=",100.),
    ("adverse from last fill at least 25 bp","from_previous","<=",-.0025),("favorable from last fill at least 25 bp","from_previous",">=",.0025),
    ("adverse from last add at least 25 bp","from_add","<=",-.0025),("favorable from last add at least 25 bp","from_add",">=",.0025),
    ("adverse from last trim at least 25 bp","from_reduce","<=",-.0025),("favorable from last trim at least 25 bp","from_reduce",">=",.0025),
    ("adverse vs first entry at least 100 bp","from_initial","<=",-.01),("adverse vs weighted entry at least 100 bp","from_cost","<=",-.01),
    ("favorable vs weighted entry at least 100 bp","from_cost",">=",.01),
    ("adverse from best market level at least 200 bp","from_best_market","<=",-.02),
    ("adverse since previous fill at least 1 realized volatility","prior_displacement_vol","<=",-1.),
    ("favorable since previous fill at least 1 realized volatility","prior_displacement_vol",">=",1.),
    ("adverse 5-second return at least 10 bp","ret_5s","<=",-.001),("favorable 5-second return at least 10 bp","ret_5s",">=",.001),
    ("adverse 5-minute return at least 50 bp","ret_5m","<=",-.005),("favorable 5-minute return at least 50 bp","ret_5m",">=",.005),
    ("positive 24h return in position direction","r24",">=",0.),("negative 24h return in position direction","r24","<=",0.),
    ("altcoin adjusted move in position direction","alt_residual",">=",0.),("5s turn against prior minute","turn_5s_1m","<=",-.5),
    ("recent turnover above 1.5x","activity_1m",">=",1.5),
)
AXES=(("from_previous","exposure_usdt"),("from_cost","peak_fraction"),("from_previous","gap_s"),
      ("ret_5m","r24"),("unrealized_usdt","exposure_usdt"),("prior_displacement_vol","qty_change_15m"),
      ("trim_streak","clip_cover"),("from_add","from_reduce"))


def features(t):
    return t.with_columns(pl.col("qty").abs().log1p().alias("abs_qty"),
        pl.coalesce(pl.col("exposure_usdt"),pl.col("hour_exposure")).log1p().alias("marked_size"),
        *[(pl.col("prev_kind")==label).cast(pl.Int8).alias(flag)
          for label,flag in (("add","prev_is_add"),("reduce","prev_is_reduce"),("reverse_open","prev_is_reversal"))],
        pl.col("clip_cover").log1p().alias("prior_clip_cover"),
        pl.col("unrealized_usdt").sign().mul(pl.col("unrealized_usdt").abs().log1p()).alias("inventory_pnl"),
        *[(pl.col(k)*pl.col("held_side")).alias(k) for k in ("r1","r4","r24","alt_residual")])


def split(t,mode):
    n=t.height
    idx=np.arange(n)
    groups=t["life_id"].to_numpy()
    dates=t["ms"].to_numpy()//DAY
    if mode=="date":
        unique=np.unique(dates)
        if len(unique)<4:
            return
        pivot=unique[max(2,int(len(unique)*.67))]
        train,test=idx[dates<pivot],idx[dates>=pivot]
        train=train[~np.isin(groups[train],groups[test])]
        yield train,test
        return
    groups=t["symbol"].to_numpy() if mode=="symbol" else groups
    tag={s:int.from_bytes(sha256(str(s).encode()).digest()[:4],"big")%3 for s in set(groups)}
    z=np.array([tag[s] for s in groups])
    for k in range(3):
        yield idx[z!=k],idx[z==k]


def scores(p,y,classes):
    actual=p[np.arange(len(y)),y]
    result={"logloss":float(-np.log(np.maximum(actual,1e-8)).mean()),
            "accuracy":float(np.mean(p.argmax(axis=1)==y))}
    for a,label in enumerate(classes):
        hits=y==a
        result[label+"_conditional_loss"]=float(-np.log(np.maximum(p[hits,a],1e-8)).mean()) if hits.any() else None
    return result



def balanced_scores(p,y,classes,t):
    accuracy=scores(p,y,classes)
    losses=-np.log(np.maximum(p[np.arange(len(y)),y],1e-8))
    for label in ("symbol","life_id"):
        _,idx=np.unique(t[label].to_numpy(),return_inverse=True)
        accuracy["equal_"+label+"_logloss"]=float(np.mean(np.bincount(idx,weights=losses)/np.bincount(idx)))
    return accuracy

def multinomial(x,y,n):
    k=x.shape[1]
    one=np.eye(n)[y]
    penalty=np.r_[0.,np.full(k-1,10.)][:,None]
    def loss(w):
        b=w.reshape(k,n-1)
        eta=np.column_stack([x@b,np.zeros(len(y))])
        z=logsumexp(eta,axis=1)
        p=np.exp(eta-z[:,None])
        grad=x.T@(p[:,:-1]-one[:,:-1])+penalty*b
        return float(np.sum(z-eta[np.arange(len(y)),y])+.5*np.sum(penalty*b*b)),grad.ravel()
    w=minimize(loss,np.zeros(k*(n-1)),jac=True,method="L-BFGS-B",
               options={"maxiter":95,"ftol":2e-7})
    if not np.isfinite(w.fun):
        raise ValueError("Nonfinite action fit")
    return w.x.reshape(k,n-1)


def fitted(t,classes):
    result={}
    y=np.array([classes.index(v) for v in t["action"].to_list()],dtype=int)
    for mode in ("date","symbol","lifecycle"):
        scores_by_model=defaultdict(list)
        yt=[]
        ids=[]
        for tr,ts in split(t,mode):
            if len(ts)<10 or len(tr)<70 or min(np.bincount(y[tr],minlength=len(classes)))<6:
                continue
            freq=(np.bincount(y[tr],minlength=len(classes))+.5)/(len(tr)+.5*len(classes))
            scores_by_model["frequency"].append(np.tile(freq,(len(ts),1)))
            yt.extend(y[ts])
            ids.extend(ts)
            if len(tr)>12000:
                rng=np.random.default_rng(1294)
                tr=rng.choice(tr,12000,replace=False)
            for name,cols in (("history_state",STATE),("price_only",PRICE),("state_and_price",STATE+PRICE)):
                model=Design(t,tr,cols,set())
                weights=multinomial(model.matrix(t,tr),y[tr],len(classes))
                eta=np.column_stack([model.matrix(t,ts)@weights,np.zeros(len(ts))])
                scores_by_model[name].append(np.exp(eta-logsumexp(eta,axis=1)[:,None]))
        if ids:
            selected=t[ids]
            result[mode]={"n":len(ids),"symbols":selected["symbol"].n_unique(),
                "lifecycles":selected["life_id"].n_unique(),"days":selected.select((pl.col("ms")//DAY).n_unique()).item(),
                "counts":dict(zip(classes,np.bincount(yt,minlength=len(classes)).tolist())),
                "models":{name:balanced_scores(np.concatenate(entries),np.array(yt),classes,selected)
                          for name,entries in scores_by_model.items()}}
    return result


def distributions(t,classes):
    labels=t["action"].to_numpy()
    y=np.column_stack([labels==v for v in classes])
    day=t["ms"].to_numpy()//DAY
    symbol=t["symbol"].to_numpy()
    life=t["life_id"].to_numpy()
    uday=np.unique(day)
    late=day>=uday[max(1,int(len(uday)*.67))] if len(uday)>1 else np.ones(len(day),dtype=bool)
    base=[]
    for name,key,c,th in SIGNALS:
        x=values(t,key)
        valid=np.isfinite(x)
        selected=valid&((x<=th) if c=="<=" else (x>=th))
        if selected.sum()<12:
            continue
        group={"signal":name,"feature":key,"op":c,"threshold":th}
        for tag,mask in (("all",np.ones(len(t),dtype=bool)),("late",late)):
            a,b=mask&valid,mask&selected
            n=int(b.sum())
            if (not a.any()) or n<8:
                continue
            nums=y[b].sum(axis=0)
            baseline=y[a].mean(axis=0)
            q=nums/n
            value={"n":n,"eligible":int(a.sum()),"risk_n":t.height,"symbols":len(set(symbol[b])),
                   "lifecycles":len(set(life[b])),"counts":dict(zip(classes,nums.tolist())),
                   "p":dict(zip(classes,q.tolist())),"baseline":dict(zip(classes,baseline.tolist())),
                   "lift":{k:float(v/m) if m else None for k,v,m in zip(classes,q,baseline)},
                   "signal_given_action":{k:float(v/m) if m else None for k,v,m in zip(classes,nums,y[a].sum(axis=0))}}
            # Independent cluster summaries expose chronological, cross-symbol and within-path dispersion.
            for name_,groupids in (("day",day),("symbol",symbol),("life",life)):
                _,inv=np.unique(groupids[a],return_inverse=True)
                tested=np.bincount(inv,weights=b[a])
                eligible=np.bincount(inv)
                good=tested>=3
                if not len(tested):
                    continue
                rates=np.column_stack([np.bincount(inv,weights=b[a]&v[a]) for v in y.T])
                allrates=np.column_stack([np.bincount(inv,weights=v[a]) for v in y.T])
                value[name_+"_above_base"]={k:int((good&(rates[:,j]*eligible>allrates[:,j]*tested)).sum())
                    for j,k in enumerate(classes)}
                value[name_+"_groups"]=int(good.sum())
                if len(tested)>=6 and good.sum()>=4:
                    rng=np.random.default_rng(512)
                    res=rng.integers(len(tested),size=(180,len(tested)))
                    den=tested[res].sum(axis=1)
                    estimate=rates[res].sum(axis=1)[den>0]/den[den>0,None]
                    value[name_+"_ci90"]={k:np.quantile(estimate[:,j],[.05,.95]).tolist() for j,k in enumerate(classes)}
            group[tag]=value
        if "all" in group:
            base.append(group)
    return base


def surfaces(t):
    result=[]
    labels=t["action"].to_numpy()
    for x,y in AXES:
        a,b=values(t,x),values(t,y)
        ok=np.isfinite(a+b)
        if ok.sum()<60:
            continue
        limits=[np.quantile(v[ok],[1/3,2/3]) for v in (a,b)]
        if limits[0][0]==limits[0][1] or limits[1][0]==limits[1][1]:
            continue
        aa,bb=np.digitize(a,limits[0]),np.digitize(b,limits[1])
        cells=[]
        for i in range(3):
            for j in range(3):
                mask=ok&(aa==i)&(bb==j)
                n=int(mask.sum())
                cells.append({"x":i,"y":j,"n":n,"counts":{c:int((labels[mask]==c).sum()) for c in ORDER}})
        result.append({"x":x,"y":y,"cuts":[v.tolist() for v in limits],"n":int(ok.sum()),"cells":cells})
    return result


def qtile(t,k):
    a=values(t,k)
    a=a[np.isfinite(a)]
    return {"n":len(a),"q":np.quantile(a,[.1,.25,.5,.75,.9]).tolist() if len(a) else []}


def sequence(t):
    result={}
    for kind in ("open","reverse_open","add","reduce"):
        sub=t.filter(pl.col("prev_kind")==kind)
        measures={}
        for action in ORDER:
            x=sub.filter(pl.col("action")==action)
            measures[action]={"n":x.height,"gap":qtile(x,"gap_s"),"move":qtile(x,"from_previous"),"size":qtile(x,"event_cash")}
        result[kind]={"n":sub.height,"actions":measures}
    return result


def geometry(t):
    r=t.filter(pl.col("action")=="reverse")
    opposing=t.filter(pl.col("action").is_in(["reduce","reverse"]))
    ready=opposing.filter(pl.col("clip_cover").is_finite())
    dist={}
    for label,a,b in (("lt_half",0,.5),("half_to_one",.5,1),("at_least_one",1,1e20)):
        x=ready.filter(pl.col("clip_cover").is_between(a,b,closed="left"))
        dist[label]={"n":x.height,"reversals":x.filter(pl.col("action")=="reverse").height}
    repeat=r.filter((pl.col("trim_streak")>=2)&(pl.col("gap_s")<=30)&
                    pl.col("clip_cover").is_between(.25,4.))
    rw=r.filter(pl.col("clip_cover").is_finite())
    # Observed full reversal order / previous same-side reduction quantity.
    observed = rw.with_columns((pl.col("clip_cover").pow(-1)*
                   (1+pl.col("incoming_qty")/pl.col("qty").abs())).alias("order_over_previous_trim"))
    return {"n":r.height,"trim_before":r.filter(pl.col("prev_kind")=="reduce").height,
            "three_trims":r.filter(pl.col("trim_streak")>=3).height,
            "low_remaining":r.filter(pl.col("peak_fraction")<=.25).height,
            "high_remaining":r.filter(pl.col("peak_fraction")>=.75).height,
            "no_trims_high":r.filter((pl.col("count_reduce")==0)&(pl.col("peak_fraction")>=.75)).height,
            "same_side_fast_after_two_trims":repeat.height,
            "incoming_to_outgoing":qtile(r.with_columns((pl.col("incoming_qty")/pl.col("qty").abs()).alias("ratio")),"ratio"),
            "remaining_peak":qtile(r,"peak_fraction"),"prior_clip_coverage":dist,
            "order_over_previous_trim":qtile(observed,"order_over_previous_trim"),
            "matches_previous_trim_within_10pct":observed.filter(pl.col("order_over_previous_trim").is_between(.9,1.1)).height,
            "prior_clip_coverage_denominator":ready.height}


def sizing(t):
    t=t.with_columns((pl.col("event_cash")/pl.col("fill_px")/pl.col("qty").abs()).alias("book_fraction"),
        (pl.col("event_cash")/pl.col("prev_cash")).alias("clip_ratio"),
        ((pl.col("fill_px")/pl.col("prev_px")).log().abs()*10000).alias("book_price_spacing_bps"))
    stats={}
    for action in ("add","reduce"):
        x=t.filter(pl.col("action")==action)
        groups={}
        for n,p in (("all",x),("prior_under_20",x.filter(pl.col("prev_raw_cash")<20)),
                    ("prior_over_100",x.filter(pl.col("prev_raw_cash")>=100)),
                    ("short_gap",x.filter(pl.col("gap_s")<=5)),
                    ("long_gap",x.filter(pl.col("gap_s")>=300))):
            groups[n]={"n":p.height,"clip":qtile(p,"event_cash"),"fraction":qtile(p,"book_fraction"),
                       "over_100":p.filter(pl.col("event_cash")>=100).height,
                       "over_500":p.filter(pl.col("event_cash")>=500).height,
                       "near_10":p.filter(pl.col("event_cash").is_between(8,12)).height}
        persist=x.filter(pl.col("prev_kind")==action)
        for tag,subset in (("same_action_5s",persist.filter(pl.col("gap_s")<=5)),
                           ("same_action_300s",persist.filter(pl.col("gap_s")>=300))):
            groups[tag]={"n":subset.height,"clip":qtile(subset,"event_cash"),
                         "price_spacing":qtile(subset,"book_price_spacing_bps"),
                         "size_matches_10pct":subset.filter(pl.col("clip_ratio").is_between(.9,1.1)).height}
        after=x.filter(pl.col("prev_kind")=="reverse_open")
        groups["after_reverse"]={"n":after.height,"within_60s":after.filter(pl.col("gap_s")<=60).height}
        stats[action]=groups
    return stats


def examples(t):
    specs=(("scale into strength",(pl.col("action")=="add")&(pl.col("from_previous")>.003)),
           ("scale despite adverse movement",(pl.col("action")=="add")&(pl.col("from_previous")<-.003)),
           ("trim profitable larger inventory",(pl.col("action")=="reduce")&(pl.col("from_cost")>.005)&(pl.col("exposure_usdt")>100)),
           ("trim underwater inventory",(pl.col("action")=="reduce")&(pl.col("from_cost")<-.005)),
           ("gradual reversal",(pl.col("action")=="reverse")&(pl.col("trim_streak")>=3)&(pl.col("peak_fraction")<=.25)),
           ("abrupt reversal",(pl.col("action")=="reverse")&(pl.col("count_reduce")==0)&(pl.col("peak_fraction")>=.75)))
    result={}
    for label,mask in specs:
        x=t.filter(mask&pl.col("price_source").is_not_null()).with_columns(
            (pl.col("event_cash")-pl.col("event_cash").median()).abs().alias("away")).sort("away")
        if not x.height:
            continue
        row=x.row(0,named=True)
        row={k:row[k] for k in ("symbol","life_id","order_id","book_ms","event_cash","peak_fraction","qty","prev_kind",
             "from_previous","from_cost","gap_s","trim_streak","exposure_usdt","incoming_qty")}
        row["url"]="http://127.0.0.1:8765/#explorer?"+urlencode({"source":"binance","symbol":row["symbol"],
                     "lifecycle":row["life_id"],"execution":row["order_id"]})
        result[label]=row
    return result



def joint_bands(out):
    results=[]
    for label in ("executions","minutes"):
        source=pl.read_parquet(out/(label+".parquet"))
        classes=ORDER if label=="executions" else (*ORDER,"hold")
        t=source.filter(pl.col("action").is_in(classes)&pl.col("from_previous").is_finite()&pl.col("exposure_usdt").is_finite())
        days=np.unique(t["ms"].to_numpy()//DAY)
        cutoff=int(days[max(1,int(len(days)*.67))])*DAY
        for side,sign in (("long",1),("short",-1)):
            lane=t.filter(pl.col("held_side")==sign)
            for movement,criterion in (("adverse",pl.col("from_previous")<=-.0025),
                                       ("favorable",pl.col("from_previous")>=.0025)):
                pool=lane.filter(criterion)
                small=pl.col("exposure_usdt")<=25
                large=pl.col("exposure_usdt")>=250
                for period,scope in (("all",pool),("late",pool.filter(pl.col("ms")>=cutoff))):
                    for size,f in (("small",small),("large",large)):
                        x=scope.filter(f)
                        ctr=Counter(x["action"].to_list())
                        record=dict(scope=label,side=side,move=movement,period=period,size=size,n=x.height,
                                    symbols=x["symbol"].n_unique(),lifecycles=x["life_id"].n_unique(),
                                    **{a:ctr[a] for a in (*ORDER,"hold")})
                        if label=="executions" and size=="small":
                            # Compare within symbol and elapsed-time bins with both sizes observed.
                            ln=scope.filter(small|large).with_columns(
                                (pl.col("exposure_usdt")<=25).alias("small"),
                                pl.when(pl.col("gap_s")<=5).then(0).when(pl.col("gap_s")<=60).then(1)
                                  .when(pl.col("gap_s")<=300).then(2).otherwise(3).alias("gap_band"))
                            g=ln.group_by("symbol","gap_band","small").agg(pl.len().alias("n"),
                                                                   (pl.col("action")=="reverse").mean().alias("p"))
                            a=g.filter(pl.col("small")&(pl.col("n")>=3))
                            b=g.filter(~pl.col("small")&(pl.col("n")>=3))
                            compared=a.join(b,on=["symbol","gap_band"],suffix="_large")
                            record["same_symbol_gap_cells"]=compared.height
                            record["cells_with_more_small_reversals"]=compared.filter(pl.col("p")>pl.col("p_large")).height
                        results.append(record)
    pl.from_dicts(results,infer_schema_length=None).write_csv(out/"fixed-interactions.csv")


def size_bands(out):
    t=pl.read_parquet(out/"executions.parquet").filter(pl.col("action").is_in(["add","reduce"]))
    t=t.with_columns((pl.col("event_cash")/pl.col("fill_px")/pl.col("qty").abs()).alias("fraction"),
                     (pl.col("after_qty").abs()*pl.col("fill_px")).alias("after_inventory"))
    t=t.with_columns(pl.col("vol_5m").median().over("symbol","held_side").alias("symbol_vol"),
                     pl.col("vol_5m").count().over("symbol","held_side").alias("vol_count"))
    volume_ready=(pl.col("vol_count")>=20)&pl.col("vol_5m").is_finite()
    bands={
        "all":pl.lit(True),
        "inventory <= $25":pl.col("exposure_usdt")<=25,
        "inventory >= $250":pl.col("exposure_usdt")>=250,
        "price adverse >= 25 bp":pl.col("from_previous")<=-.0025,
        "price favorable >= 25 bp":pl.col("from_previous")>=.0025,
        "volatility below same-symbol median":volume_ready&(pl.col("vol_5m")<pl.col("symbol_vol")),
        "volatility at or above same-symbol median":volume_ready&(pl.col("vol_5m")>=pl.col("symbol_vol")),
    }
    rows=[]
    for (side,action),group in t.group_by("held_side","action"):
        for label,band in bands.items():
            x=group.filter(band)
            rows.append(dict(side="long" if side==1 else "short",action=action,condition=label,
                n=x.height,eligible=group.height,symbols=x["symbol"].n_unique(),lifecycles=x["life_id"].n_unique(),
                clip_median=x["event_cash"].median(),clip_10=x["event_cash"].quantile(.1,interpolation="linear"),
                clip_90=x["event_cash"].quantile(.9,interpolation="linear"),
                clips_at_least_100=x.filter(pl.col("event_cash")>=100).height,
                fraction_median=x["fraction"].median(),post_fill_exposure_median=x["after_inventory"].median()))
    pl.from_dicts(rows).sort("side","action","condition").write_csv(out/"sizing-conditions.csv")


def run(out):
    original=features(pl.read_parquet(out/"executions.parquet"))
    minutes=features(pl.read_parquet(out/"minutes.parquet"))
    report={"raw_events":original.group_by("action").len().to_dicts(),"raw_minutes":minutes.group_by("action").len().to_dicts()}
    for name,sign in (("long",1),("short",-1)):
        all_e=original.filter((pl.col("held_side")==sign)&pl.col("action").is_in(ORDER))
        ready=all_e.filter(pl.col("price_source").is_not_null())
        clocks=minutes.filter((pl.col("held_side")==sign)&pl.col("action").is_in((*ORDER,"hold")))
        fast_minutes=clocks.filter(pl.col("price_source").is_not_null())
        choices=dict(n=all_e.height,symbols=all_e["symbol"].n_unique(),lives=all_e["life_id"].n_unique(),
                     baseline=Counter(all_e["action"].to_list()),fine=ready.height,
                     fine_baseline=Counter(ready["action"].to_list()),fine_sources=ready.group_by("price_source").len().to_dicts(),
                     fine_symbols=ready["symbol"].n_unique(),fine_days=ready.select((pl.col("ms")//DAY).n_unique()).item(),
                     stats={a:{k:qtile(ready.filter(pl.col("action")==a),k) for k in ("from_previous","from_add","from_reduce","from_initial",
                             "from_cost","from_best_market","exposure_usdt","unrealized_usdt","gap_s","peak_fraction","ret_5s","ret_5m")}
                             for a in ORDER},signals=distributions(all_e,ORDER),surfaces=surfaces(ready),
                     validation={"all":fitted(all_e,ORDER),"fine":fitted(ready,ORDER)},
                     timing={"all_n":clocks.height,"fine_n":fast_minutes.height,"counts":Counter(clocks["action"].to_list()),
                             "fine_counts":Counter(fast_minutes["action"].to_list()),
                             "signals":distributions(clocks,(*ORDER,"hold"))},
                     transitions=sequence(all_e),geometry=geometry(all_e),sizing=sizing(all_e),examples=examples(all_e))
        report[name]=choices
        print(name,all_e.height,ready.height,flush=True)
    def clean(x):
        if isinstance(x,dict): return {k:clean(v) for k,v in x.items()}
        if isinstance(x,(list,tuple)): return [clean(v) for v in x]
        if isinstance(x,(float,np.floating)) and not np.isfinite(x): return None
        return x
    joint_bands(out)
    size_bands(out)
    (out/"findings.json").write_text(json.dumps(clean(report),indent=2,allow_nan=False),encoding="utf-8")


if __name__=="__main__":
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("out",type=Path)
    p.add_argument("--joint-only",action="store_true")
    args=p.parse_args()
    if args.joint_only:
        joint_bands(args.out)
        size_bands(args.out)
    else:
        run(args.out)
