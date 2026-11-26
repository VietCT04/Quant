import { useState } from 'react'
import { CartesianGrid, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts'
import { Activity, ArrowUpRight, FileDown, FlaskConical } from 'lucide-react'
import { useResource, type Detail, type Hypothesis, type Trial } from './api'
import { Badge, Empty, ErrorBox, Json, Loading, num, pct } from './components'

export type Operation = (operation: string, ids: string[], values?: object) => void

function record(value: unknown): Record<string, unknown> {
  return value && typeof value === 'object' && !Array.isArray(value) ? value as Record<string, unknown> : {}
}

function number(value: unknown) { return typeof value === 'number' ? value : undefined }

function nativeCoverage(value: Record<string, unknown>) {
  if (!Array.isArray(value.per_symbol)) return undefined
  let usable = 0, expected = 0
  for (const symbol of value.per_symbol) {
    const states = record(record(symbol).bar_states)
    const observed = (number(states.traded) ?? 0) + (number(states.empty) ?? 0)
    usable += observed
    expected += observed + (number(states.missing) ?? 0) + (number(states.quarantined) ?? 0)
  }
  return expected ? usable / expected : undefined
}

function sameSelection(a: Trial, b: Trial) {
  const dimensions = (trial: Trial) => {
    const spec = trial.specification
    return JSON.stringify([spec.start_ms, spec.end_ms, spec.universe, spec.dataset_fingerprint, spec.evaluation])
  }
  return a.kind === b.kind && dimensions(a) === dimensions(b)
}

function Equity({ trials }: { trials: Trial[] }) {
  const rows = new Map<number, Record<string, number>>()
  const colors = ['#72e0b6', '#8fa8ff']
  trials.forEach((trial, index) => {
    const equity = trial.outcome?.metrics?.daily_marked_equity
    if (!Array.isArray(equity)) return
    const { start_ms: start, end_ms: end } = trial.specification
    const wallet = record(trial.outcome?.metrics.settings).initial_wallet
    if (typeof wallet === 'number') {
      const initial = rows.get(start) ?? { date: start }
      initial[`candidate${index + 1}`] = wallet
      rows.set(start, initial)
    }
    for (const [day, value] of equity as [number, number][]) {
      const date = Math.min(day + 86_400_000, end)
      const row = rows.get(date) ?? { date }
      row[`candidate${index + 1}`] = value
      rows.set(date, row)
    }
  })
  const data = [...rows.values()].sort((a, b) => a.date - b.date)
  if (!data.length) return <Empty title="Marked equity unavailable">The selected trial has no recorded curve.</Empty>
  const label = (date: number) => new Date(date).toISOString().slice(5, data.at(-1)!.date - data[0].date < 86_400_000 ? 16 : 10).replace('T', ' ')
  return <div className="chart"><ResponsiveContainer width="100%" height={240}>
    <LineChart data={data} margin={{ top: 10, left: 0, right: 24, bottom: 2 }}>
      <CartesianGrid vertical={false} stroke="#282d35" strokeDasharray="3 4" />
      <XAxis dataKey="date" tickFormatter={label} tick={{ fill: '#a1a8b3', fontSize: 11 }} />
      <YAxis width={74} domain={['auto', 'auto']} tickFormatter={value => num(value)} tick={{ fill: '#a1a8b3', fontSize: 11 }} />
      <Tooltip labelFormatter={value => typeof value === 'number' ? label(value) : ''} contentStyle={{ background: '#191c22', border: '1px solid #323741' }} />
      {trials.map((trial, index) => <Line key={trial.id} name={`Candidate ${index + 1}`} dataKey={`candidate${index + 1}`} isAnimationActive={false} stroke={colors[index]} strokeWidth={2} dot={data.length === 1} />)}
    </LineChart>
  </ResponsiveContainer></div>
}

function Comparison({ trials, screen }: { trials: Trial[]; screen: boolean }) {
  const values = trials.map(trial => trial.outcome?.metrics ?? {})
  const stats = screen
    ? [
      ['Pearson / rank IC', ...values.map(v => num(v.ic ?? v.mean_ic ?? record(v.pearson).mean, 5))],
      ['Coverage', ...values.map(v => pct(v.coverage ?? v.score_coverage))],
      ['Score turnover', ...values.map(v => num(v.normalized_score_turnover, 5))],
    ] : [
      ['Net P&L · USDT', ...values.map(v => num(v.net_pnl, 2))],
      ['Gross P&L · USDT', ...values.map(v => num(v.gross_pnl_before_execution_cost_fees_and_funding, 2))],
      ['Net return', ...values.map(v => pct(v.total_return))],
      ['Sharpe', ...values.map(v => num(v.sharpe, 3))],
      ['Maximum drawdown', ...values.map(v => pct(v.maximum_drawdown))],
      ['Turnover · USDT', ...values.map(v => num(record(v.end).turnover, 2))],
      ['Post-listing bar coverage', ...values.map(v => pct(nativeCoverage(v)))],
      ['Modeled fees · USDT', ...values.map(v => num(record(v.execution_costs).fees, 2))],
      ['Spread / slippage / impact · USDT', ...values.map(v => num(record(v.execution_costs).spread_slippage_impact, 2))],
      ['Funding cash flow · USDT', ...values.map(v => num(record(v.execution_costs).funding, 2))],
    ]
  return <div className="table-scroll"><table className="dense-table compare"><thead><tr><th>Metric</th>{trials.map((trial, index) => <th key={trial.id}>Candidate {index + 1}</th>)}</tr></thead><tbody>
    {stats.map(([label, ...data]) => <tr key={label}><th scope="row">{label}</th>{data.map((v, i) => <td key={trials[i].id} className="mono">{v}</td>)}</tr>)}
  </tbody></table></div>
}

export function Results({ rows, selected, pick, operate }: { rows: Hypothesis[]; selected: string; pick: (id: string) => void; operate: Operation }) {
  const [mode, setMode] = useState<'screen' | 'native'>('screen')
  const [primary, setPrimary] = useState('')
  const [secondary, setSecondary] = useState('')
  const id = selected || rows[0]?.id || ''
  const summary = rows.find(row => row.id === id)
  const result = useResource<Detail>(id ? `/hypotheses/${id}/results` : null, `${summary?.revision}/${summary?.state}`)
  const trials = result?.value?.experiments ?? []
  const sameMode = trials.filter(trial => mode === 'screen' ? trial.kind === 'screen' : trial.kind !== 'screen')
  const completed = sameMode.filter(trial => trial.status === 'completed')
  const first = completed.find(trial => trial.id === primary) ?? completed[0]
  const compatible = first ? completed.filter(trial => trial.id !== first.id && sameSelection(first, trial)) : []
  const second = compatible.find(trial => trial.id === secondary)
  const picks = first ? [first, ...(second ? [second] : [])] : []
  if (!rows.length) return <section className="panel"><Empty title="No research results">Register and execute a development hypothesis to view its evidence.</Empty></section>
  return <>
    <section className="panel results-controls"><label>Hypothesis<select aria-label="Hypothesis" value={id} onChange={event => { pick(event.target.value); setPrimary(''); setSecondary('') }}>{rows.map(row => <option key={row.id} value={row.id}>{row.id} · {row.name}</option>)}</select></label>
      <div className="segments" aria-label="Results type"><button aria-pressed={mode === 'screen'} onClick={() => setMode('screen')}><FlaskConical size={15} /> Screening</button><button aria-pressed={mode === 'native'} onClick={() => setMode('native')}><Activity size={15} /> Native simulation</button></div>
      {summary && <div className="result-verdict"><span>Research verdict</span>{summary.verdict ? <Badge value={summary.verdict} /> : <strong>Unassigned</strong>}</div>}
    </section>
    {result?.error ? <ErrorBox>{result.error}</ErrorBox> : !result ? <Loading /> : !picks.length ?
      <section className="panel"><Empty title={mode === 'screen' ? 'No completed screening trials' : 'No completed native trials'}>{sameMode.length ? `${sameMode.length} trials registered. See the hypothesis for execution history.` : 'Select a hypothesis with this evaluation type.'}</Empty></section> : <>
      <section className="panel"><header className="panel-heading"><div><h2>{mode === 'screen' ? 'Descriptive diagnostics' : 'Net portfolio results'}</h2><p>{completed.length} completed of {sameMode.length} registered trials</p></div>{mode === 'native' && <button onClick={() => operate('audit', [id], { experiment_id: first.id })}><ArrowUpRight size={15} /> Audit replay</button>}</header>
        <div className="comparison-selectors">
          <label>Candidate 1<select aria-label="Candidate 1" value={first.id} onChange={event => { setPrimary(event.target.value); setSecondary('') }}>{completed.map(trial => <option key={trial.id} value={trial.id}>{trial.id.slice(0, 8)} · {paramName(trial)}</option>)}</select></label>
          <label>Compare with<select aria-label="Compare with" value={second?.id ?? ''} onChange={event => setSecondary(event.target.value)}><option value="">{compatible.length ? 'Select comparable candidate' : 'No comparable candidates'}</option>{compatible.map(trial => <option key={trial.id} value={trial.id}>{trial.id.slice(0, 8)} · {paramName(trial)}</option>)}</select></label>
        </div>
        <Comparison screen={mode === 'screen'} trials={picks} />
      </section>
      {mode === 'native' && <section className="panel"><header className="panel-heading"><div><h2>Marked equity</h2><p>Last UTC daily mark · USDT</p></div></header><Equity trials={picks} /></section>}
      <div className="evidence-grid">{picks.map((trial, index) => {
        const metrics = trial.outcome!.metrics
        const robustness = metrics.monthly_marked_pnl ?? metrics.monthly ?? metrics.monthly_ic
        return <section className="panel" key={trial.id}><header className="panel-heading"><div><h2>Candidate {index + 1} evidence</h2><p className="mono">{trial.id}</p></div></header>
          <div className="evidence-content">
            {robustness != null && <details open><summary>Recorded subperiod results</summary><Json value={robustness} /></details>}
            {mode === 'native' && <details open><summary>Cost and risk assumptions</summary><Json value={{ ...trial.specification.costs, ...record(trial.specification.parameters.settings) }} /></details>}
            <details><summary>Full metrics and warnings</summary><Json value={{ metrics, warnings: trial.outcome?.warnings }} /></details>
            {trial.artifact_urls.map((url, i) => <a href={url} key={url} download className="text-button"><FileDown size={15} /> Download artifact {i + 1}</a>)}
          </div>
        </section>
      })}</div>
    </>}
  </>
}

function paramName(trial: Trial) {
  const p = trial.specification.parameters
  return p.fast != null ? `EMA ${p.fast}/${p.slow}${record(p.settings).fee_bps != null ? `, fee ${record(p.settings).fee_bps} bps` : ''}` : 'Causal expression pair'
}
