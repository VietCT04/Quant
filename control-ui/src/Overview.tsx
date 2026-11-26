import { Activity, ArrowDownToLine, ArrowRight, CircleCheck, Clock3, Database, Gauge, Inbox, Layers, Radio, TriangleAlert } from 'lucide-react'
import type { Hypothesis, ResearchEvent, Service, Snapshot } from './api'
import { explorerLink } from './explorerTypes'
import { Badge, clock, duration, Empty, gib, num, Progress } from './components'

const captions: Record<string, string> = {
  'hypothesis.accepted': 'Hypothesis accepted', 'job.queued': 'Trials queued', 'job.started': 'Trials started',
  'job.completed': 'Trials completed', 'job.failed': 'Trials failed', 'job.cancelled': 'Trials cancelled',
  'result.available': 'Results committed', 'capability.requested': 'SDK capability requested', 'capability.changed': 'Capability updated',
  'worker.changed': 'Dispatch settings updated', 'worker.started': 'Service started', 'worker.stopped': 'Service stopped', 'acknowledge.changed': 'Results acknowledged', 'verdict.changed': 'Verdict recorded',
  'priority.changed': 'Priority updated', 'cancel.changed': 'Cancellation requested', 'retry.changed': 'Retry requested',
}

function Events({ events, open }: { events: ResearchEvent[]; open: (id: string) => void }) {
  const grouped = new Map<string, ResearchEvent & { count: number }>()
  for (const event of [...events].reverse()) {
    const key = [event.type, event.hypothesis, event.at.slice(0, 16)].join('/')
    const found = grouped.get(key)
    if (found) found.count++
    else grouped.set(key, { ...event, count: 1 })
  }
  return <div className="events">
    {Array.from(grouped.values()).slice(0, 7).map(event => <div key={event.id} className="event">
      <span className={`event-icon ${event.type === 'job.failed' || event.type === 'capability.requested' ? 'amber' : event.type === 'job.completed' || event.type === 'result.available' ? 'mint' : ''}`}><Radio size={14} /></span>
      <div><strong>{captions[event.type] ?? event.type}</strong><small>{event.hypothesis ? <button className="text-button mono" onClick={() => open(event.hypothesis!)}>{event.hypothesis}</button> : 'Service'}{event.count > 1 ? ` · ${event.count} recent events` : ''}</small></div>
      <time className="mono">{clock(event.at)}</time>
    </div>)}
    {!events.length && <Empty title="No events yet">Lifecycle changes appear here as they commit.</Empty>}
  </div>
}

export function Worker({ service }: { service: Service }) {
  const { worker, resources, last_batch, budgets } = service
  const profile = worker.profile ?? last_batch
  const range = worker.range
  const sampled = typeof worker.through_ms === 'number' && range ? Math.max(0, Math.min(1, (worker.through_ms - range[0]) / (range[1] - range[0]))) : null
  return <div className="worker-status">
    <div className="worker-title"><div className="worker-glyph"><Activity size={19} /></div>
      <div><strong>Research dispatcher</strong><small>{service.worker_error ? 'Worker error' : service.stopping ? 'Draining active work' : budgets.paused ? 'Dispatch paused' : worker.phase === 'idle' ? 'Waiting for jobs' : worker.phase === 'processing' ? 'Processing shared blocks' : worker.phase}</small></div>
      <span className={`health-dot ${service.worker_error || budgets.paused ? 'warning' : ''}`} /></div>
    {sampled !== null && <div className="worker-progress"><div>Market range processed <span>{num(sampled * 100, 1)}%</span></div><progress value={sampled} max={1} aria-label="Market range processed" /></div>}
    <dl className="worker-metrics">
      <div><dt><Layers size={15} /> Worker allocation</dt><dd>{worker.phase === 'processing' ? num(worker.profile?.workers) : '—'} <small>/ {budgets.cpu_limit} CPU</small></dd></div>
      <div><dt><Gauge size={15} /> {worker.phase === 'processing' ? 'Current throughput' : 'Last batch throughput'}</dt><dd>{num(worker.bars_per_second ?? last_batch?.bars_per_second)} <small>bars/s</small></dd></div>
      <div><dt><Database size={15} /> Prepared cache</dt><dd>{gib(resources.cache_bytes)} {last_batch?.cache_reads != null && <small>· {num(last_batch.cache_reads)} reads</small>}</dd></div>
      <div><dt><Layers size={15} /> Shared feature nodes</dt><dd>{num(profile?.feature_nodes)}</dd></div>
    </dl>
  </div>
}

