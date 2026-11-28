"""Export a standalone decision evidence browser and a tidy probability table."""

import argparse
import csv
import json
from pathlib import Path
from html import escape
import polars as pl


PAGE = """<!doctype html>
<meta charset="utf-8">
<title>Lead trader decision evidence</title>
<style>
:root{color-scheme:dark;font:14px/1.5 system-ui,sans-serif;background:#101821;color:#dce6ed}
body{margin:24px auto;padding:0 20px;max-width:1220px} h1{font-size:24px;margin-bottom:4px}h2{font-size:17px}
p{color:#aebfce;margin:5px 0}select{background:#1f3041;color:#f0f6fc;border:1px solid #4d6377;padding:6px;margin:8px 16px 8px 4px}
section{margin:22px 0}table{border-collapse:collapse;width:100%;font-variant-numeric:tabular-nums}
th,td{padding:5px 8px;border-bottom:1px solid #2d4052;text-align:right}th:first-child,td:first-child{text-align:left}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:12px}
.card{padding:8px;border:1px solid #2d4052;border-radius:6px}.card table td{text-align:center;height:35px}
#summary{display:block;padding:6px 0;color:#b1d2cc}.number{font-size:16px}
small{color:#c1d6df}.posit{color:#84d8c7}.warn{color:#fac284}
</style>
<h1>Lead trader decision evidence</h1>
<p>Portfolio 5140488422016171264. Each decision uses its own risk set.</p>
<label>Decision <select id="target"></select></label><label>Probability scope
<select id="scope"><option value="pooled">All covered observations</option><option value="chronology">Chronological test period</option></select></label>
<output id="summary"></output>
<section><h2>Held-out model performance</h2><div id="models"></div></section>
<section><h2>Conditional decision surfaces</h2><p>Low, middle and high observed feature bands. Each cell shows its sample size.</p><div id="surface" class="grid"></div></section>
<section><h2>Descriptive model terms</h2><p>Largest standardized coefficients from the full sample fit. Correlated predictors can exchange weight.</p><div id="terms"></div></section>
<section><h2>Conditional signals</h2><div id="rates"></div></section>
<script id="data" type="application/json">__DATA__</script>
<script>
const data=JSON.parse(document.querySelector('#data').textContent)
const select=document.querySelector('#target'), scope=document.querySelector('#scope')
const pct=(v,k=2)=>v==null?'—':(100*v).toFixed(k)+'%'
const num=(v,k=3)=>v==null?'—':Number(v).toFixed(k)
const words=s=>s.replaceAll('_',' ')
const comma=x=>Number(x).toLocaleString('en-US')
function cut(v,k){
 if(v==null)return '?'
 if(['exposure','last_order_usdt','leg_usdt','inventory_mark_pnl'].includes(k)){
  const x=k==='inventory_mark_pnl'?Math.sinh(v):k==='leg_usdt'?v:Math.expm1(v)
  return '$'+num(x,2)
 }
 if(['last_gap','position_age'].includes(k))return num(Math.expm1(v),1)+' s'
 if(k.endsWith('_rank'))return pct(v,1)
 if(['distance_to_cost','alt_residual','residual'].includes(k)||k.startsWith('ret_')||/^r[0-9]+$/.test(k))
  return num(v*10000,1)+' bp'
 return Math.abs(v)>0&&Math.abs(v)<.001?Number(v).toExponential(2):num(v)
}
const interval=x=>x?x.map(v=>pct(v)).join(' to '):'—'
function draw(){
 const name=select.value, d=data[name], binary=d.base!==null
 document.querySelector('#summary').textContent=comma(d.n)+' observations · '+d.symbols+' symbols · '+d.days+' days'
   +(binary?' · base '+pct(d.base)+' ('+comma(d.positives)+' decisions)':d.quantity?' · USDT (p10 / p50 / p90) '+d.quantity.map(v=>num(v,2)).join(' / '):'')
 let models=''
 for(const [split,result] of Object.entries(d.models)){
  models+='<p>'+words(split)+': '+(result.models?comma(result.n)+' held-out observations across '+result.symbols+' symbols and '+result.days+' dates':result.state)+'</p>'
  if(!result.models)continue
  const cols=binary?['logloss','ap','auc','brier']:['mae_log','medae_log']
  models+='<table><thead><tr><th>Model</th>'+cols.map(x=>'<th>'+words(x)+'</th>').join('')+'</tr></thead><tbody>'
  for(const [model,metrics] of Object.entries(result.models)) models+='<tr><td>'+words(model)+'</td>'+cols.map(x=>'<td>'+num(metrics[x],4)+'</td>').join('')+'</tr>'
  models+='</tbody></table>'
 }
 document.querySelector('#models').innerHTML=models||'<p class="warn">Insufficient events for model validation.</p>'
 document.querySelector('#terms').innerHTML=d.terms.length?'<p>'+d.terms.slice(0,10).map(x=>words(x.term)+' '+num(x.weight,2)).join(' ? ')+'</p>':'<p>Insufficient observations for model terms.</p>'
 document.querySelector('#surface').innerHTML=d.surfaces.map((v)=>{
  let grid='<div class="card"><b>'+words(v.x)+' × '+words(v.y)+'</b><p>Subset baseline '+(binary?pct(v.baseline):'$'+num(v.baseline,2))+'</p><table><tr><th></th><th>Low</th><th>Middle</th><th>High</th></tr>'
  for(let j=2;j>=0;j--){
   grid+='<tr><th>'+['Low','Mid','High'][j]+'</th>'
   for(let i=0;i<3;i++){
    const c=v.cells.find(z=>z.x===i&&z.y===j)
    const alpha=c.value==null?0:Math.min(.42,.06+.14*Math.abs((c.value-v.baseline)/Math.max(1e-6,v.baseline)))
    grid+='<td style="background:rgba('+(c.value>=v.baseline?'41,178,157':'203,96,91')+','+alpha+')"><span class="number">'+
       (binary?pct(c.value):c.value==null?'—':'$'+num(c.value,2))+'</span><br><small>n='+comma(c.n)+
       (binary?' · actions '+comma(c.actions):'')+'</small></td>'
   }
   grid+='</tr>'
  }
  return grid+'</table><small>x cutoffs '+v.cuts[0].map(x=>num(x)).join(' / ')+' · y cutoffs '+v.cuts[1].map(x=>num(x)).join(' / ')+'</small></div>'
 }).join('')||'<p>No supported two-feature cells for this decision.</p>'
 const rows=d.rates.map(r=>[r,r[scope.value]]).filter(([_,x])=>x).sort((a,b)=>Math.abs(b[1].difference)-Math.abs(a[1].difference)).slice(0,45)
 document.querySelector('#rates').innerHTML=rows.length?'<table><thead><tr><th>Signal band</th><th>Threshold</th><th>Actions / signal n</th><th>P(action | signal)</th><th>Baseline</th><th>Lift</th><th>P(signal | action)</th><th>90% symbol CI</th></tr></thead><tbody>'+
  rows.map(([r,x])=>'<tr><td>'+words(r.signal)+' · '+r.band+'</td><td>'+[].concat(r.threshold).map((v,i)=>cut(v,r.signal.split(' + ')[i])).join(' / ')+'</td><td>'+comma(x.signal_actions)+' / '+comma(x.signal_n)+'</td><td>'+pct(x.action_given_signal)+'</td><td>'+pct(x.baseline)+'</td><td>'+num(x.lift,2)+'×</td><td>'+pct(x.signal_given_action)+'</td><td>'+interval(x.symbol_rate_ci90)+'</td></tr>').join('')+'</tbody></table>':'<p>No supported rate bands for this decision.</p>'
}
for(const name of Object.keys(data)){
 const opt=document.createElement('option'); opt.value=name;opt.textContent=words(name);select.append(opt)
}
select.onchange=draw; scope.onchange=draw; draw()
</script>
"""


