"""Estimate conditional decision rates and validate transparent, regularized models."""

from collections import Counter, defaultdict
from hashlib import sha256
import json

import numpy as np
import polars as pl
from scipy.optimize import minimize
from scipy.special import expit
from scipy.stats import rankdata

from .behavior_market import DAY
from .decision_evidence import CARRY, FAST, HIST, HOURLY, STATE

INTERACTIONS = (("activity_1m","exposure"),("ret_1m","r24"),("distance_to_cost","exposure"),
                ("last_gap","activity_1m"),("vol_rank","r_rank"),("alt_residual","alt_beta_change"))
SIGNALS = ("vol_rank","q_rank","r_rank","abs_rank","relq","reln","quote_rel_day","trade_rel_day",
           "r4","alt_residual","residual","coin_vol","btc_beta_change","alt_beta_change","alt_corr",
           "listed_days","oi_change","funding","last_gap","prior_orders_15m","last_order_usdt","position_age",
           "prior_realized_15m","exposure","distance_to_cost","inventory_change_15m","ret_15s","ret_1m",
           "ret_5m","flow_1m","activity_1m","trade_intensity1m","range_drawdown5m")


def values(t, name):
    return t[name].cast(pl.Float64).to_numpy().copy() if name in t.columns else np.full(t.height,np.nan)


def column(t,name):
    if name.startswith("symbol="):
        return (t["symbol"].to_numpy()==name.removeprefix("symbol=")).astype(float)
    return values(t,name)


class Design:
    def __init__(self,t,idx,names,nonlinear):
        self.features=[]
        self.columns=[]
        for name in dict.fromkeys(names):
            v=column(t,name)[idx]
            v=v[np.isfinite(v)]
            if len(v)<max(10,.1*len(idx)) or len(np.unique(v))<2:
                continue
            p10,p50,p90=np.quantile(v,[.1,.5,.9])
            scale=max((p90-p10)/2.56,np.std(v)/5,1e-9)
            self.features.append((name,p50,scale,name in nonlinear))
            self.columns.append(name)
            if name in nonlinear:
                self.columns += [f"{name} > center",f"{name} missing"]
        keep=set(self.columns)
        self.pairs=[(a,b) for a,b in INTERACTIONS if a in keep and b in keep and a in nonlinear and b in nonlinear]
        self.columns += [a+" x "+b for a,b in self.pairs]

    def matrix(self,t,idx):
        vals={"intercept":np.ones(len(idx))}
        for name,center,scale,nonlinear in self.features:
            x=column(t,name)[idx]
            z=np.where(np.isfinite(x),np.clip((x-center)/scale,-6,6),0.)
            vals[name]=z
            if nonlinear:
                vals[f"{name} > center"]=np.maximum(0,z)
                vals[f"{name} missing"]=~np.isfinite(x)
        vals.update((a+" x "+b,vals[a]*vals[b]) for a,b in self.pairs)
        return np.column_stack(list(vals.values()))


def fitting_indices(y,index,binary):
    # Keep all rare outcomes and sample only excess controls within the training fold.
    if not binary or len(index)<=7500:
        return index,np.ones(len(index))
    minor = int(np.mean(y[index])<.5)
    positives=index[y[index]==minor]
    ordinary=index[y[index]!=minor]
    n=min(len(ordinary),max(2800,min(11000,12*len(positives))))
    if len(ordinary)<=n:
        return index,np.ones(len(index))
    chosen=np.random.default_rng(420).choice(ordinary,n,replace=False)
    return np.r_[positives,chosen],np.r_[np.ones(len(positives)),np.full(n,len(ordinary)/n)]


