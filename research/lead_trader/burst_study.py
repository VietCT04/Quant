"""Study signed execution bursts and causal transition risk sets in the frozen lead tape."""

import argparse
from bisect import bisect_left
from collections import Counter, defaultdict
import json
from pathlib import Path
from urllib.parse import urlencode

import numpy as np
import polars as pl
import quant as q
from scipy.special import expit
from scipy.stats import rankdata

from .action_analysis import split
from .action_evidence import DAY, MarketTape, prebook, priced
from .decision_evidence import grouped_frames
from .decision_fit import Design, fitting_indices, model, values

INTERVALS = (5, 15, 30, 60, 120)
PRIMARY = 30
MARKET = ("ret_5s", "ret_15s", "ret_1m", "ret_5m", "ret_15m", "from_previous",
          "from_cost", "turn_5s_1m", "acceleration_1m", "flow_1m", "activity_1m",
          "trade_intensity1m", "vol_5m", "range_drawdown5m", "range_rebound5m",
          "btc_relative_5m")
STATE = ("inventory_log", "gap_log", "size_log", "age_log", "peak_fraction", "prev_aligned")
THRESHOLDS = (
    ("adverse_15s_10bp", "ret_15s", -.001, False),
    ("favorable_15s_10bp", "ret_15s", .001, True),
    ("adverse_1m_25bp", "ret_1m", -.0025, False),
    ("favorable_1m_25bp", "ret_1m", .0025, True),
    ("adverse_5m_50bp", "ret_5m", -.005, False),
    ("favorable_5m_50bp", "ret_5m", .005, True),
    ("adverse_since_fill_25bp", "from_previous", -.0025, False),
    ("favorable_since_fill_25bp", "from_previous", .0025, True),
    ("underwater_100bp", "from_cost", -.01, False),
    ("in_profit_100bp", "from_cost", .01, True),
    ("adverse_taker_flow_20pct", "flow_1m", -.20, False),
    ("supportive_taker_flow_20pct", "flow_1m", .20, True),
    ("turnover_1.5x", "activity_1m", 1.5, True),
)


def same_run(side, previous_side, gap_ms, seconds):
    return side == previous_side and 0 <= gap_ms <= seconds * 1000


def inventory_action(before, signed_quantity):
    if np.sign(before) == np.sign(signed_quantity):
        return "add"
    if np.isclose(abs(before), abs(signed_quantity), rtol=1e-8, atol=0):
        return "close"
    return "reduce" if abs(signed_quantity) < abs(before) else "reverse"


def order_sign(order):
    return {"BUY": 1, "SELL": -1}[order["side"]]


