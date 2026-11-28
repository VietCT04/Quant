"""Causal hourly features, selection risk sets, and independent market markouts."""

from collections import defaultdict
from datetime import UTC, datetime
import json

import numpy as np
import polars as pl
from scipy.stats import rankdata


HOUR = 3_600_000
DAY = 24 * HOUR
ORIGIN = int(datetime(2026, 5, 1, tzinfo=UTC).timestamp() * 1000)
FEATURES = ("q24", "q_rank", "relq", "reln", "q_accel", "n_accel", "vol", "vol_rank", "r1",
            "r4", "r24", "abs_rank", "r_rank", "flow", "residual", "btc_corr", "listed_days")


def trailing(x, n):
    if n > len(x):
        return np.full(len(x), np.nan)
    valid = np.isfinite(x)
    sums = np.r_[0., np.cumsum(np.where(valid, x, 0.))]
    counts = np.r_[0, np.cumsum(valid)]
    total = sums[n:] - sums[:-n]
    total[counts[n:] - counts[:-n] != n] = np.nan
    return np.r_[np.full(n-1, np.nan), total]


def lag(x, n):
    return np.r_[np.full(min(n,len(x)), np.nan), x[:-n]]


def market(folder, extra=None):
    meta = json.loads((folder / "roster.json").read_text(encoding="utf-8"))
    paths = {p.stem:p for p in folder.glob("*.parquet")}
    if extra is not None:
        for p in extra.glob("*.parquet"):
            paths.setdefault(p.stem,p)
    symbols = sorted(paths)
    origin = meta.get("start_ms", ORIGIN)
    n = (meta["cutoff_ms"] - origin) // HOUR
    fields = ("open", "close", "quote", "trades", "buy_quote")
    raw = {k: np.full((len(paths), n), np.nan, dtype="f8") for k in fields}
    age = np.full((len(paths), n), np.nan, dtype="f4")
    listings = {r["symbol"]: r["onboardDate"] for r in meta["exchange_info"]["symbols"] if r.get("onboardDate")}
    for j, symbol in enumerate(symbols):
        path=paths[symbol]
        table = pl.read_parquet(path, columns=["ms", *fields])
        t = (table["ms"].to_numpy() - origin) // HOUR
        if t.size != np.unique(t).size:
            raise ValueError(f"Duplicate hours in {path.name}")
        observed = (t >= 0) & (t < n)
        ids = t[observed]
        for key in fields:
            raw[key][j, ids] = table[key].to_numpy()[observed]
        stamp = listings.get(path.stem)
        if ids.size and stamp and stamp <= origin + HOUR * (ids.min() + 1):
            age[j, ids] = (origin + HOUR * (ids + 1) - stamp) / DAY
    features = {key: np.full(age.shape, np.nan, dtype="f4") for key in FEATURES if not key.endswith("_rank")}
    features["listed_days"] = age
    btclog = np.log(raw["close"][symbols.index("BTCUSDT")])
    btc1 = btclog - lag(btclog, 1)
    btc24 = np.expm1(btclog - lag(btclog, 24))
    forward = {k: np.full(age.shape, np.nan, dtype="f4") for k in (1, 4, 24)}
    with np.errstate(divide="ignore", invalid="ignore"):
        for j in range(len(paths)):
            op, close, quote, trades, buy = (raw[k][j] for k in fields)
            log = np.log(close)
            r = log - lag(log, 1)
            q24 = trailing(quote, 24)
            x, y = (np.where(np.isfinite(r) & np.isfinite(btc1), v, np.nan) for v in (r, btc1))
            sx, sy, sxx, syy, sxy = (trailing(a, 168) for a in (x, y, x*x, y*y, x*y))
            factor = dict(q24=q24, relq=quote / (lag(q24, 1) / 24), reln=trades / (lag(trailing(trades, 24), 1) / 24),
                          q_accel=quote / lag(quote, 1), n_accel=trades / lag(trades, 1),
                          vol=np.sqrt(np.maximum(0., trailing(r*r, 24))),
                          r1=np.expm1(r), r4=np.expm1(log-lag(log, 4)), r24=np.expm1(log-lag(log, 24)),
                          flow=(2*buy-quote)/quote, residual=np.expm1(log-lag(log, 24))-btc24,
                          btc_corr=(sxy-sx*sy/168)/np.sqrt(np.maximum(sxx-sx*sx/168,0)*np.maximum(syy-sy*sy/168,0)))
            for key, val in factor.items():
                features[key][j] = np.where(np.isfinite(val), val, np.nan)
            for k, arr in forward.items():
                # Predictor at completed hour i; return from following open to kth following close.
                ok = np.isfinite(trailing(close, k)[k:]) & (op[1:n-k+1] > 0)
                arr[j, :-k] = np.where(ok, close[k:] / op[1:n-k+1] - 1, np.nan)
    for key, val in {"q_rank": features["q24"], "vol_rank": features["vol"], "r_rank":features["r24"],
                     "abs_rank": np.abs(features["r24"])}.items():
        ranks = rankdata(val, axis=0, nan_policy="omit")
        features[key] = ((ranks-.5)/np.maximum(1, np.isfinite(val).sum(axis=0))).astype("f4")
    return symbols, features, forward, btc24, origin


