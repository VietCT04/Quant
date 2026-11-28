"""Export interpretable conditional surfaces and research tables for manual review."""
import argparse
import csv
import json
from pathlib import Path

PAGE="""<!DOCTYPE html><meta charset="utf-8"><title>Inventory decisions</title>
<style>
:root{color-scheme:dark;background:#101922;color:#e1e9ee;font:14px/1.5 system-ui}
body{margin:22px auto;padding:0 20px;max-width:1280px}h1{margin-bottom:3px}h2{font-size:17px;margin-top:22px}
p,small{color:#adc0cc}select{margin:5px 18px 5px 4px;padding:5px;background:#1d3042;border:1px solid #536b7d}
table{border-collapse:collapse;width:100%;font-variant-numeric:tabular-nums} td,th{border-bottom:1px solid #2c3c4e;padding:5px;text-align:right}
td:first-child,th:first-child{text-align:left}.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(270px,1fr));gap:12px}
.card{border:1px solid #2c3c4e;border-radius:5px;padding:7px}.card td{text-align:center}.card th{text-align:center}
a{color:#8ad2f0}header{margin:12px 0} .rate{font-size:16px} .warning{color:#e9bc81}
</style>
<h1>Inventory decisions</h1><p>Binance lead portfolio 5140488422016171264. Five-second marks use completed observations.</p>
<header>
<label>Held side <select id="side"><option value="long">Long</option><option value="short">Short</option></select></label>
<label>Decision <select id="action"><option>add</option><option>reduce</option><option>reverse</option></select></label>
<label>Scope <select id="scope"><option value="events">Executed orders</option><option value="timing">Verified minute opportunities</option></select></label>
<label>Dates <select id="dates"><option value="all">All</option><option value="late">Later dates</option></select></label></header>
<output id="summary"></output>
<section id="surfaces"><h2>Joint action probability at executions · all dates</h2><p>Rows and columns are low, medium and high bands. Hover any cell to see all three action counts.</p>
<div id="grids" class="grid"></div></section>
<h2>Conditional action distributions</h2><div id="rates"></div>
<h2>Replay verified lifecycles</h2><div id="links"></div>
<script type="application/json" id="data">__DATA__</script>
<script>
const DATA=JSON.parse(document.querySelector('#data').textContent)
const f=x=>document.querySelector('#'+x)
const n=x=>Number(x).toLocaleString('en-US')
const p=x=>x==null?'—':(100*x).toFixed(2)+'%'
const names={from_previous:'Move since last fill',exposure_usdt:'Marked inventory',
 from_cost:'Move from weighted entry',peak_fraction:'Fraction of peak quantity',
 gap_s:'Time since last fill',ret_5m:'Five-minute return',r24:'24-hour return',
 unrealized_usdt:'Unrealized P&L',prior_displacement_vol:'Move since fill / volatility',
 qty_change_15m:'Recent inventory change',trim_streak:'Consecutive reductions',
 clip_cover:'Last trim / remaining quantity',from_add:'Move since last add',
 from_reduce:'Move since last reduction'}
const key=x=>names[x]||x.replaceAll('_',' ')
const units=(k,v)=>k.startsWith('from_')||k.startsWith('ret_')||k==='r24'?(10000*v).toFixed(0)+' bp':
 k==='gap_s'?v.toFixed(0)+' s':k==='unrealized_usdt'?'$'+v.toFixed(2):
 k==='exposure_usdt'?'$'+v.toFixed(0):k==='peak_fraction'?p(v):v.toPrecision(3)
const actions=['add','reduce','reverse']
function draw(){
 const side=f('side').value,a=f('action').value,scope=f('scope').value,dates=f('dates').value,d=DATA[side]
 const rates=scope==='events'?d.signals:d.timing.signals
 const totals=scope==='events'?d.baseline:d.timing.counts
 const size=Object.values(totals).reduce((x,y)=>x+y,0)
 f('summary').textContent=n(size)+' '+(scope==='events'?'eligible executions':'verified minutes')+
  ' · '+String(totals[a])+' '+a+' · all-dates overall '+p(totals[a]/size)
 f('surfaces').hidden=scope!=='events'||dates!=='all'
 const rows=rates.map(r=>[r,r[dates]]).filter(([_,x])=>x)
  .sort((x,y)=>Math.abs(y[1].p[a]-y[1].baseline[a])-Math.abs(x[1].p[a]-x[1].baseline[a]))
 f('rates').innerHTML='<table><thead><tr><th>Condition</th><th>Signals / eligible</th><th>ADD</th><th>REDUCE</th><th>REVERSE</th>'+
  (scope==='timing'?'<th>HOLD</th>':'')+'<th>'+a+' baseline</th><th>Change</th><th>Lift</th><th>90% symbol interval</th></tr></thead><tbody>'+
  rows.map(([r,x])=>'<tr><td>'+r.signal+'</td><td>'+n(x.n)+' / '+n(x.eligible)+'</td>'+
   (scope==='timing'?[...actions,'hold']:actions).map(c=>'<td>'+n(x.counts[c])+' · '+p(x.p[c])+'</td>').join('')+
   '<td>'+p(x.baseline[a])+'</td><td>'+((x.p[a]-x.baseline[a])*100).toFixed(2)+' pp</td><td>'+(x.lift[a]==null?'—':x.lift[a].toFixed(2)+'×')+'</td><td>'+
   (x.symbol_ci90?x.symbol_ci90[a].map(p).join(' to '):'—')+'</td></tr>').join('')+'</tbody></table>'
 f('grids').innerHTML=d.surfaces.map(s=>{
   let t='<section class="card"><strong>'+key(s.x)+' × '+key(s.y)+'</strong><p>n='+n(s.n)+'</p><table><tr><th></th><th>Low</th><th>Mid</th><th>High</th></tr>'
   for(let j=2;j>=0;j--){
    t+='<tr><th>'+['Low','Mid','High'][j]+'</th>'
    for(let i=0;i<3;i++){
     const c=s.cells.find(k=>k.x===i&&k.y===j),v=c.n?c.counts[a]/c.n:0
     t+='<td style="background:rgba(58,172,152,'+(c.n ? .12+.63*v : 0)+')" title="'+actions.map(k=>k+' '+c.counts[k]).join(' · ')+'"><span class="rate">'+
       (c.n?p(v):'—')+'</span><br><small>'+c.counts[a]+'/'+c.n+'</small></td>'
    }
    t+='</tr>'
   }
   return t+'</table><small>x cuts '+s.cuts[0].map(x=>units(s.x,x)).join(' / ')+' · y cuts '+s.cuts[1].map(y=>units(s.y,y)).join(' / ')+'</small></section>'
 }).join('')
 f('links').innerHTML=Object.entries(d.examples).map(([label,x])=>'<p><a href="'+x.url+'" target="_blank" rel="noopener">'+x.symbol+' · '+label+'</a></p>').join('')
}
const params=new URLSearchParams(location.search)
for(const id of ['side','action','scope','dates']){
 if([...f(id).options].some(o=>o.value===params.get(id)))f(id).value=params.get(id)
 f(id).onchange=draw
}
draw()
</script>"""


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("out",type=Path)
    folder=p.parse_args().out
    data=json.loads((folder/"findings.json").read_text(encoding="utf-8"))
    payload=json.dumps(data,ensure_ascii=False).replace("<","\\u003c")
    (folder/"action-atlas.html").write_text(PAGE.replace("__DATA__",payload),encoding="utf-8")
    with (folder/"conditional-actions.csv").open("w",encoding="utf-8",newline="") as f:
        cols=("side","scope","period","condition","action","signal_n","eligible_n","count","rate","baseline","lift","symbol_ci90")
        writer=csv.DictWriter(f,fieldnames=cols)
        writer.writeheader()
        for side in ("long","short"):
            d=data[side]
            for scope,groups in (("executions",d["signals"]),("timing",d["timing"]["signals"])):
                for r in groups:
                    for period in ("all","late"):
                        x=r.get(period)
                        if not x: continue
                        for action,count in x["counts"].items():
                            writer.writerow(dict(side=side,scope=scope,period=period,condition=r["signal"],action=action,
                                signal_n=x["n"],eligible_n=x["eligible"],count=count,rate=x["p"][action],
                                baseline=x["baseline"][action],lift=x["lift"][action],symbol_ci90=x.get("symbol_ci90",{}).get(action)))


if __name__=="__main__":
    main()
