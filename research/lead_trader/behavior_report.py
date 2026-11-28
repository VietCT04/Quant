"""Summarize the frozen behavioral study, robustness checks, and export small plots."""

import argparse
from collections import Counter, defaultdict
from datetime import UTC, datetime
from html import escape
import json
from pathlib import Path

import numpy as np
import polars as pl

from .behavior_study import DAY, distribution


def day_label(ms):
    return datetime.fromtimestamp(ms/1000,UTC).strftime("%Y-%m-%d")


def med(values):
    return distribution(values).get("median")


def paired_weeks(rows, key, timestamp="ms"):
    daily = defaultdict(list)
    for row in rows:
        x = row[key]
        if np.isfinite(x):
            daily[row[timestamp]//DAY].append(x)
    by_week = defaultdict(list)
    for d, values in daily.items():
        by_week[(d+3)//7].append(np.mean(values))
    weeks = [np.array(v) for v in by_week.values()]
    total = [np.mean(v) for v in daily.values()]
    result = dict(days=len(total),weeks=len(weeks),nonnegative_days=int(sum(x>=0 for x in total)))
    if total:
        result["daily_equal_mean"] = float(np.mean(total))
    if len(weeks)>=8:
        rng = np.random.default_rng(811)
        counts = np.array([len(w) for w in weeks])
        amounts = np.array([w.sum() for w in weeks])
        draws = rng.integers(len(weeks),size=(1500,len(weeks)))
        means = amounts[draws].sum(axis=1)/counts[draws].sum(axis=1)
        result["week_block_90"] = np.quantile(means,[.05,.95]).tolist()
    return result


def selection_stats(out):
    rows = pl.read_parquet(out/"asset-events.parquet").to_dicts()
    report = {}
    features = ("q24","q_rank","relq","reln","q_accel","n_accel","vol_rank","r4","r_rank","abs_rank",
                "flow","residual","btc_corr","listed_days")
    for event in ("activation","cessation"):
        matches = [r for r in rows if r["event"]==event]
        report[event] = {
            "events":len(matches), "symbols":len({r["symbol"] for r in matches}),
            "risk_set":med([r["risk_set"] for r in matches]), "liquidity_matched":med([r["liquidity_matched"] for r in matches]),
            "features":{k: {field:distribution([r[field] for r in matches])
                    for field in (k,k+"_broad",k+"_equal",k+"_equal_pct")} for k in features},
            "listed_30":sum(0<=r["listed_days"]<=30 for r in matches),
            "listed_90":sum(0<=r["listed_days"]<=90 for r in matches),
            "absolute_top_decile":sum(r["q_rank"]>=.9 for r in matches),
            "same_hour_top_momentum":sum(r["r_rank"]>=.9 for r in matches),
            "same_hour_bottom_momentum":sum(r["r_rank"]<=.1 for r in matches),
        }
        for k in ("relq","vol_rank","r_rank","abs_rank","reln"):
            q = [{"ms":r["ms"],"p":r[k+"_equal_pct"]-.5} for r in matches if r[k+"_equal_pct"] is not None]
            report[event][k+"_week_robustness"] = paired_weeks(q,"p")
    active = {(r["symbol"],r["start"]):r for r in rows if r["event"]=="activation"}
    paired = [(active[(r["symbol"],r["start"])],r) for r in rows if r["event"]=="cessation" and
              (r["symbol"],r["start"]) in active]
    report["same_spell_end_minus_start"] = {"spells":len(paired),
          **{k:distribution([b[k]-a[k] for a,b in paired]) for k in ("relq","vol_rank","q_rank","r_rank")}}
    return report


def opening_stats(out):
    rows = pl.read_parquet(out/"opening-events.parquet").to_dicts()
    reports = {}
    for sign in (1,-1):
        for boundary in (True,False):
            selected = [r for r in rows if r["direction"]==sign and r["opposite_boundary"] is boundary]
            if selected:
                report = dict(n=len(selected), symbols=len({r["symbol"] for r in selected}),activity=distribution([r["relq"] for r in selected]),
                              flow=distribution([sign*r["flow"] for r in selected]))
                for key in ("r1","r4","r24","residual","future1","future4","future24"):
                    report[key] = distribution([sign*r[key]*1e4 for r in selected if r[key] is not None])
                by_sym = Counter(r["symbol"] for r in selected)
                subset = [r for r in selected if r["symbol"] not in dict(by_sym.most_common(5))]
                report["ex_top5_pre4"] = distribution([sign*r["r4"]*1e4 for r in subset])
                report["ex_top5_fwd4"] = distribution([sign*r["future4"]*1e4 for r in subset if r["future4"] is not None])
                reports[f"{'long' if sign>0 else 'short'}_{'handoff' if boundary else 'other'}"] = report
    return reports


def size_stats(out):
    table = pl.read_parquet(out/"opening-events.parquet").to_dicts()
    pool = defaultdict(list)
    for row in table:
        if row["status"]=="All Closed" and all(np.isfinite(row[k]) for k in ("r4","relq","vol_rank")) and row["relq"]>0:
            g=(row["symbol"],row["direction"],row["opened"]//(7*DAY),row["opposite_boundary"],row["r4"]>=0)
            pool[g].append(row)
    pairs = []
    for group in pool.values():
        used = set()
        losers = [r for r in group if r["closingPnl"]<0]
        for win in sorted((r for r in group if r["closingPnl"]>0),key=lambda r:r["opened"]):
            candidates = []
            for loss in losers:
                dr = abs(win["r4"]-loss["r4"])
                dv = abs(np.log(win["relq"]/loss["relq"]))
                if loss["life_id"] not in used and dr <= .02 and dv <= np.log(2) and abs(win["vol_rank"]-loss["vol_rank"])<=.15:
                    candidates.append((dr/.02+dv/np.log(2), loss["life_id"], loss))
            if candidates:
                _,key,loss = min(candidates)
                used.add(key)
                pairs.append({"symbol":win["symbol"],"side":win["side"],"winner":win["life_id"],"loser":key,
                              "win_pnl":win["closingPnl"],"lose_pnl":loss["closingPnl"],"win_peak":win["peak_proxy"],
                              "lose_peak":loss["peak_proxy"],"win_hold":(win["closed"]-win["opened"])/3600000,
                              "lose_hold":(loss["closed"]-loss["opened"])/3600000})
    frame = pl.from_dicts(pairs)
    frame.write_csv(out/"matched-wins-losses.csv")
    exact = pl.read_parquet(out/"complete-trajectories.parquet")
    partial = pl.read_parquet(out/"partial-trajectories.parquet")
    reports = {}
    for side in ("long","short"):
        m = [r for r in pairs if r["side"].lower()==side]
        reports[side] = dict(pairs=len(m),symbols=len({r["symbol"] for r in m}),
             peak_ratio=distribution([r["win_peak"]/r["lose_peak"] for r in m if r["lose_peak"]>0]),
             hold_ratio=distribution([r["win_hold"]/r["lose_hold"] for r in m if r["lose_hold"]>0]),
             peak_difference=distribution([r["win_peak"]-r["lose_peak"] for r in m]))
    exact_groups = {}
    for label,lo,hi in (("under100",0,100),("100to1000",100,1000),("above1000",1000,1e20)):
        r = exact.filter(pl.col("peak_proxy").is_between(lo,hi,closed="left"))
        g = r.to_dicts()
        exact_groups[label] = dict(n=len(g),symbols=r["symbol"].n_unique(), initial=distribution(r["first_clip"]),
            additions=distribution(r["additions"]),max_add=distribution(r["max_add"]),
            share_with_add=sum(x["additions"]>0 for x in g),
            peak_over_initial=distribution([x["peak_proxy"]/x["first_clip"] for x in g if x["first_clip"]>0]),
            count_pnl_pos=sum(x["pnl"]>0 for x in g))
    source = pl.read_parquet(out/"positions.parquet").filter(pl.col("status")=="All Closed").sort("closingPnl")
    top = pl.concat([source.head(25).with_columns(pl.lit("losing").alias("tail")),
                     source.tail(25).with_columns(pl.lit("winning").alias("tail"))])
    complete_keys=set(exact["life_id"])
    partial_keys=set(partial["life_id"])
    top.with_columns(pl.when(pl.col("life_id").is_in(complete_keys)).then(pl.lit("complete"))
        .when(pl.col("life_id").is_in(partial_keys)).then(pl.lit("partial")).otherwise(pl.lit("no_orders")).alias("trajectory")).write_csv(out/"pnl-tails.csv")
    return dict(matched_outcomes=reports,exact_size_groups=exact_groups,
                tails_complete=sum(k in complete_keys for k in top["life_id"]),
                tails_partial=sum(k in partial_keys for k in top["life_id"]))


def hazard_stats(out):
    minutes = pl.read_parquet(out/"minutes.parquet").to_dicts()
    groups = {}
    for side in (1,-1):
        lane = [r for r in minutes if r["side"]==side]
        action_count=Counter(r["action"] for r in lane)
        details=dict(minutes=len(lane),symbols=len({r["symbol"] for r in lane}),lives=len({r["life_id"] for r in lane}),
                     dates=len({r["ms"]//DAY for r in lane}),counts=dict(action_count),
                     symbols_most_actions=Counter(r["symbol"] for r in lane if r["action"]!="hold").most_common(8))
        factors = (
            ("loss_vs_gain","signed_inventory_mark_bps",-25,25),
            ("adverse_vs_favorable_minute","signed_prior_minute",-25,25),
            ("outflow_vs_inflow","signed_flow",-.10,.10),
            ("ordinary_vs_busy","activity",.9,1.5),
            ("small_vs_large_inventory","marked_inventory_abs",75,250),
        )
        for label,feature,low,high in (*factors, *((name+"_after_fill_control",field,low,high)
                 for name,field,low,high in factors if name in {"ordinary_vs_busy","small_vs_large_inventory"})):
            cells = defaultdict(lambda: [[],[]])
            for row in lane:
                band = 0 if row[feature]<low else 1 if row[feature]>high else None
                if band is not None:
                    elapsed = np.searchsorted([15,60,240,720],row["elapsed_ms"]/60000)
                    recency = np.searchsorted([2,15],row["since_fill_ms"]/60000) if label.endswith("_after_fill_control") else 0
                    cells[(row["life_id"],int(elapsed),int(recency))][band].append(row)
            subsets = [(a,b) for a,b in cells.values() if len(a)>=3 and len(b)>=3]
            by_life=defaultdict(list)
            metrics = {}
            for action in ("add","reduce","reverse"):
                for a,b in subsets:
                    aa=sum(r["action"]==action for r in a)/len(a)
                    bb=sum(r["action"]==action for r in b)/len(b)
                    by_life[a[0]["life_id"]].append((a[0]["symbol"],aa,bb))
                differences = [(v[0][0],float(np.mean([x[1] for x in v])),float(np.mean([x[2] for x in v])))
                               for v in by_life.values()]
                by_life.clear()
                by_sym = defaultdict(list)
                for sym,a,b in differences:
                    by_sym[sym].append(b-a)
                values={s:float(np.mean(v)) for s,v in by_sym.items()}
                metrics[action] = dict(lifecycles=len(differences),symbols=len(by_sym),
                    low_pct=float(np.mean([a*100 for _,a,b in differences])) if differences else None,
                    high_pct=float(np.mean([b*100 for _,a,b in differences])) if differences else None,
                    symbol_equal_diff_pp=float(np.mean(list(values.values()))*100) if values else None,
                    positive_symbols=sum(v>0 for v in values.values()))
            details[label]=metrics
        groups["long" if side==1 else "short"]=details
    return groups


def market_stats(path):
    table=pl.read_parquet(path)
    table = table.with_columns((pl.col("own_bps")-pl.col("control_bps")).alias("spread"),
        (pl.col("delayed_own_bps")-pl.col("delayed_control_bps")).alias("delayed_spread"),
        (-pl.col("own_bps")-14.).alias("fade_after_cost"),
        (-pl.col("delayed_own_bps")-14.).alias("delayed_fade_after_cost"))
    rows=[]
    for hz in (1,4,24):
        for side in (1,-1):
            sample=table.filter((pl.col("horizon")==hz)&(pl.col("side")==side))
            for name,s in (("all",sample),("btc_up",sample.filter(pl.col("btc_regime")=="up")),
                            ("btc_down",sample.filter(pl.col("btc_regime")=="down")),
                            ("flow_aligned",sample.filter(pl.col("flow_aligned"))),
                            ("flow_opposed",sample.filter(~pl.col("flow_aligned")))):
                x=s.select("ms","spread").to_dicts()
                if not x:
                    continue
                rows.append(dict(horizon=hz,side=side,slice=name,n=s.height,symbols=s["symbol"].n_unique(),
                    median_own=med(s["own_bps"]),median_control=med(s["control_bps"]),
                    median_delayed=med(s["delayed_own_bps"]),median_spread=med(s["spread"]),median_delayed_spread=med(s["delayed_spread"]),
                    **paired_weeks(x,"spread"),
                    fade_after_cost=paired_weeks(s.select("ms","fade_after_cost").to_dicts(),"fade_after_cost"),
                    delayed_fade_after_cost=paired_weeks(s.select("ms","delayed_fade_after_cost").to_dicts(),"delayed_fade_after_cost")))
    monthly=(table.with_columns(pl.from_epoch("ms",time_unit="ms").dt.strftime("%Y-%m").alias("month"))
              .group_by("month","horizon","side").agg(pl.len().alias("pairs"),pl.median("own_bps").alias("median_own"),
                pl.median("spread").alias("median_spread"),pl.median("delayed_spread").alias("median_delayed"))
              .sort("month","horizon","side"))
    monthly.write_csv(path.with_name(path.stem+"-by-month.csv"))
    return rows,monthly





def downside_robustness(out):
    snapshot = pl.read_parquet(out/"positions.parquet",columns=["symbol"])
    traded = set(snapshot["symbol"])
    frozen_roster = out/"liquid50-hourly"
    liquid = {p.stem for p in frozen_roster.glob("*.parquet")}
    cutoff = int(datetime(2026,5,25,tzinfo=UTC).timestamp()*1000)
    september = int(datetime(2026,9,1,tzinfo=UTC).timestamp()*1000)
    results = []
    for name, stem in (("broad","market-markouts"),("liquid50","liquid50-markouts")):
        path = out/(stem+".parquet")
        if not path.exists():
            continue
        t = pl.read_parquet(path).filter((pl.col("horizon")==4)&(pl.col("side")==-1)&(pl.col("btc_regime")=="down"))
        t = t.with_columns((-pl.col("own_bps")-14).alias("fade"),(-pl.col("own_bps")-28).alias("double_cost"),
             (-pl.col("delayed_own_bps")-14).alias("late"),(pl.col("control_bps")-pl.col("own_bps")).alias("advantage"),
             (pl.col("ms")//DAY).alias("day"))
        top_days = t.group_by("day").agg(pl.mean("fade")).sort("fade",descending=True).head(5)["day"].to_list()
        slices = {"all":t, "exclude_best_five_days":t.filter(~pl.col("day").is_in(top_days))}
        if name=="broad":
            slices.update(
                pre_september=t.filter(pl.col("ms")<september), from_september=t.filter(pl.col("ms")>=september),
                neither_leg_traded=t.filter(~pl.col("symbol").is_in(traded)&~pl.col("control").is_in(traded)),
                outside_liquid50=t.filter(~pl.col("symbol").is_in(liquid)&~pl.col("control").is_in(liquid)))
        else:
            slices["before_broad_test"]=t.filter(pl.col("ms")<cutoff)
            slices["from_broad_test"]=t.filter(pl.col("ms")>=cutoff)
        for sample, frame in slices.items():
            row = {"universe":name,"sample":sample,"pairs":frame.height,"symbols":frame["symbol"].n_unique()}
            for value in ("fade","double_cost","late","advantage"):
                row[value]=paired_weeks(frame.select("ms",value).to_dicts(),value)
            results.append(row)
    return results

def leadership_stats(path):
    t = pl.read_parquet(path)
    report = {}
    for hz in (4,24):
        quiet,busy=f"quiet{hz}",f"busy{hz}"
        pairs=t.with_columns((pl.col(quiet)-pl.col(busy)).alias("difference"))
        report[str(hz)] = dict(n=t.height,symbols=t["symbol"].n_unique(),
            median_quiet=med(t[quiet]),median_busy=med(t[busy]),median_spread=med(pairs["difference"]),
            **paired_weeks(pairs.select("ms","difference").to_dicts(),"difference"))
    return report

def execution_shapes(out):
    legs = pl.read_parquet(out/"confirmed-legs.parquet")
    life = pl.read_parquet(out/"complete-trajectories.parquet")
    parents = pl.read_parquet(out/"positions.parquet").filter(pl.col("status")=="All Closed")
    monthly = (parents.with_columns(pl.from_epoch("closed",time_unit="ms").dt.strftime("%Y-%m").alias("month"))
        .group_by("month","side").agg(pl.len().alias("positions"),pl.sum("closingPnl").alias("reported_pnl"))
        .sort("month","side"))
    monthly.write_csv(out/"position-pnl-by-month.csv")
    exact = life.sort("pnl")
    first = exact.filter(pl.col("peak_proxy")>=1000)
    return {
        "clips_by_leg":{action:distribution(legs.filter(pl.col("action")==action)["clip"])
                         for action in ("open","add","reduce","close")},
        "large_initial_at_most_20":first.filter(pl.col("first_clip")<=20).height,
        "large_with_at_least_one_500_add":first.filter(pl.col("max_add")>=500).height,
        "large_by_side":first.group_by("side").agg(pl.len().alias("count")).to_dicts(),
        "timeline_winners":exact.tail(5).to_dicts(),"timeline_losers":exact.head(5).to_dicts(),
        "months":monthly.to_dicts(),
    }

def svg_bars(path,title,subtitle,rows,scale,percent=False):
    width,height=780,120+34*len(rows)
    zero=476
    extent=224/scale
    parts=[f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">',
           '<rect width="100%" height="100%" fill="#f7fafc"/>',
           '<g font-family="Segoe UI, Arial, sans-serif" fill="#142734">',
           f'<text x="25" y="32" font-size="20" font-weight="600">{escape(title)}</text>',
           f'<text x="25" y="56" font-size="12" fill="#446375">{escape(subtitle)}</text>',
           f'<path d="M{zero} 70 V{height-26}" stroke="#738896"/>']
    for j,(label,value) in enumerate(rows):
        top=80+j*34
        stop=zero+value*extent
        parts += [f'<text x="25" y="{top+14}" font-size="12">{escape(label)}</text>',
                  f'<rect x="{min(zero,stop):.2f}" y="{top}" width="{abs(stop-zero):.2f}" height="19" rx="2" fill="{("#008574" if value>=0 else "#ae4a3e")}"/>',
                  f'<text x="{max(zero,stop)+8:.2f}" y="{top+14}" font-size="12">{value:+.1f}{"%" if percent else ""}</text>']
    parts.append('</g></svg>')
    path.write_text("\n".join(parts),encoding="utf-8")


def main():
    cli=argparse.ArgumentParser(description=__doc__)
    cli.add_argument("out",type=Path)
    out=cli.parse_args().out
    report=dict(selection=selection_stats(out),openings=opening_stats(out),
                sizing=size_stats(out),inventory=hazard_stats(out),execution_shapes=execution_shapes(out))
    for title,stem in (("broad","market-markouts"),("liquid50","liquid50-markouts")):
        path=out/(stem+".parquet")
        if not path.exists():
            continue
        stats, _=market_stats(path)
        report[title]=stats
        rows=[(f'{r["horizon"]}h {"up" if r["side"]>0 else "down"} impulse',r["daily_equal_mean"])
              for r in stats if r["slice"]=="all"]
        svg_bars(out/(stem+".svg"),f'{title.title()} paired continuation markouts',
                 'High relative activity minus ordinary activity, equal UTC-day weighting (basis points).',
                 rows,max(1,max(abs(v) for _,v in rows)))
    report["downside_robustness"]=downside_robustness(out)
    labels = {("broad","all"):"Broad universe", ("broad","outside_liquid50"):"Outside Liquid 50",
              ("broad","neither_leg_traded"):"Neither symbol in trader history",
              ("broad","exclude_best_five_days"):"Broad, drop best five days",
              ("liquid50","all"):"Audited Liquid 50",
              ("liquid50","before_broad_test"):"Audited, earlier dates",
              ("liquid50","exclude_best_five_days"):"Audited, drop best five days"}
    downside = [(labels[(r["universe"],r["sample"])],r["fade"]["daily_equal_mean"])
                for r in report["downside_robustness"] if (r["universe"],r["sample"]) in labels
                and "daily_equal_mean" in r["fade"]]
    if downside:
        svg_bars(out/"downside-regime.svg","Four-hour bounce after busy declines",
                 "BTC down over the preceding day. Equal-day mean markouts less 14 bp friction.",
                 downside,max(1,max(abs(v) for _,v in downside)))
    for title in ("market","liquid50"):
        path=out/(title+"-leadership.parquet")
        if path.exists():
            report[title+"_leadership"]=leadership_stats(path)
    s=report["selection"]["activation"]["features"]
    factors=[(n,(s[k][k+"_equal_pct"]["median"]-.5)*100) for n,k in
             (("24h realized volatility","vol_rank"),("1h relative quote volume","relq"),
              ("1h relative trade count","reln"),("Absolute 24h return","abs_rank"),("Signed 24h return","r_rank"))]
    svg_bars(out/"asset-selection.svg","Which markets became active for the trader?",
             'Median percentile among same-time inactive peers of similar liquidity, minus 50.',factors,50.)
    response = []
    for group, side in (("long","Long"),("short","Short")):
        for action in ("add","reduce"):
            r = report["inventory"][group]["ordinary_vs_busy"][action]
            if r["symbol_equal_diff_pp"] is not None:
                response.append((f"{side} {action}", r["symbol_equal_diff_pp"]))
    if response:
        svg_bars(out/"inventory-response.svg","Inventory adjustments on busy minutes",
             "Busy minute minus quiet minute, matched within lifecycle and age (percentage points).",
             response,max(1,max(abs(v) for _,v in response)))
    (out/"findings.json").write_text(json.dumps(report,indent=2,allow_nan=False),encoding="utf-8")


if __name__=="__main__":
    main()
