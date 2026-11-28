"""Build strictly covered hourly bars from the existing audited five-second SDK."""

import argparse
from datetime import UTC, datetime
import json
from pathlib import Path

import polars as pl

from quant import data
from .behavior_market import HOUR, leadership, market, transfer
from .behavior_study import table


FIELDS = {"open":"open", "close":"close", "quote_volume":"quote",
          "buy_quote_volume":"buy_quote", "trade_count":"trades"}


def aggregate(source, out):
    dataset = data.open(source, universe=None)
    roster = json.loads((dataset.meta / "universes" / "liquid50.json").read_text(encoding="utf-8"))
    listings = [{"symbol":s["symbol"],"onboardDate":s["listing_timestamp_ms"]} for s in roster["symbols"]]
    start, end = (int(datetime.fromisoformat(t).replace(tzinfo=UTC).timestamp()*1000)
                  for t in ("2024-11-25","2026-11-25"))
    symbols = tuple(s["symbol"] for s in listings)
    out.mkdir(parents=True,exist_ok=True)
    blocks = data.iter_blocks(data.select(dataset,start=start,end=end,symbols=symbols,
                                         fields=tuple(FIELDS)), block_steps=14_400)
    frames = {symbol:[] for symbol in symbols}
    for block in data.resample(blocks,HOUR):
        for j,symbol in enumerate(symbols):
            valid = block.status[:,j] <= data.EMPTY
            frames[symbol].append(pl.DataFrame({"ms":block.timestamps_ms[valid]} |
                {name:block.values[key][:,j][valid] for key,name in FIELDS.items()}))
    for sym, parts in frames.items():
        pl.concat(parts).write_parquet(out/(sym+".parquet"))
    info = dict(start_ms=start,cutoff_ms=end,exchange_info={"symbols":listings,"source":"audited five-second SDK"})
    (out/"roster.json").write_text(json.dumps(info,indent=2),encoding="utf-8")


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("out",type=Path)
    parser.add_argument("--source",type=Path,default=Path("data/binance/um/bars_5s"))
    parser.add_argument("--reuse-hours",action="store_true")
    a=parser.parse_args()
    folder=a.out/"liquid50-hourly"
    if not a.reuse_hours:
        aggregate(a.source, folder)
    symbols,f,future,btc,origin=market(folder)
    rows=transfer(symbols,f,future,btc,origin)
    table(a.out,"liquid50-markouts",rows)
    table(a.out,"liquid50-leadership",leadership(symbols,f,future,origin))
    print("Audited Liquid 50 markouts",len(rows),flush=True)


if __name__=="__main__":
    main()