def model(x,y,binary,weights=None):
    penalty=np.r_[0.,np.repeat(8.,x.shape[1]-1)]
    if not binary:
        # Size errors are evaluated by MAE. A smooth absolute loss keeps tail clips
        # from forcing an estimate of the mean onto a median-based comparison.
        def absolute_loss(beta):
            residual=x@beta-y
            smooth=np.sqrt(residual**2+.2**2)
            return (np.sum(smooth-.2)+.5*np.sum(penalty*beta**2),
                    x.T@(residual/smooth)+penalty*beta)
        x0=np.r_[np.median(y),np.zeros(x.shape[1]-1)]
        fit=minimize(absolute_loss,x0,jac=True,method="L-BFGS-B",options={"maxiter":145,"ftol":1e-8})
        if not np.isfinite(fit.fun):
            raise ValueError("Nonfinite size fit")
        return fit.x
    if weights is None:
        weights=np.ones(len(y))
    def loss(beta):
        logit=x@beta
        return (np.sum(weights*(np.logaddexp(0,logit)-y*logit))+.5*np.sum(penalty*beta**2),
                x.T@(weights*(expit(logit)-y))+penalty*beta)
    x0=np.zeros(x.shape[1])
    p=(np.dot(y,weights)+.5)/(weights.sum()+1)
    x0[0]=np.log(p/(1-p))
    result=minimize(loss,x0,jac=True,method="L-BFGS-B",options={"maxiter":125,"ftol":5e-8})
    if not np.isfinite(result.fun):
        raise ValueError("Nonfinite logistic fit")
    return result.x


def scores(y,p,binary):
    if not binary:
        return {"mae_log":float(np.mean(np.abs(p-y))),"medae_log":float(np.median(np.abs(p-y)))}
    p=np.clip(p,1e-7,1-1e-7)
    pos=int(y.sum())
    n=len(y)
    ap=None
    auc=None
    if 0<pos<n:
        rk=rankdata(p)
        auc=float((rk[y==1].sum()-pos*(pos+1)/2)/(pos*(n-pos)))
        order=np.argsort(-p,kind="stable")
        right=np.r_[np.flatnonzero(np.diff(p[order])!=0),n-1]
        tp=np.cumsum(y[order])[right]
        ap=float(np.sum((tp/(right+1))*np.diff(np.r_[0,tp]))/pos)
    return {"logloss":float(np.mean(-y*np.log(p)-(1-y)*np.log1p(-p))),
            "brier":float(np.mean((p-y)**2)),"auc":auc,"ap":ap,"predicted_rate":float(p.mean())}


def blocks(t,mode,name):
    days=t["ms"].to_numpy()//DAY
    date=np.unique(days)
    idx=np.arange(len(days))
    labels=t["symbol"].to_numpy()
    if mode=="chronology" and len(date)>=4:
        pivot=date[min(len(date)-1,max(2,int(len(date)*.67)))]
        embargo=7 if name=="deactivation" else 0
        train=idx[days < pivot-embargo]
        test=idx[days>=pivot]
        if "life_id" in t.columns:
            life=t["life_id"].to_numpy()
            train=train[~np.isin(life[train],np.unique(life[test]))]
        yield train,test
    elif mode=="unseen_symbol":
        lookup={str(x):int.from_bytes(sha256(str(x).encode()).digest()[:2],"big")%3 for x in set(labels)}
        fold=np.array([lookup[str(s)] for s in labels])
        for k in range(3):
            yield idx[fold!=k],idx[fold==k]


