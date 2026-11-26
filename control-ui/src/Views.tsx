import { useState } from 'react'
import { ArrowRight, BookOpen, Cpu, Database, HardDrive, MemoryStick, Radio } from 'lucide-react'
import { useResource, type Capability, type Detail, type Hypothesis, type Service } from './api'
import { Badge, between, duration, Empty, ErrorBox, gib, Json, Loading, num, pct, Progress } from './components'
import { explorerLink } from './explorerTypes'
import { Worker } from './Overview'
import type { Operation } from './Results'

export function Details({ item, operate, showResults }: { item: Hypothesis; operate: Operation; showResults: () => void }) {
  const resource = useResource<Detail>(`/hypotheses/${item.id}/results`, `${item.state}/${item.revision}`)
  if (!resource) return <Loading />
  if (resource.error) return <ErrorBox>{resource.error}</ErrorBox>
  const value = resource.value!
  const trials = value.experiments ?? []
  const native = trials.find(trial => trial.kind !== 'screen' && trial.status === 'completed')
  const active = item.state === 'running' || item.state === 'queued' || (item.state === 'blocked' && item.finished < item.total)
  return <>
    <div className="detail-meta"><Badge value={item.state} /><span className="mono">{item.id}</span><span>{item.family}</span><span className="stage">{item.stage}</span></div>
    <div className="detail-timing"><div><span>Queued time{item.first_started ? ' before first attempt' : ''}</span><strong>{between(item.created, item.first_started || (item.finished === item.total ? item.last_result : null))}</strong></div>
      <div><span>Execution time</span><strong>{item.first_started ? duration(item.execution_seconds + (item.active_attempt ? Math.max(0, Date.now() - Date.parse(item.timing_sampled_at)) / 1000 : 0)) : "—"}</strong></div>
      <div><span>Total elapsed</span><strong>{between(item.created, item.finished === item.total ? item.last_result : null)}</strong></div></div>
    {item.cancelling > 0 && <p className="muted">{item.cancelling} running trials are awaiting cancellation.</p>}
    <div className="detail-actions"><button onClick={showResults}>View results <ArrowRight size={14} /></button>{native && <a className="text-button" href={explorerLink(item.id, native.id)}>Backtest Explorer <ArrowRight size={14} /></a>}<button onClick={() => operate('priority', [item.id])}>Priority {item.priority}</button>
      {active && <button className="danger" onClick={() => operate('cancel', [item.id])}>Cancel work</button>}
      {(['failed', 'cancelled'].includes(item.state) || (item.state === 'blocked' && item.finished === item.total)) && <button onClick={() => operate('retry', [item.id])}>Retry failed trials</button>}
      {item.unreviewed && <button onClick={() => operate('acknowledge', [item.id], { revision: item.revision })}>Acknowledge</button>}
      {item.finished === item.total && <button onClick={() => operate('verdict', [item.id])}>Record verdict</button>}</div>
    {item.errors.map((error, index) => <ErrorBox key={index}>{error.type}{error.name ? ` · ${error.name}` : ''}{error.message ? ` · ${error.message}` : ''}</ErrorBox>)}
    <div className="detail-section"><h3>Research specification</h3><dl className="spec-list">
      <dt>Mechanism</dt><dd>{value.definition.mechanism}</dd>
      <dt>Mathematical definition</dt><dd className="formula">{value.definition.definition}</dd>
      <dt>Prediction</dt><dd>{value.definition.prediction}</dd>
      <dt>Required capabilities</dt><dd>{value.definition.capabilities?.map(cap => cap.name).join(', ') || 'None specified'}</dd>
      <dt>Dependencies</dt><dd>{item.dependencies.join(', ') || 'None'}</dd>
      <dt>Research verdict</dt><dd>{item.verdict ? <><Badge value={item.verdict} /> <span>{item.verdict_note}</span></> : 'Unassigned'}</dd>
    </dl>
    {value.definition.parameters && <details><summary>Hypothesis parameters</summary><Json value={value.definition.parameters} /></details>}
    <details><summary>Frozen development protocol</summary><Json value={value.definition.protocol} /></details>
    {value.verdict_history?.length > 0 && <details><summary>Verdict history</summary><Json value={value.verdict_history} /></details>}
    </div>
    <div className="detail-section"><div className="detail-section-head"><h3>Experiment history</h3><Progress item={item} /></div>
      {trials.map((trial, i) => <details className="trial" key={trial.id}><summary><span className="mono">{i + 1}. {trial.id.slice(0, 10)}</span> <span>{trial.kind}</span><Badge value={trial.status} /></summary>
        <Json value={{ ...trial.specification, attempts: trial.attempts, failure: trial.outcome?.failure }} />
        {trial.kind !== 'screen' && trial.status === 'completed' && <a href={explorerLink(item.id, trial.id)}>Explore candidate</a>}
        {trial.artifact_urls.map((url, index) => <a key={url} href={url} download>Download artifact {index + 1}</a>)}
      </details>)}
    </div>
  </>
}