def export_tapes(source, focus, market, out):
    _, orders, legs, lives = grouped_frames(source)
    events = {r["order_id"]: r for r in pl.read_parquet(focus / "executions.parquet").to_dicts()}
    minutes = defaultdict(list)
    for row in pl.read_parquet(focus / "minutes.parquet").iter_rows(named=True):
        minutes[row["life_id"]].append(row)
    ds = q.data.open(market, universe=None)
    fills, clocks = [], []
    for symbol, paths in sorted(lives.items()):
        raw = {r["id"]: r for r in orders[symbol]}
        landmarks = []
        for path in paths:
            life = path["life_id"]
            seq = legs[life]
            ts = [leg["ms"] for leg in seq]
            if len(set(ts)) != len(ts):
                continue
            signs = [order_sign(raw[leg["id"]]) for leg in seq]
            for i, leg in enumerate(seq[1:], 1):
                if leg["id"] not in events:
                    continue
                cur = events[leg["id"]].copy()
                gap = ts[i] - ts[i-1]
                cur.update(order_side=signs[i], prev_order_side=signs[i-1],
                           booked_gap_ms=gap, ordinal=i,
                           signed_quantity=signs[i] * abs(raw[leg["id"]]["executedQty"]))
                fills.append(cur)
                stamp = ts[i-1] + 5000
                if stamp >= cur["ms"]:
                    continue
                ahead = ts[i] < ts[i-1] + 30_000
                state = prebook(seq, raw, i, stamp)
                landmarks.append(dict(symbol=symbol, life_id=life, ms=stamp, ordinal=i,
                    prev_order_side=signs[i-1], risk="landmark",
                    outcome=("quiet" if not ahead else "same" if signs[i]==signs[i-1] else "opposite"),
                    **state))
            for row in minutes[life]:
                stamp = row["ms"]
                i = bisect_left(ts, stamp)
                leg = seq[i]
                next_event = events.get(leg["id"])
                if not next_event or next_event["ms"] < stamp:
                    continue
                ahead = ts[i] < stamp + 60_000
                if ahead:
                    kind = next_event["action"]
                    start = not same_run(signs[i], signs[i-1], ts[i]-ts[i-1], PRIMARY)
                    outcome = kind if start else "continuation"
                else:
                    outcome = "quiet"
                clocks.append(row | {"outcome": outcome, "prev_order_side": signs[i-1], "risk": "minute"})
        if landmarks:
            stamps = np.unique([r["ms"] for r in landmarks])
            tape = MarketTape(symbol, ds, focus / "five-second", int(stamps[0]), int(stamps[-1]))
            quotes = tape.observations(stamps)
            for r in landmarks:
                r.update(priced(r, quotes.get(r["ms"]), None, None))
            clocks.extend(landmarks)
    for name, rows in (("fills", fills), ("opportunities", clocks)):
        pl.from_dicts(rows, infer_schema_length=None).write_parquet(out / (name+".parquet"))


def enrich(t):
    return t.with_columns(
        pl.col("exposure_usdt").log1p().alias("inventory_log"),
        pl.col("gap_s").log1p().alias("gap_log"), pl.col("prev_raw_cash").log1p().alias("size_log"),
        pl.col("age_s").log1p().alias("age_log"),
        (pl.col("prev_order_side")==pl.col("held_side")).cast(pl.Int8).alias("prev_aligned"),
    )


