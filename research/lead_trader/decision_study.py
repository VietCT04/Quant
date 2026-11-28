"""Build verified opportunity panels and fit inverse models against observed decisions."""

import argparse
from contextlib import closing
from datetime import date
from pathlib import Path
from time import perf_counter
import json
import sqlite3

import polars as pl
import quant as q

from . import decision_evidence as evidence
from .behavior_study import table
from .collector_store import coverage


def build(source,out,hourly,audited,metrics_dir,get_metrics=False,extra=None):
    t0=perf_counter()
    pos,orders,legs,lives=evidence.grouped_frames(source)
    if get_metrics:
        symbols=sorted(lives,key=lambda x:len(lives[x]),reverse=True)[:12]
        from datetime import timedelta
        days=[(date(2026,11,20)+timedelta(days=i)).isoformat() for i in range(8)]
        captured=evidence.public_metrics(metrics_dir,symbols,days)
        (out/"metrics-download.json").write_text(json.dumps(captured,indent=2),encoding="utf-8")
    features=evidence.Hourly(hourly,audited.parent/"simulator_aux"/"funding",metrics_dir,extra)
    daily=list(evidence.symbol_daily(pos,features))
    table(out,"symbol-days",daily)
    table(out,"reported-openings",list(evidence.reported_openings(pos,features)))
    print("Symbol-day risk sets",len(daily),flush=True)
    with closing(sqlite3.connect((source/"archive.sqlite").resolve().as_uri()+"?mode=ro",uri=True)) as db:
        spans=coverage(db)
    dataset=q.data.open(audited,universe=None)
    rows=[]
    for sym,candidates in evidence.candidate_clocks(pos,orders,legs,lives,spans):
        rows.extend(evidence.finalize_clock(candidates,features,dataset,sym))
    table(out,"decision-clocks",evidence.reversal_context(rows))
    print("Minute risks and execution size cases",len(rows),flush=True)
    metadata=dict(source=str(source),assets=len(features.symbols),hourly_extra=str(extra) if extra else None,funding_symbols=len(features.funding),
                  metrics_symbols=len(features.metrics),verified_order_windows=spans,build_seconds=perf_counter()-t0)
    (out/"evidence.json").write_text(json.dumps(metadata,indent=2),encoding="utf-8")


def main():
    args=argparse.ArgumentParser(description=__doc__)
    args.add_argument("out",type=Path)
    args.add_argument("--source",type=Path,default=Path("data/research/lead-trader/behavior-2026-11-28"))
    args.add_argument("--hourly",type=Path,default=Path("data/research/lead-trader/market-hourly"))
    args.add_argument("--audited",type=Path,default=Path("data/binance/um/bars_5s"))
    args.add_argument("--build-only",action="store_true")
    args.add_argument("--models-only",action="store_true")
    args.add_argument("--get-metrics",action="store_true")
    args.add_argument("--metrics-dir",type=Path)
    args.add_argument("--hourly-extra",type=Path)
    args.add_argument("--only",nargs="+",help="Update only these model targets")
    args.add_argument("--refresh-reversal-context",action="store_true",help="Join outgoing facts for an existing set of size cases")
    a=args.parse_args()
    a.out.mkdir(parents=True,exist_ok=True)
    if not a.models_only:
        build(a.source,a.out,a.hourly,a.audited,a.metrics_dir or a.out/"public-metrics",a.get_metrics,a.hourly_extra)
    if a.refresh_reversal_context:
        rows=pl.read_parquet(a.out/"decision-clocks.parquet").to_dicts()
        table(a.out,"decision-clocks",evidence.reversal_context(rows))
    if not a.build_only:
        from .decision_fit import fit_all
        fit_all(a.out,a.only)


if __name__=="__main__":
    main()