def utc_hour(stamp):
    return (stamp - ORIGIN - 1) // HOUR - 1


def spells(rows):
    grouped = defaultdict(list)
    for row in rows:
        grouped[row["symbol"]].append((row["opened"], row.get("closed") or row["updateTime"], row["status"]))
    for symbol, spans in grouped.items():
        merged = []
        for start, end, status in sorted(spans):
            if merged and start - merged[-1][1] < 7 * DAY:
                merged[-1][2] += 1
                merged[-1][3] |= status != "All Closed"
                merged[-1][1] = max(end, merged[-1][1])
            else:
                merged.append([start, end, 1, status != "All Closed"])
        for start, end, count, partial in merged:
            yield dict(symbol=symbol, start=start, end=end, positions=count, partial=partial)


def selection(positions, symbols, f):
    symbol_index = {s:i for i,s in enumerate(symbols)}
    episodes = list(spells(positions))
    lo = min(r["updateTime"] for r in positions) + 7*DAY
    hi = max(r["updateTime"] for r in positions)
    by_symbol = defaultdict(list)
    for ep in episodes:
        by_symbol[ep["symbol"]].append(ep)
    results = []
    n = f["q24"].shape[1]
    for ep in episodes:
        j = symbol_index.get(ep["symbol"])
        if j is None:
            continue
        for event, t in (("activation", ep["start"]), ("cessation", ep["end"])):
            i = utc_hour(t)
            if not lo <= t <= hi or not 0 <= i < n or not np.isfinite(f["q_rank"][j,i]):
                continue
            if event == "cessation" and (ep["partial"] or t > hi - 7*DAY):
                continue
            active = {s for s, es in by_symbol.items() if any(e["start"] < t < e["end"] + 7*DAY for e in es)}
            controls = np.isfinite(f["q_rank"][:,i])
            controls[[symbol_index[s] for s in active if s in symbol_index]] = False
            controls[j] = False
            equal = controls & (np.abs(f["q_rank"][:,i]-f["q_rank"][j,i]) <= .10)
            rec = {**ep, "event":event, "ms":t, "risk_set":int(controls.sum()), "liquidity_matched":int(equal.sum())}
            for key in FEATURES:
                col = f[key][:,i]
                rec[key] = float(col[j])
                for name, cohort in (("broad",controls),("equal",equal)):
                    vals = col[cohort & np.isfinite(col)]
                    rec[f"{key}_{name}"] = float(np.median(vals)) if len(vals) else None
                    rec[f"{key}_{name}_pct"] = float(np.mean(vals < col[j]) + .5*np.mean(vals == col[j])) if (
                        len(vals) and np.isfinite(col[j])) else None
            results.append(rec)
    return results


def openings(positions, symbols, f, future):
    index = {s:i for i,s in enumerate(symbols)}
    closes = {(p["symbol"], -p["direction"], p["closed"]) for p in positions if p["status"] == "All Closed"}
    table = []
    for row in positions:
        j, i = index.get(row["symbol"]), utc_hour(row["opened"])
        if j is None or not 0 <= i < f["q24"].shape[1] or not np.isfinite(f["q_rank"][j,i]):
            continue
        direct = any((row["symbol"], row["direction"], row["opened"] + delta) in closes for delta in (-1,0,1))
        # A full-hour markout starts at the first UTC hourly open strictly after the trader's timestamp.
        label = (row["opened"] - ORIGIN) // HOUR
        table.append({**row, "opposite_boundary":direct, **{k:float(f[k][j,i]) for k in FEATURES},
                      **{f"future{k}":float(v[j,label]) if label < v.shape[1] else None for k,v in future.items()}})
    return table