export function InboxRows({ items, open }: { items: Hypothesis[]; open: (id: string) => void }) {
  return items.length ? <div className="compact-list">{items.map(item => <div className="inbox-entry" key={item.id}><button className="compact-row" onClick={() => open(item.id)}>
    <span className="item-icon"><ArrowDownToLine size={15} /></span><span className="item-label"><strong>{item.name}</strong><small>{item.id} · {item.finished} trials {item.errors.length ? 'with errors' : 'recorded'}</small></span><Badge value={item.state} /><ChevronRightIcon />
  </button>{item.state === 'completed' && item.stage !== 'screening' && <a className="text-button inbox-explorer" href={explorerLink(item.id)} aria-label={`Explore ${item.id}`}>Explore</a>}</div>)}</div> : <Empty title="Inbox is clear">Completed or changed results will appear here.</Empty>
}

function ChevronRightIcon() { return <ArrowRight size={15} className="muted" /> }

export function Overview({ data, open, navigate }: { data: Snapshot; open: (id: string) => void; navigate: (page: string) => void }) {
  const items = data.hypotheses
  const count = (state: string) => items.filter(item => item.state === state).length
  const unreviewed = items.filter(item => item.unreviewed)
  const stats = [
    { label: 'Active hypotheses', value: count('running'), icon: Activity, note: 'Currently executing', tone: 'mint' },
    { label: 'Queued', value: count('queued'), icon: Clock3, note: 'Ready for dispatch', tone: 'neutral' },
    { label: 'Completed', value: count('completed'), icon: CircleCheck, note: 'Execution finished', tone: 'mint' },
    { label: 'Blocked', value: count('blocked'), icon: TriangleAlert, note: 'Prerequisites pending', tone: 'amber' },
    { label: 'Research inbox', value: unreviewed.length, icon: Inbox, note: 'Unreviewed outcomes', tone: 'blue' },
  ]
  const order = ['running', 'blocked', 'queued', 'failed', 'completed', 'cancelled']
  const current = [...items].sort((a, b) => order.indexOf(a.state) - order.indexOf(b.state) || b.priority - a.priority).slice(0, 6)
  return <>
    <div className="stats-grid">{stats.map(stat => <article className="stat" key={stat.label}>
      <div><span>{stat.label}</span><span className={`stat-icon ${stat.tone}`}><stat.icon size={17} /></span></div>
      <strong>{num(stat.value)}</strong><small>{stat.note}</small>
    </article>)}</div>
    <div className="overview-grid">
      <section className="panel"><header className="panel-heading"><div><h2>Hypothesis execution</h2><p>{items.length} hypotheses across {data.campaigns.length} {data.campaigns.length === 1 ? 'campaign' : 'campaigns'}</p></div><button className="text-button detail-link" onClick={() => navigate('queue')}>Open queue <ArrowRight size={15} /></button></header>
        {current.length ? <div className="table-scroll"><table className="dense-table summary-table"><thead><tr><th>Hypothesis</th><th>Execution</th><th>Stage</th><th>Trials</th></tr></thead><tbody>
          {current.map(item => <tr key={item.id}>
            <td><button className="row-link" onClick={() => open(item.id)}><strong>{item.name}</strong><small>{item.id} <span>·</span> {item.family}</small></button></td>
            <td><Badge value={item.state} /></td><td className="stage">{item.stage}</td><td><Progress item={item} /></td>
          </tr>)}</tbody></table></div> : <Empty title="No hypotheses registered">Submit an experiment specification to populate the queue.</Empty>}
      </section>
      <section className="panel"><header className="panel-heading"><div><h2>System health</h2><p>Local execution resources</p></div><button className="text-button detail-link" onClick={() => navigate('system')} aria-label="Open system"><ArrowRight size={16} /></button></header>
        <Worker service={data.service} /><div className="panel-foot"><span className="health-dot" /> Uptime <strong>{duration(data.service.resources.uptime_seconds)}</strong></div>
      </section>
      <section className="panel"><header className="panel-heading"><div><h2>Recent activity</h2><p>Committed events · UTC</p></div><Radio size={17} className="muted" /></header><Events events={data.events} open={open} /></section>
      <section className="panel"><header className="panel-heading"><div><h2>Research inbox</h2><p>{unreviewed.length} outcomes awaiting review</p></div><button className="text-button detail-link" onClick={() => navigate('inbox')} aria-label="Open inbox"><ArrowRight size={16} /></button></header><InboxRows items={unreviewed.slice(0, 5)} open={open} /></section>
    </div>
  </>
}