def targets(daily,events,openings):
    h=events.filter((pl.col("risk")=="hazard")&(pl.col("has_hourly")|pl.col("has_fast")))
    flat=events.filter((pl.col("risk")=="flat")&(pl.col("has_hourly")|pl.col("has_fast")))
    sized=events.filter((pl.col("risk")=="size")&(pl.col("has_hourly")|pl.col("has_fast")))
    active=daily.filter(pl.col("risk")=="activation")
    deactivate=daily.filter(pl.col("risk")=="deactivation")
    yield "activation",active,values(active,"target"),True,CARRY,()
    yield "deactivation",deactivate,values(deactivate,"target"),True,CARRY,()
    direction=active.filter(pl.col("target")==1)
    yield "direction_at_activation",direction,(values(direction,"entry_side")<0).astype(float),True,CARRY,()
    yield "reported_open_direction",openings,values(openings,"target"),True,(),()
    for side,sign in (("long",1),("short",-1)):
        v=values(active,"entry_side")
        yield "activation_"+side,active,(v==sign).astype(float),True,CARRY,()
        a=flat["action"].to_numpy()
        yield "verified_flat_"+side,flat,(a==side+"_entry").astype(float),True,HIST,()
        lane=h.filter(pl.col("direction")==sign)
        kinds=lane["action"].to_numpy()
        for act in ("add","reduce","reverse"):
            target=(kinds==act).astype(float)
            name=f"{side}_to_{'short' if side=='long' else 'long'}" if act=="reverse" else f"{act}_{side}"
            yield name,lane,target,True,HIST,STATE
    for side,sign in (("long",1),("short",-1)):
        lane=h.filter((pl.col("direction")==sign)&pl.col("has_fast"))
        kinds=lane["action"].to_numpy()
        for act in ("add","reduce","reverse"):
            yield f"fine_{act}_{side}",lane,(kinds==act).astype(float),True,HIST,STATE
    if "oi_change" in h.columns:
        oi=h.filter(pl.col("oi_change").is_finite())
        for act in ("add","reduce","reverse"):
            yield "oi_"+act,oi,(oi["action"].to_numpy()==act).astype(float),True,HIST,STATE
    yield "hold",h,(h["action"].to_numpy()=="hold").astype(float),True,HIST,STATE
    yield "flat_close",h,(h["action"].to_numpy()=="close").astype(float),True,HIST,STATE
    for action in ("open","add","reduce","reverse_open"):
        sub=sized.filter(pl.col("action")==action)
        s=values(sub,"leg_usdt")
        yield "large_clip_"+action,sub,(s>=50).astype(float),True,HIST,STATE
        actual=values(sub,"order_usdt") if action=="reverse_open" else s
        yield "order_usdt_"+action,sub,np.log1p(actual),False,HIST,STATE
        if action!="open":
            yield "resulting_exposure_"+action,sub,np.log1p(values(sub,"after_usdt")),False,HIST,STATE
    for act in ("add","reduce","reverse_open"):
        fine=sized.filter((pl.col("action")==act)&pl.col("has_fast"))
        yield "fine_order_usdt_"+act,fine,np.log1p(values(fine,"order_usdt")),False,HIST,STATE
    sub=sized.filter((pl.col("growth").is_not_null())&(pl.col("action").is_in(["open","reverse_open"])))
    yield "small_open_grows",sub,values(sub,"growth"),True,(*HIST,"leg_usdt"),()


def sufficient(y,train,test,binary):
    if len(train)<35 or len(test)<12:
        return False
    return not binary or min(float(y[train].sum()),float((1-y[train]).sum()))>=8


