"""Materialize supplementary five-second research bars from checksummed Binance daily ZIPs."""
import argparse
from datetime import UTC, datetime
from hashlib import sha256
import json
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen

import polars as pl
from marketdata.aggregate import (fill_bars, klines, mask_minutes, reconcile, sparse_bars)

BASE="https://data.binance.vision/data/futures/um/daily"
DAY=86400000
CAP=45*1024**2


def obtain(url, dst, limit):
    with urlopen(url+".CHECKSUM",timeout=20) as stream:
        expected=stream.read(1024).decode().split()
    if len(expected)!=2 or expected[1].lstrip("*")!=dst.name:
        raise ValueError("Archive checksum metadata does not match filename")
    if not dst.exists() or sha256(dst.read_bytes()).hexdigest()!=expected[0].lower():
        with urlopen(Request(url,method="HEAD"),timeout=20) as stream:
            size=int(stream.headers.get("Content-Length") or 0)
        if size>limit:
            raise ValueError(f"Archive cap: {size} bytes")
        with urlopen(url,timeout=40) as stream:
            raw=stream.read(limit+1)
        if len(raw)>limit or sha256(raw).hexdigest()!=expected[0].lower():
            raise ValueError("Archive size or SHA-256 mismatch")
        dst.write_bytes(raw)
    return expected[0].lower()


def aggregate_day(symbol, day, target):
    stem=target/f"{symbol}-{day}"
    output=stem.with_suffix(".parquet")
    if output.exists():
        return json.loads(stem.with_suffix(".json").read_text(encoding="utf-8"))
    sym=quote(symbol,safe="")
    agg=target/f"{symbol}-aggTrades-{day}.zip"
    ref=target/f"{symbol}-1m-{day}.zip"
    aid=obtain(f"{BASE}/aggTrades/{sym}/{sym}-aggTrades-{day}.zip",agg,CAP)
    rid=obtain(f"{BASE}/klines/{sym}/1m/{sym}-1m-{day}.zip",ref,2*1024**2)
    lo=int(datetime.fromisoformat(day).replace(tzinfo=UTC).timestamp()*1000)
    hi=lo+DAY
    work=target/"scratch"
    work.mkdir(exist_ok=True)
    sparse,info=sparse_bars(agg,lo,hi,lo,hi,lo,5000,8*1024**2,work,256*1024**2)
    reference=klines(ref.read_bytes(),lo,hi,lo,hi)
    if len(info.id_gap_timestamps_ms)!=info.agg_id_gaps:
        raise ValueError("Incomplete source-gap ledger")
    gaps=[(max(lo,a//60000*60000),min(hi,(b//60000+1)*60000)) for a,b in info.id_gap_timestamps_ms]
    sparse,reference,gaps=mask_minutes(sparse,reference,gaps)
    while True:
        report=reconcile(sparse,reference,lo,hi)
        if not report["mismatched_minutes"] and not report["daily_outliers"]:
            break
        invalid={r["timestamp_ms"] for r in report["first_mismatch_samples"]}
        invalid.update(report["missing_agg_timestamps_ms"])
        invalid.update(report["missing_ref_timestamps_ms"])
        if not invalid or len(invalid)+sum((b-a)//60000 for a,b in gaps)>432:
            raise ValueError(f"Source reconciliation needs review: {report['mismatched_minutes']} mismatched minutes")
        dropped=[[x,x+60000] for x in sorted(invalid)]
        sparse,reference,gaps=mask_minutes(sparse,reference,gaps+dropped)
    dense=fill_bars(sparse,reference.filter(pl.col("volume")>0),5000,lo,hi,lo)
    dense.write_parquet(output)
    data={"symbol":symbol,"day":day,"rows":dense.height,"agg_sha256":aid,"reference_sha256":rid,
          "verified_gaps":gaps,"first_agg_id":info.first_id,"last_agg_id":info.last_id,"zip_bytes":agg.stat().st_size}
    stem.with_suffix(".json").write_text(json.dumps(data,indent=2),encoding="utf-8")
    return data


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("source",type=Path)
    p.add_argument("out",type=Path)
    p.add_argument("--limit",type=int,default=20)
    a=p.parse_args()
    a.out.mkdir(parents=True,exist_ok=True)
    d=pl.read_parquet(a.source/"decision-clocks.parquet")
    d=d.filter((pl.col("risk")=="size")&(~pl.col("has_fast"))&
               pl.col("action").is_in(["add","reduce","reverse_close"]))
    days=d.with_columns(pl.from_epoch("ms",time_unit="ms").dt.date().cast(pl.String).alias("day"))
    candidates=days.group_by("symbol","day").agg(pl.len().alias("orders")).sort(["orders","symbol","day"],descending=[True,False,False])
    # Take at most two observations per symbol in the first pass for concentration control.
    selected=[]
    counts={}
    for r in candidates.iter_rows(named=True):
        sym=r["symbol"]
        if counts.get(sym,0)<2:
            selected.append((sym,r["day"],r["orders"]))
            counts[sym]=counts.get(sym,0)+1
    results=[]
    for symbol,day,n in selected[:a.limit]:
        try:
            r=aggregate_day(symbol,day,a.out)
        except (ValueError,URLError,TimeoutError) as exc:
            r={"symbol":symbol,"day":day,"error":str(exc)}
            if isinstance(exc,HTTPError) and exc.code in (418,429):
                results.append(r|{"target_orders":n})
                break
        results.append(r|{"target_orders":n})
        print(day,ascii(symbol),r.get("rows",r.get("error")),flush=True)
    (a.out/"manifest.json").write_text(json.dumps(results,indent=2),encoding="utf-8")


if __name__=="__main__":
    main()
