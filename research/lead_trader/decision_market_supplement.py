"""Supplement missing public hourly contracts for the inverse decision study."""

import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from io import BytesIO
from zipfile import ZipFile
import csv
import json
from pathlib import Path
import time
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import urlopen

import polars as pl


HOUR=3600000
MARKET="https://data.binance.vision/data/futures/um/monthly/klines"
REST="https://fapi.binance.com/fapi/v1/klines"
SCHEMA=("ms","open","high","low","close","quote","trades","buy_quote")


def parse(rows, end):
    for row in rows:
        if row and str(row[0]).isdigit():
            ms=int(row[0])
            if ms+HOUR<=end and int(row[6])==ms+HOUR-1:
                yield (ms,*(float(row[i]) for i in (1,2,3,4,7)),int(row[8]),float(row[10]))


def archived(symbol):
    name=quote(symbol,safe="")
    rows=[]
    for month in ("2026-07","2026-08","2026-09","2026-10"):
        try:
            with urlopen(f"{MARKET}/{name}/1h/{name}-1h-{month}.zip",timeout=8) as r:
                body=r.read()
            with ZipFile(BytesIO(body)) as z:
                from io import TextIOWrapper
                with z.open(z.namelist()[0]) as f:
                    rows.extend(parse(csv.reader(TextIOWrapper(f)),2**63-1))
        except HTTPError as error:
            if error.code!=404:
                return rows,error.code
        except (URLError,TimeoutError) as error:
            return rows,str(error)
    return rows,None


def supplement(folder,out):
    out.mkdir(parents=True,exist_ok=True)
    m=json.loads((folder/"roster.json").read_text(encoding="utf-8"))
    known={p.stem for p in folder.glob("*.parquet")}
    existing={p.stem for p in out.glob("*.parquet")}
    missing=sorted(set(m["included"])-known-existing)
    active={r["symbol"] for r in m["exchange_info"]["symbols"] if r.get("status")=="TRADING"}
    missing.sort(key=lambda x:x not in active)
    end=m["cutoff_ms"]
    start=int(datetime(2026,11,1,tzinfo=UTC).timestamp()*1000)
    rest_allowed=True
    status={}
    with ThreadPoolExecutor(max_workers=6) as pool:
        for symbol,(rows,issue) in zip(missing,pool.map(archived,missing)):
            info={"archive_rows":len(rows),"archive_error":issue}
            if rest_allowed:
                time.sleep(.75)
                query=urlencode(dict(symbol=symbol,interval="1h",limit=1000,startTime=start,endTime=end-1))
                try:
                    with urlopen(REST+"?"+query,timeout=8) as response:
                        result=json.loads(response.read())
                        reported_used=int(response.headers.get("X-MBX-USED-WEIGHT-1M","0"))
                    new=list(parse(result,end))
                    rows.extend(new)
                    info.update(november_rows=len(new),reported_minute_weight=reported_used)
                    if reported_used>=900:
                        time.sleep(61)
                except HTTPError as exc:
                    info["november_status"]=exc.code
                    if exc.code in (418,429,451,403):
                        rest_allowed=False
                        info["retry_after"]=exc.headers.get("Retry-After")
                except (URLError,TimeoutError) as exc:
                    info["november_error"]=str(exc)
                    rest_allowed=False
            unique={r[0]:r for r in rows}
            if unique:
                pl.DataFrame(sorted(unique.values()),schema=SCHEMA,orient="row").write_parquet(out/(symbol+".parquet"))
            status[symbol]={**info,"rows":len(unique)}
            if len(status)%20==0:
                print("supplement",len(status),"/",len(missing),flush=True)
    (out/"supplement.json").write_text(json.dumps(status,indent=2,ensure_ascii=False),encoding="utf-8")


if __name__=="__main__":
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("hourly",type=Path)
    p.add_argument("out",type=Path)
    a=p.parse_args()
    supplement(a.hourly,a.out)