def transfer(symbols, f, future, btc, origin):
    """Match simultaneous public-market opportunities in coarse liquidity/volatility cells."""
    summary = []
    names = np.array(symbols)
    for horizon, produced in future.items():
        for i in range(24, len(btc)-horizon, horizon):
            if not np.isfinite(btc[i]):
                continue
            ret, activity, liquid_rank, vol_rank, flow = (f[k][:,i] for k in ("r4","relq","q_rank","vol_rank","flow"))
            delayed = produced[:,i+1]
            usable = np.isfinite(produced[:,i]+ret+activity+liquid_rank+vol_rank+flow)
            usable &= (liquid_rank >= .25) & (np.abs(ret) >= .02)
            quiet = usable & (activity >= .8) & (activity <= 1.25)
            shock = usable & (activity >= 2.)
            cohort = {}
            for j in np.flatnonzero(quiet):
                key = (ret[j]>0,int(liquid_rank[j]*5),int(vol_rank[j]*4))
                cohort.setdefault(key,[]).append(j)
            for j in np.flatnonzero(shock):
                key = (ret[j]>0,int(liquid_rank[j]*5),int(vol_rank[j]*4))
                control = cohort.get(key)
                if not control:
                    continue
                match = min(control, key=lambda k: abs(abs(ret[k])-abs(ret[j])) + .04*abs(liquid_rank[k]-liquid_rank[j]))
                if abs(abs(ret[j])-abs(ret[match])) > .025:
                    continue
                side = 1 if ret[j]>0 else -1
                summary.append(dict(ms=origin+(i+1)*HOUR, symbol=str(names[j]), control=str(names[match]), side=side,
                                    btc_regime="up" if btc[i]>=0 else "down", horizon=horizon,
                                    own_bps=side*float(produced[j,i])*1e4, control_bps=side*float(produced[match,i])*1e4,
                                    delayed_own_bps=side*float(delayed[j])*1e4, delayed_control_bps=side*float(delayed[match])*1e4,
                                    activity=float(activity[j]), past_bps=float(ret[j])*1e4, control_past_bps=float(ret[match])*1e4,
                                    flow_aligned=bool(flow[j]*side>0)))
    return summary


def leadership(symbols, f, future, origin):
    # Compare quiet versus busy hours for already liquid and volatile relative leaders.
    rows = []
    for i in range(23, f["q24"].shape[1] - 24, 24):
        qr, rr, vr, activity = (f[k][:,i] for k in ("q_rank", "r_rank", "vol_rank", "relq"))
        ready = np.isfinite(future[24][:,i] + future[4][:,i] + activity)
        leaders = ready & (qr >= .7) & (rr >= .75) & (vr >= .8) & (f["r24"][:,i] > 0)
        busy = np.flatnonzero(leaders & (activity >= 1.5))
        if not busy.size:
            continue
        for j in np.flatnonzero(leaders & (activity <= 1.)):
            peers = busy[(np.abs(qr[busy]-qr[j]) <= .10) & (np.abs(rr[busy]-rr[j]) <= .10)
                         & (np.abs(vr[busy]-vr[j]) <= .10)]
            if not peers.size:
                continue
            v = peers[np.argmin(np.abs(qr[peers]-qr[j]) + np.abs(rr[peers]-rr[j]) + np.abs(vr[peers]-vr[j]))]
            rows.append(dict(ms=origin+(i+1)*HOUR,symbol=symbols[j],control=symbols[v],
                             ret24=float(f["r24"][j,i]),control_ret24=float(f["r24"][v,i]),
                             quiet_activity=float(activity[j]),busy_activity=float(activity[v]),
                             **{name+str(hz):float(future[hz][key,i])*1e4
                                for hz in (4,24) for name,key in (("quiet",j),("busy",v))}))
    return rows