def render(out):
    summary=json.loads((out/"decision-models.json").read_text(encoding="utf-8"))
    public={name:{**item,"rates":item["rates"][:45]} for name,item in summary.items()}
    payload=json.dumps(public,ensure_ascii=False).replace("<","\\u003c")
    (out/"decision-atlas.html").write_text(PAGE.replace("__DATA__",payload),encoding="utf-8")
    rows=[]
    for target,item in summary.items():
        for condition in item["rates"]:
            for scope in ("pooled","chronology"):
                v=condition[scope]
                if v:
                    rows.append(dict(target=target,signal=condition["signal"],band=condition["band"],
                                     threshold=json.dumps(condition["threshold"]),scope=scope,**v))
    if rows:
        with (out/"conditional-probabilities.csv").open("w",encoding="utf-8",newline="") as handle:
            writer=csv.DictWriter(handle,fieldnames=list(dict.fromkeys(k for r in rows for k in r)))
            writer.writeheader()
            writer.writerows(rows)




def contrasting_minutes(out):
    tape=pl.read_parquet(out/"decision-clocks.parquet").filter(
        (pl.col("risk")=="hazard")&pl.col("exposure").is_finite()&pl.col("last_gap").is_finite())
    records=[]
    for sign in (1,-1):
        lane=tape.filter(pl.col("direction")==sign)
        for action in ("add","reduce","reverse"):
            early=lane.filter((pl.col("action")==action)&(pl.col("last_gap")<=5))
            for case in early.sort("ms").head(50).iter_rows(named=True):
                peers=lane.filter((pl.col("symbol")==case["symbol"])&
                    (pl.col("life_id")==case["life_id"])&(pl.col("action")=="hold")&
                    ((pl.col("last_gap")-case["last_gap"]).abs()<=.5)&
                    ((pl.col("exposure")-case["exposure"]).abs()<=.5))
                if peers.height:
                    peer=peers.with_columns((pl.col("ms")-case["ms"]).abs().alias("age")).sort("age").row(0,named=True)
                    for kind,item in (("action",case),("same lifecycle hold",peer)):
                        records.append({"comparison":action,"example":kind,**{k:item[k] for k in
                            ("symbol","life_id","direction","ms","last_gap","exposure","position_age","prior_orders_15m","ret_1m","r24","alt_residual")}})
                    break
    if records:
        pl.from_dicts(records,infer_schema_length=None).write_csv(out/"contrasting-minutes.csv")