def matched_pairs(t):
    """Match once without replacement on symbol, day, held and previous order sides."""
    groups = defaultdict(lambda: ([], []))
    for i, r in enumerate(t.select("symbol","ms","held_side","prev_order_side","inventory_log","gap_log","y").iter_rows()):
        sym, ms, held, previous, inventory, gap, outcome = r
        if inventory is not None and gap is not None:
            groups[sym,ms//DAY,held,previous][int(outcome)].append((i,inventory,gap))
    result = []
    for controls, cases in groups.values():
        if not controls or not cases:
            continue
        pool = np.asarray(controls)
        order = []
        for i, inv, gap in cases:
            d = np.abs(pool[:,1:]-[inv,gap])
            valid = np.flatnonzero((d[:,0]<=np.log(2)) & (d[:,1]<=np.log(3)))
            candidates = valid[np.argsort(d[valid].sum(axis=1),kind="stable")]
            if len(candidates):
                order.append((len(candidates),i,candidates))
        taken = set()
        for _, i, candidates in sorted(order,key=lambda x: x[:2]):
            j = next((c for c in candidates if c not in taken),None)
            if j is not None:
                taken.add(j)
                result.append((i,int(pool[j,0])))
    return np.asarray(result,dtype=int).reshape(-1,2)


def test_panel(name, panel):
    t = panel.filter(pl.col("price_source").is_not_null()).sort("ms","symbol","life_id")
    y = t["y"].to_numpy().astype(int)
    if t.is_empty():
        return [], [], []
    dates = np.unique(t["ms"].to_numpy()//DAY)
    cut = dates[min(len(dates)-1,max(1,int(.67*len(dates))))]
    day = t["ms"].to_numpy()//DAY
    pairs = matched_pairs(t)
    rates, matched, validation = [], [], []
    for label, col, level, high in THRESHOLDS:
        x = values(t,col)
        known = np.isfinite(x)
        signal = known & ((x>=level) if high else (x<=level))
        for ident, scope in (("all",np.ones(len(y),dtype=bool)),("later",day>=cut)):
            a,b = scope&known,scope&signal
            if b.any():
                rates.append(dict(panel=name,signal=label,period=ident,n=int(b.sum()),
                    positive=int(y[b].sum()),eligible=int(a.sum()),baseline_positive=int(y[a].sum()),
                    symbols=t.filter(pl.Series(b))["symbol"].n_unique()))
    for col in MARKET:
        a,b = values(t,col)[pairs].T
        valid = np.isfinite(a)&np.isfinite(b)
        for ident, scope in (("all",np.ones(len(pairs),dtype=bool)),("later",day[pairs[:,0]]>=cut)):
            keep = scope&valid
            if not keep.any():
                continue
            d = a[keep]-b[keep]
            sym=t["symbol"].to_numpy()[pairs[keep,0]]
            medians=np.array([np.median(d[sym==s]) for s in np.unique(sym)])
            matched.append(dict(panel=name,feature=col,period=ident,pairs=len(d),
                symbols=len(medians),positive_symbols=int((medians>0).sum()),negative_symbols=int((medians<0).sum()),
                positive_pairs=int((d>0).sum()),negative_pairs=int((d<0).sum()),
                median_case=float(np.median(a[keep])),median_control=float(np.median(b[keep])),
                median_difference=float(np.median(d))))
    for mode in ("date","symbol"):
        results=defaultdict(list)
        fold_y=[]
        fold_symbol=[]
        for train,test in split(t,mode):
            if len(test)<12 or min(np.bincount(y[train],minlength=2))<10:
                continue
            idx,w=fitting_indices(y,train,True)
            fold_y.extend(y[test])
            fold_symbol.extend(t["symbol"].to_numpy()[test])
            # Gap and prior direction define the first/continuation label and
            # would otherwise make these descriptive fits almost tautological.
            state = tuple(k for k in STATE if k not in ("gap_log","prev_aligned")) if "_first_vs_continuation_" in name else STATE
            for label, cols in (("state",state),("market",MARKET),("combined",state+MARKET)):
                d=Design(t,idx,cols,set())
                coef=model(d.matrix(t,idx),y[idx],True,w)
                results[label].extend(expit(d.matrix(t,test)@coef))
            results["frequency"].extend(np.repeat((sum(y[train])+.5)/(len(train)+1),len(test)))
        a=np.asarray(fold_y)
        sy=np.asarray(fold_symbol)
        for label,p in results.items():
            p=np.clip(np.asarray(p),1e-7,1-1e-7)
            loss=-np.where(a,np.log(p),np.log1p(-p))
            auc=(rankdata(p)[a==1].sum()-a.sum()*(a.sum()+1)/2)/(a.sum()*(1-a).sum()) if 0<a.sum()<len(a) else None
            validation.append(dict(panel=name,mode=mode,model=label,n=len(a),positive=int(a.sum()),
                symbols=len(set(sy)),logloss=float(loss.mean()),auc=auc,
                equal_symbol_logloss=float(np.mean([loss[sy==s].mean() for s in set(sy)]))))
    return rates, matched, validation


def annotate(fills, seconds):
    return fills.with_columns(
        ((pl.col("order_side")==pl.col("prev_order_side"))&
         (pl.col("booked_gap_ms").is_between(0,seconds*1000))).alias("continuation"),
        (pl.col("order_side")!=pl.col("prev_order_side")).alias("switch"),
        ((pl.col("fill_px")/pl.col("prev_px")).log().abs()*10000).alias("fill_distance_bp"),
    )


def sensitivity(fills):
    output = []
    for seconds in INTERVALS:
        t = annotate(fills,seconds)
        for (side,action), grp in t.group_by("held_side","action"):
            for flag in (False,True):
                s = grp.filter(pl.col("continuation")==flag)
                output.append(dict(seconds=seconds,side=side,action=action,continuation=flag,
                    n=s.height, switches=s.filter(pl.col("switch")).height,
                    symbols=s["symbol"].n_unique(),lifecycles=s["life_id"].n_unique(),
                    five_second_marks=s["mark_px"].count(),
                    median_fill_distance_bp=s["fill_distance_bp"].median(),
                    same_price=s.filter(pl.col("fill_distance_bp")<.01).height,
                    small_prior=s.filter(pl.col("prev_raw_cash")<20).height,
                    jumps=s.filter((pl.col("prev_raw_cash")<20)&(pl.col("whole_cash")>=100)).height))
    return pl.from_dicts(output).sort("seconds","side","action","continuation")


def signal_sensitivity(fills):
    rows=[]
    for seconds in INTERVALS:
        t=annotate(fills,seconds).filter(pl.col("action").is_in(["add","reduce"]))
        for (side,action,phase), subset in t.group_by("held_side","action","continuation"):
            for label,key,cut,high in THRESHOLDS:
                x=subset.filter(pl.col(key).is_finite())
                selected=x.filter((pl.col(key)>=cut) if high else (pl.col(key)<=cut))
                rows.append(dict(seconds=seconds,side=side,action=action,continuation=phase,
                    signal=label,observed=selected.height,eligible=x.height))
    return pl.from_dicts(rows).sort("seconds","side","action","continuation","signal")


def geometry(t):
    exact = [inventory_action(qty,order) for qty,order in t.select("qty","signed_quantity").iter_rows()]
    checked = t.with_columns(pl.Series("mechanical_action",exact))
    disagreements = checked.filter(pl.col("action")!=pl.col("mechanical_action"))
    opposing = checked.filter(pl.col("order_side")!=pl.col("held_side")).with_columns(
        (pl.col("signed_quantity").abs()/pl.col("qty").abs()).alias("size_ratio"))
    mixes = []
    for (held,phase), group in opposing.group_by("held_side","continuation"):
        for label, band in (
            ("all",pl.lit(True)),
            ("inventory <= $25",pl.col("exposure_usdt")<=25),
            ("inventory >= $250",pl.col("exposure_usdt")>=250),
        ):
            g=group.filter(band)
            counts=Counter(g["action"].to_list())
            mixes.append(dict(held_side=held,continuation=phase,band=label,n=g.height,
                reduce=counts["reduce"],close=counts["close"],reverse=counts["reverse"],
                order_to_inventory_median=g["size_ratio"].median()))

    marks = opposing.filter(pl.col("mark_px").is_not_null()).with_columns(
        (pl.col("signed_quantity").abs()*pl.col("mark_px")).alias("proxy_cash"))
    simulation = []
    for mode in ("date","symbol"):
        for fold,(train,test) in enumerate(split(marks,mode)):
            ref=marks[train].filter(pl.col("action")!="close")
            tgt=marks[test].filter(pl.col("action")!="close")
            if ref.is_empty() or tgt.is_empty():
                continue
            for held in (1,-1):
                a=ref.filter(pl.col("held_side")==held)
                b=tgt.filter(pl.col("held_side")==held)
                if a.is_empty() or b.is_empty():
                    continue
                raw = np.sort(a["proxy_cash"].to_numpy())
                cash = b["exposure_usdt"].to_numpy()
                p=(len(raw)-np.searchsorted(raw,cash,side="right")+.5)/(len(raw)+1)
                y=b["action"].to_numpy()=="reverse"
                base=((a["action"]=="reverse").sum()+.5)/(a.height+1)
                opp=(a["prev_raw_cash"]<20).fill_null(False)
                buckets=[np.sort(a.filter(m)["proxy_cash"].to_numpy()) for m in (~opp,opp)]
                p_history = p.copy()
                small=(b["prev_raw_cash"]<20).fill_null(False).to_numpy()
                for ix,values_ in enumerate(buckets):
                    if len(values_)<15:
                        continue
                    mask=small==bool(ix)
                    p_history[mask]=(len(values_)-np.searchsorted(values_,cash[mask],side="right")+.5)/(len(values_)+1)
                for name, chance in (("frequency",np.full(b.height,base)),("independent_clip",p),("clip_by_previous_size",p_history)):
                    simulation.append(dict(mode=mode,fold=fold,side=held,model=name,n=b.height,
                        observed_reversals=int(y.sum()),expected_reversals=float(chance.sum()),
                        logloss=float(-np.where(y,np.log(chance),np.log1p(-chance)).mean())))
    return disagreements, mixes, simulation


def cohorts(fills, clocks):
    for held in (1,-1):
        suffix="long" if held>0 else "short"
        f = fills.filter(pl.col("held_side")==held)
        o = clocks.filter(pl.col("held_side")==held)
        m = o.filter(pl.col("risk")=="minute")
        l = o.filter(pl.col("risk")=="landmark")
        for action in ("add","reduce"):
            g=f.filter(pl.col("action")==action)
            yield f"{action}_first_vs_continuation_{suffix}",g.with_columns((~pl.col("continuation")).alias("y"))
            h=m.filter(pl.col("outcome").is_in([action,"quiet"]))
            yield f"{action}_first_vs_quiet_minute_{suffix}",h.with_columns((pl.col("outcome")==action).alias("y"))
        # Both directions face the same pause rule. Fast switches are instead
        # represented in the landmark comparison.
        g=f.filter((pl.col("prev_order_side")==held)&(pl.col("booked_gap_ms")>PRIMARY*1000))
        yield f"opposing_vs_same_side_restart_{suffix}",g.with_columns(pl.col("switch").alias("y"))
        g=f.filter((pl.col("order_side")!=held)&pl.col("action").is_in(["reduce","reverse"]))
        yield f"reverse_vs_reduce_{suffix}",g.with_columns((pl.col("action")=="reverse").alias("y"))
        g=f.filter((pl.col("prev_raw_cash")<20)&(pl.col("order_side")==pl.col("prev_order_side")))
        yield f"same_side_big_clip_after_small_{suffix}",g.with_columns((pl.col("whole_cash")>=100).alias("y"))
        for outcome in ("same","opposite"):
            g=l.filter(pl.col("outcome").is_in([outcome,"quiet"]))
            yield f"landmark_{outcome}_vs_quiet_{suffix}",g.with_columns((pl.col("outcome")==outcome).alias("y"))
        g=l.filter((pl.col("prev_kind")=="add")&pl.col("outcome").is_in(["opposite","quiet"]))
        yield f"after_add_opposing_vs_quiet_{suffix}",g.with_columns((pl.col("outcome")=="opposite").alias("y"))


def examples(fills):
    transition=fills.filter((~pl.col("continuation"))&(pl.col("prior")=="add")&
        (pl.col("action")=="reduce")&pl.col("from_previous").is_not_null())
    jump=fills.filter((pl.col("prev_raw_cash")<20)&(pl.col("whole_cash")>=100)&
        (~pl.col("switch"))&pl.col("mark_px").is_not_null())
    selections=[]
    for s in (1,-1):
        for label,frame,sort in (("switch_after_adverse_move",transition,"from_previous"),
                                 ("switch_after_favorable_move",transition,"from_previous"),
                                 ("same_direction_clip_jump",jump,"whole_cash")):
            g=frame.filter(pl.col("held_side")==s)
            if label=="switch_after_adverse_move":
                g=g.filter(pl.col(sort)<=-.0025)
            elif label=="switch_after_favorable_move":
                g=g.filter(pl.col(sort)>=.0025)
            if not g.height:
                continue
            r=g.sort(sort).row(g.height//2,named=True)
            url="http://127.0.0.1:8765/#explorer?"+urlencode(
                dict(source="binance",symbol=r["symbol"],lifecycle=r["life_id"],execution=r["order_id"]))
            selections.append(dict(label=label,url=url,**{k:r[k] for k in ("symbol","life_id","order_id",
                "held_side","book_ms","gap_s","action","exposure_usdt","prev_raw_cash","whole_cash","from_previous")}))
    return selections


def analyze(out):
    fills=enrich(pl.read_parquet(out/"fills.parquet"))
    clocks=enrich(pl.read_parquet(out/"opportunities.parquet"))
    sensitivity(fills).write_csv(out/"burst-sensitivity.csv")
    signal_sensitivity(fills).write_csv(out/"burst-market-sensitivity.csv")
    fills=annotate(fills,PRIMARY).with_columns(pl.col("prev_kind").alias("prior"))
    runs=fills.sort("life_id","ordinal").with_columns(
        (~pl.col("continuation")).cast(pl.Int32).cum_sum().over("life_id").alias("run"))
    runs.group_by("symbol","life_id","run",maintain_order=True).agg(
        pl.col("order_side").first(),pl.col("held_side").first(),pl.col("book_ms").first().alias("first_book"),
        pl.col("book_ms").last().alias("last_book"),pl.col("order_id").first().alias("first_order"),
        pl.col("order_id").last().alias("last_order"),pl.len().alias("adjustment_orders"),
        pl.col("whole_cash").sum().alias("whole_order_notional"),
        pl.col("action").first().alias("first_action"),pl.col("action").last().alias("last_action"),
    ).with_columns(
        pl.when(pl.col("order_side").shift(-1).over("life_id").is_null()).then(pl.lit("lifecycle_boundary"))
          .when(pl.col("order_side")!=pl.col("order_side").shift(-1).over("life_id"))
          .then(pl.lit("opposite_order")).otherwise(pl.lit("same_side_after_pause")).alias("next_boundary")
    ).write_csv(out/"execution-bursts-30s.csv")
    discrepancies, mixed, simulations=geometry(fills)
    discrepancies.write_parquet(out/"mechanical-disagreements.parquet")
    pl.from_dicts(mixed).sort("held_side","continuation","band").write_csv(out/"opposing-orders.csv")
    pl.from_dicts(simulations).write_csv(out/"clip-projection-validation.csv")
    rates, matches, validation, counts = [], [], [], []
    for name,panel in cohorts(fills,clocks):
        counts.append(dict(panel=name,n=panel.height,positive=panel["y"].sum(),
            marked=panel["price_source"].count(),marked_positive=panel.filter(pl.col("price_source").is_not_null())["y"].sum()))
        a,b,c=test_panel(name,panel)
        rates.extend(a)
        matches.extend(b)
        validation.extend(c)
    for name,items in (("risk-sets",counts),("market-conditions",rates),("market-matched",matches),
                       ("market-validation",validation)):
        pl.from_dicts(items).write_csv(out/(name+".csv"))
    selected = examples(fills)
    (out/"examples.json").write_text(json.dumps(selected,indent=2),encoding="utf-8")
    sample_ids=[r["life_id"] for r in selected]
    fills.filter(pl.col("life_id").is_in(sample_ids)).select(
        "symbol","life_id","ordinal","ms","book_ms","order_id","order_side","action","qty",
        "after_qty","incoming_qty","fill_px","whole_cash","continuation","booked_gap_ms",
        "from_previous","ret_15s","ret_1m","ret_5m","flow_1m","vol_5m",
    ).sort("life_id","ordinal").write_csv(out/"example-adjustment-sequences.csv")
    print(f"{fills.height} eligible fills, {clocks.height} opportunities, {discrepancies.height} mechanical disagreements")


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("out",type=Path)
    p.add_argument("--source",type=Path,default=Path("data/research/lead-trader/behavior-2026-11-28"))
    p.add_argument("--focus",type=Path,default=Path("data/research/lead-trader/action-focus-2026-11-29"))
    p.add_argument("--market",type=Path,default=Path("data/binance/um/bars_5s"))
    p.add_argument("--analyze-only",action="store_true")
    a=p.parse_args()
    a.out.mkdir(parents=True,exist_ok=True)
    if not a.analyze_only:
        export_tapes(a.source,a.focus,a.market,a.out)
    analyze(a.out)


if __name__=="__main__":
    main()