export function Library({ items, open }: { items: Hypothesis[]; open: (id: string) => void }) {
  const [search, setSearch] = useState('')
  const visible = items.filter(item => [item.name, item.family, item.id].some(value => value.toLowerCase().includes(search.toLowerCase())))
  return <section className="panel"><header className="panel-heading"><div><h2>Registered hypotheses</h2><p>{visible.length} records</p></div><input aria-label="Search library" placeholder="Search library…" value={search} onChange={event => setSearch(event.target.value)} /></header>
    {visible.length ? <div className="compact-list">{visible.map(item => <button className="compact-row" key={item.id} onClick={() => open(item.id)}><span className="item-icon"><BookOpen size={16} /></span><span className="item-label"><strong>{item.name}</strong><small>{item.id} · {item.family} · {item.stage}</small></span><Badge value={item.state} />{item.verdict && <Badge value={item.verdict} />}<ArrowRight size={15} /></button>)}</div>
      : <Empty title="No hypotheses found">{items.length ? 'Change the library search.' : 'Registered research definitions will appear here.'}</Empty>}
  </section>
}

export function Requests({ requests, items, submit, open }: { requests: Capability[]; items: Hypothesis[]; submit: () => void; open: (id: string) => void }) {
  return <section className="panel"><header className="panel-heading"><div><h2>Capability requests</h2><p>{requests.length} registered</p></div><button onClick={submit}>New request</button></header>
    {!requests.length && <Empty title="No missing capabilities recorded">Requests linked to blocked work appear here.</Empty>}
    {requests.map(item => {
      const spec = item.request
      const blocked = items.filter(h => h.errors.some(error => error.request_id === item.id))
      return <details className="capability" key={item.id}><summary><span><strong className="mono">{spec.module}.{spec.name}</strong><small>{spec.purpose}</small></span><Badge value={item.status} /></summary>
        <dl className="spec-list"><dt>Mathematical specification</dt><dd>{spec.specification || 'Unspecified'}</dd><dt>Required inputs</dt><dd>{spec.required_inputs.join(', ') || 'Unspecified'}</dd><dt>Output units</dt><dd>{spec.output_units || 'Unspecified'}</dd>
          <dt>Causality and streaming</dt><dd>{spec.causal ? 'Causal' : 'Research only'} · {spec.streaming_required ? 'Streaming required' : 'Streaming optional'}</dd><dt>Acceptance criteria</dt><dd>{spec.acceptance_criteria.join(' · ')}</dd>
          <dt>Blocked hypotheses</dt><dd>{blocked.length ? blocked.map(h => <button className="text-button mono" key={h.id} onClick={() => open(h.id)}>{h.id}</button>) : 'None linked'}</dd>
          <dt>Review</dt><dd>{item.review ? `${item.review.reviewer} · ${item.review.findings}` : 'Pending review'}</dd></dl>
        <details><summary>Parameters and review history</summary><Json value={{ parameters: spec.parameters, missing_data: spec.missing_data, warmup: spec.warmup, review_history: item.review_history }} /></details>
      </details>
    })}
  </section>
}

export function System({ service, connected, configure }: { service: Service; connected: boolean; configure: () => void }) {
  const resources = service.resources
  const coverage = service.coverage
  const entries = [
    { label: 'CPU utilization', icon: Cpu, value: pct(resources.cpu_percent / 100), description: `${service.hardware.physical} physical · ${service.hardware.usable_cpus} usable CPUs` },
    { label: 'System memory', icon: MemoryStick, value: gib(resources.ram_used_bytes), description: `${gib(resources.ram_total_bytes)} total · ${gib(resources.process_rss_bytes)} service RSS` },
    { label: 'Disk available', icon: HardDrive, value: gib(resources.disk_free_bytes), description: `${gib(resources.disk_total_bytes)} total volume capacity` },
    { label: 'Prepared cache', icon: Database, value: gib(resources.cache_bytes), description: `${service.budgets.disk_budget_gib} GiB budget` },
  ]
  return <>
    <div className="stats-grid system-grid">{entries.map(entry => <div className="stat" key={entry.label}><div><span>{entry.label}</span><span className="stat-icon"><entry.icon size={16} /></span></div><strong>{entry.value}</strong><small>{entry.description}</small></div>)}</div>
    <div className="overview-grid">
      <section className="panel"><header className="panel-heading"><div><h2>Dispatcher and budgets</h2><p>Applied to the next wave</p></div><button onClick={configure}>Configure budgets</button></header>
        <Worker service={service} /><dl className="metric-strip"><div><dt>CPU limit</dt><dd>{service.budgets.cpu_limit}</dd></div><div><dt>RAM reservation</dt><dd>{service.budgets.memory_budget_gib} GiB</dd></div><div><dt>Disk reservation</dt><dd>{service.budgets.disk_budget_gib} GiB</dd></div></dl>
        {service.worker_error && <ErrorBox>{service.worker_error}</ErrorBox>}
      </section>
      <section className="panel"><header className="panel-heading"><div><h2>Market data and connection</h2><p>Development workspace</p></div><Radio size={16} /></header>
        <dl className="spec-list system-spec"><dt>Event stream</dt><dd><Badge value={connected ? 'connected' : 'disconnected'} /></dd>
          <dt>Service uptime</dt><dd>{num(service.resources.uptime_seconds)} seconds</dd><dt>Universe</dt><dd>{coverage?.symbols ?? '—'} symbols · Liquid 50</dd><dt>Bar interval</dt><dd>{coverage?.interval_ms ? `${coverage.interval_ms / 1000}s` : '—'}</dd>
          <dt>Audited coverage</dt><dd>{pct(coverage?.planned_coverage)}</dd><dt>Development range</dt><dd>2024-11-25 → 2026-08-25 UTC</dd><dt>Source</dt><dd className="source-path">{coverage?.source ?? '—'}</dd>
        </dl>{coverage?.error && <ErrorBox>{coverage.error}</ErrorBox>}
      </section>
    </div>
  </>
}