def validate(t,y,binary,history,state,name):
    results={}
    market=(*HOURLY,*FAST)
    for mode in ("chronology","unseen_symbol"):
        model_predictions=defaultdict(list)
        actual=[]
        for train,test in blocks(t,mode,name):
            if not sufficient(y,train,test,binary):
                continue
            yt=y[train]
            actual.extend(test)
            symbols=[s for s,n in Counter(t["symbol"].to_numpy()[train]).most_common(30) if n>=12]
            prior=(yt.sum()+.5)/(len(yt)+1) if binary else np.median(yt)
            model_predictions["frequency"].extend(np.full(len(test),prior).tolist())
            fit_idx,weights=fitting_indices(y,train,binary)
            known=(*history,*(f"symbol={s}" for s in symbols))
            sets={"history":known,"history_state":(*known,*state),
                  "history_market":(*known,*market),"full":(*known,*state,*market)}
            if name.startswith("activation"):
                sets["ranks_only"] = (*known,"q_rank","vol_rank","r_rank","abs_rank")
            if name.startswith("oi_"):
                sets["without_derivatives"] = (*known,*state,*(k for k in market if k not in {"oi","oi_change","funding"}))
            if not state:
                del sets["history_state"]
                del sets["history_market"]
            for key,cols in sets.items():
                nonlinear=set(cols)&set((*market,*state,"last_gap","last_order_usdt","position_age")) if key in {"full","without_derivatives"} else set()
                design=Design(t,train,cols,nonlinear)
                x=design.matrix(t,fit_idx)
                beta=model(x,y[fit_idx],binary,weights)
                guess=design.matrix(t,test)@beta
                model_predictions[key].extend((expit(guess) if binary else guess).tolist())
        if not actual:
            results[mode]={"state":"insufficient support"}
            continue
        idx=np.array(actual)
        z=y[idx]
        target=t[idx.tolist()]
        results[mode]={"n":len(z),"positives":int(z.sum()) if binary else None,"rate":float(z.mean()) if binary else None,
                       "days":int(target.select((pl.col("ms")//DAY).n_unique()).item()),
                       "symbols":target["symbol"].n_unique(),
                       "models":{m:scores(z,np.array(v),binary) for m,v in model_predictions.items()}}
    return results


def group_codes(t):
    _,ds=np.unique(t["ms"].to_numpy()//DAY,return_inverse=True)
    _,symbols=np.unique(t["symbol"].to_numpy(),return_inverse=True)
    return {"day":ds,"symbol":symbols}


def cohorts(t,split,cond,y,codes=None):
    idx=np.flatnonzero(split)
    if not len(idx):
        return None
    codes=codes or group_codes(t)
    flag=cond[idx]
    obs=y[idx]
    pos=float(obs[flag].sum())
    total=float(obs.sum())
    hits=int(flag.sum())
    if hits<5:
        return None
    risk=pos/hits
    base=total/len(idx)
    result={"n":int(len(idx)),"actions":int(total),"signal_n":hits,"signal_actions":int(pos),
            "action_given_signal":risk,"baseline":base,"signal_given_action":pos/total if total else None,
            "signal_prevalence":hits/len(idx),"difference":risk-base,"lift":risk/base if base else None}
    for name,groups in codes.items():
        rev=groups[idx]
        count=np.bincount(rev)
        present=count>0
        count=count[present]
        n=len(count)
        total=np.bincount(rev,weights=obs)[present]
        tested=np.bincount(rev,weights=flag)[present]
        events=np.bincount(rev,weights=obs*flag)[present]
        comparable=tested>=5
        result[f"{name}_clusters"]=n
        result[f"{name}_supporting_groups"]=int(comparable.sum())
        result[f"{name}_above_baseline"]=int(np.sum(comparable & (events*count>total*tested)))
        if n>=6 and comparable.sum()>=4:
            rng=np.random.default_rng(524)
            ix=rng.integers(n,size=(300,n))
            sizes=tested[ix].sum(axis=1)
            ok=sizes>0
            mean=events[ix].sum(axis=1)[ok]/sizes[ok]
            dif=mean-total[ix].sum(axis=1)[ok]/count[ix].sum(axis=1)[ok]
            result[f"{name}_rate_ci90"]=np.quantile(mean,[.05,.95]).tolist()
            result[f"{name}_difference_ci90"]=np.quantile(dif,[.05,.95]).tolist()
    return result


def rates(t,y,name):
    idx=list(blocks(t,"chronology",name))
    train, test=idx[0] if idx else (np.arange(t.height),np.array([],dtype=int))
    whole=np.ones(t.height,dtype=bool)
    latest=np.isin(np.arange(t.height),test)
    result=[]
    codes=group_codes(t)
    bands={}
    for sig in SIGNALS:
        x=values(t,sig)
        valid=np.isfinite(x)
        inputs=x[train]
        inputs=inputs[np.isfinite(inputs)]
        if len(inputs)<20 or len(np.unique(inputs))<3:
            continue
        lo,hi=np.quantile(inputs,[.25,.75])
        if lo==hi:
            continue
        bands[sig]={"low":(x<=lo,lo,valid),"high":(x>=hi,hi,valid)}
        for label,cond,p in (("low",x<=lo,lo),("high",x>=hi,hi)):
            pooled=cohorts(t,whole&valid,cond,y,codes)
            if pooled:
                result.append({"signal":sig,"band":label,"threshold":float(p),"pooled":pooled,
                               "chronology":cohorts(t,latest&valid,cond,y,codes) if latest.any() else None})
    for a,b in INTERACTIONS:
        if a not in bands or b not in bands:
            continue
        for ka in ("low","high"):
            for kb in ("low","high"):
                x,p,f=bands[a][ka]
                z,q,g=bands[b][kb]
                pool=cohorts(t,f&g,x&z,y,codes)
                if pool:
                    result.append({"signal":a+" + "+b,"band":ka+" / "+kb,"threshold":[float(p),float(q)],"pooled":pool,
                                   "chronology":cohorts(t,latest&f&g,x&z,y,codes) if latest.any() else None})
    return sorted(result,key=lambda r:abs((r["chronology"] or r["pooled"])["difference"]),reverse=True)



def surface(t,y,name,binary):
    if name.startswith(("activation","deactivation","direction_","reported_open_")):
        dimensions=(("vol_rank","q_rank"),("r_rank","alt_residual"))
    elif name.startswith(("order_","fine_order_","resulting_","large_","small_open_")):
        dimensions=(("last_order_usdt","exposure"),("distance_to_cost","activity_1m"))
    else:
        dimensions=(("last_gap","exposure"),("ret_1m","r24"),("activity_1m","exposure"))
    for a,b in dimensions:
        x,z=values(t,a),values(t,b)
        valid=np.isfinite(x)&np.isfinite(z)
        if valid.sum()<35:
            continue
        edges=[np.quantile(v[valid],[1/3,2/3]) for v in (x,z)]
        if any(n[0]==n[1] for n in edges):
            continue
        ix,jx=np.digitize(x,edges[0]),np.digitize(z,edges[1])
        cells=[]
        for i in range(3):
            for j in range(3):
                which=valid&(ix==i)&(jx==j)
                count=int(which.sum())
                v=y[which]
                cells.append(dict(x=i,y=j,n=count,actions=int(v.sum()) if binary else None,
                                  value=float(v.mean()) if binary and count else float(np.expm1(np.median(v)))
                                  if count else None))
        yield dict(x=a,y=b,cuts=[e.tolist() for e in edges],cells=cells,
                   baseline=float(y[valid].mean()) if binary else float(np.expm1(np.median(y[valid]))))

def interpret(t,y,binary,history,state,name):
    market=(*HOURLY,*FAST)
    idx=np.arange(t.height)
    if not sufficient(y,idx,idx,binary):
        return []
    design=Design(t,idx,(*history,*state,*market),set((*market,*state)))
    fit_idx,weights=fitting_indices(y,idx,binary)
    fit=model(design.matrix(t,fit_idx),y[fit_idx],binary,weights)
    return [{"term":term,"weight":float(weight)} for term,weight in sorted(zip(design.columns,fit),
                                                                         key=lambda x:abs(x[1]),reverse=True)
            if term!="intercept"][:16]


def fit_all(out,only=None):
    daily=pl.read_parquet(out/"symbol-days.parquet")
    executions=pl.read_parquet(out/"decision-clocks.parquet")
    target=out/"decision-models.json"
    findings=json.loads(target.read_text(encoding="utf-8")) if only and target.exists() else {}
    opening_path=out/"reported-openings.parquet"
    openings=pl.read_parquet(opening_path) if opening_path.exists() else pl.DataFrame(schema={"ms":pl.Int64,"symbol":pl.String})
    for name,t,y,binary,hist,state in targets(daily,executions,openings):
        if only and name not in only:
            continue
        keep=np.isfinite(y)
        t=t.filter(pl.Series(keep))
        y=y[keep]
        positives=int(y.sum()) if binary else None
        eligible=t.height>=35 and (not binary or min(positives,t.height-positives)>=8)
        split=validate(t,y,binary,hist,state,name) if eligible else {}
        findings[name]={"n":t.height,"symbols":t["symbol"].n_unique(),
                        "days":int(t.select((pl.col("ms")//DAY).n_unique()).item()),"positives":positives,
                        "base":float(y.mean()) if binary and len(y) else None,"models":split,
                        "terms":interpret(t,y,binary,hist,state,name) if eligible else [],
                        "rates":rates(t,y,name) if binary and eligible else [],
                        "surfaces":list(surface(t,y,name,binary)) if eligible else [],
                        "quantity":np.expm1(np.quantile(y,[.1,.5,.9])).tolist() if not binary and len(y) else None}
        target.write_text(json.dumps(findings,indent=2,allow_nan=False),encoding="utf-8")
        print(name,t.height,positives,flush=True)