def trajectory_examples(out,source):
    lives=pl.read_parquet(source/"complete-trajectories.parquet")
    legs=pl.read_parquet(source/"confirmed-legs.parquet")
    cases=lives.sort("pnl")
    chosen=[cases.row(0,named=True),cases.row(-1,named=True)]
    marks=legs.filter(pl.col("life_id").is_in([r["life_id"] for r in chosen])).sort("life_id","ms")
    marks=marks.with_columns((pl.col("after")*pl.col("price")).abs().alias("exposure_abs"))
    marks.write_csv(out/"winning-losing-trajectories.csv")
    peak=float(marks["exposure_abs"].max())
    width,height=760,328
    x1,x2,y1,y2=64,708,67,257
    svg=[f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}" width="{width}" height="{height}">',
         '<rect width="100%" height="100%" fill="#f6fafc"/>',
         '<g fill="#1a303e" font-family="Segoe UI, Arial, sans-serif">',
         '<text x="24" y="29" font-size="20" font-weight="600">Large exposure growth in two complete paths</text>',
         '<text x="24" y="50" font-size="12">Absolute USDT exposure at each booked execution. Time normalized within each lifecycle.</text>']
    for k in range(5):
        y=y2-(y2-y1)*k/4
        svg += [f'<text x="{x1-8}" y="{y+4}" font-size="11" text-anchor="end">{peak*k/4:,.0f}</text>',
                f'<path d="M{x1} {y} H{x2}" fill="none" stroke="#bfccd1"/>']
    for j,(case,color) in enumerate(zip(chosen,("#a94c46","#038878"))):
        r=marks.filter(pl.col("life_id")==case["life_id"])
        elapsed=r["ms"].to_numpy()-r["ms"].min()
        xs=x1+(x2-x1)*elapsed/max(elapsed.max(),1)
        ys=y2-(y2-y1)*r["exposure_abs"].to_numpy()/peak
        path="M"+f"{xs[0]:.1f} {ys[0]:.1f}"+''.join(f" H{x:.1f} V{y:.1f}" for x,y in zip(xs[1:],ys[1:]))
        svg += [f'<path d="{path}" fill="none" stroke="{color}" stroke-width="1.6"/>',
                f'<text x="{x1}" y="{289+j*18}" fill="{color}" font-size="12">{escape(case["symbol"])} '
                f'{case["side"]}, reported {case["pnl"]:+,.0f} USDT, {case["additions"]} adds</text>']
    svg.append('</g></svg>')
    (out/"exposure-trajectories.svg").write_text("\n".join(svg),encoding="utf-8")


def cash_bands(out):
    import numpy as np
    tape=pl.read_parquet(out/"decision-clocks.parquet").filter(pl.col("risk")=="size")
    contrast=(("last clip below 20","last_order_usdt",20,False),("last clip at least 100","last_order_usdt",100,True),
              ("inventory below 75","exposure",75,False),("inventory at least 250","exposure",250,True))
    rows=[]
    for action in ("open","add","reduce","reverse_open"):
        sub=tape.filter(pl.col("action")==action)
        for label,key,value,high in contrast:
            field=pl.col(key)
            x=sub.filter(field.is_finite()&((field>=np.log1p(value)) if high else (field<np.log1p(value))))
            if x.height:
                sizes=x["leg_usdt"]
                baseline=float((sub["leg_usdt"]>=50).mean())
                rows.append(dict(action=action,band=label,n=x.height,symbols=x["symbol"].n_unique(),
                                 median_usdt=float(sizes.median()),n_at_least_50=int((sizes>=50).sum()),
                                 n_at_least_500=int((sizes>=500).sum()),baseline_50=baseline,
                                 rate_50=float((sizes>=50).mean())))
    pl.from_dicts(rows).write_csv(out/"size-bands.csv")

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("out",type=Path)
    p.add_argument("--source",type=Path,default=Path("data/research/lead-trader/behavior-2026-11-28"))
    args=p.parse_args()
    render(args.out)
    trajectory_examples(args.out,args.source)
    contrasting_minutes(args.out)
    cash_bands(args.out)


if __name__=="__main__":
    main()
