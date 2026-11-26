import { lazy, Suspense, useCallback, useEffect, useState, type FormEvent } from 'react'
import { Activity, ArrowDownToLine, BookOpen, ChartCandlestick, ChevronRight, CircleAlert, Command, Cpu, FlaskConical, Gauge, Inbox, LayoutGrid, ListOrdered, Pause, Play, Plus, Radio, Waves } from 'lucide-react'
import { api, useControl } from './api'
import { Badge, ErrorBox, Loading, Modal, num } from './components'
import { InboxRows, Overview } from './Overview'
import { Queue } from './Queue'
import type { Operation } from './Results'
import { Details, Library, Requests, System } from './Views'

const Explorer = lazy(() => import('./Explorer'))
const Results = lazy(() => import('./Results').then(module => ({ default: module.Results })))

const navigation = [
  { id: 'overview', label: 'Overview', icon: LayoutGrid, description: 'Execution, research outcomes, and local resources at a glance.' },
  { id: 'queue', label: 'Hypothesis Queue', icon: ListOrdered, description: 'Prioritize and monitor development experiments.' },
  { id: 'library', label: 'Hypothesis Library', icon: BookOpen, description: 'Registered mechanisms, predictions, and experiment history.' },
  { id: 'results', label: 'Alpha Results', icon: FlaskConical, description: 'Inspect screening evidence and marked portfolio results.' },
  { id: 'explorer', label: 'Backtest Explorer', icon: ChartCandlestick, description: 'Inspect market prices, simulated executions, and positions.' },
  { id: 'inbox', label: 'Research Inbox', icon: Inbox, description: 'Review durable outcomes and acknowledge the evidence you have seen.' },
  { id: 'requests', label: 'SDK Requests', icon: Command, description: 'Track missing capabilities and engineering review.' },
  { id: 'system', label: 'System', icon: Cpu, description: 'Inspect host telemetry, dispatch limits, and data coverage.' },
]
const pageFromHash = () => navigation.find(item => item.id === location.hash.slice(1).split('?')[0])?.id ?? 'overview'
type Prompt = { operation: string; ids?: string[]; values?: object }

export default function App() {
  const [page, setPage] = useState(pageFromHash)
  const [selected, setSelected] = useState('')
  const [resultId, setResultId] = useState('')
  const [prompt, setPrompt] = useState<Prompt | null>(null)
  const [showReviewed, setShowReviewed] = useState(false)
  const [notice, setNotice] = useState('')
  const [failure, setFailure] = useState('')
  const [saving, setSaving] = useState(false)
  const { data, connected, error, refresh } = useControl()
  const open = useCallback((id: string) => setSelected(id), [])
  const navigate = useCallback((id: string) => {
    if (location.hash !== `#${id}`) location.hash = id
    setPage(id)
  }, [])
  useEffect(() => {
    const onHash = () => {
      const next = pageFromHash()
      if (next === 'explorer') setSelected('')
      setPage(next)
    }
    window.addEventListener('hashchange', onHash)
    return () => window.removeEventListener('hashchange', onHash)
  }, [])
  useEffect(() => {
    if (!notice) return
    const timer = setTimeout(() => setNotice(''), 6000)
    return () => clearTimeout(timer)
  }, [notice])
  const current = navigation.find(item => item.id === page)!
  const service = data?.service
  const items = data?.hypotheses ?? []
  const item = items.find(h => h.id === selected)
  const unread = items.filter(h => h.unreviewed).length
  const active = items.filter(h => h.state === 'running').length

  async function send(path: string, payload: unknown, message: string) {
    setSaving(true)
    setFailure('')
    try {
      const result = await api<{ submission_ids?: string[] }>(path, payload)
      setNotice(result.submission_ids ? `Accepted ${result.submission_ids.join(', ')}` : message)
      setPrompt(null)
    } catch (reason) {
      setFailure((reason as Error).message)
    } finally {
      setSaving(false)
    }
  }
  function queueAction(operation: string, ids: string[], values: object = {}) {
    return send(ids.length === 1 ? `/hypotheses/${ids[0]}/${operation}` : `/queue/${operation}`, { ...values, ids }, {
      cancel: 'Cancellation requested', retry: 'Retry requested', priority: 'Priority saved',
      acknowledge: 'Result acknowledged', verdict: 'Verdict recorded', audit: 'Audit replay accepted',
    }[operation] || 'Queue updated')
  }
  const operate: Operation = (operation, ids, values) => {
    setFailure('')
    if (!connected) { setFailure('Reconnect to the local service before changing research state.'); return }
    if (['cancel', 'priority', 'verdict', 'audit'].includes(operation)) {
      setPrompt({ operation, ids, values })
    } else {
      void queueAction(operation, ids, values)
    }
  }
  function showPrompt(operation: string) {
    setFailure('')
    setPrompt({ operation })
  }
  function flipPause() {
    if (!service) return
    if (service.budgets.paused) void send('/service/settings', { paused: false }, 'Dispatch resumed')
    else showPrompt('pause')
  }
  async function submitForm(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (!prompt || saving) return
    const form = new FormData(event.currentTarget)
    try {
      if (prompt.operation === 'submit' || prompt.operation === 'request') {
        const file = form.get('file')
        const raw = file instanceof File && file.size ? await file.text() : String(form.get('spec') ?? '')
        await send(prompt.operation === 'submit' ? '/hypotheses' : '/requests', JSON.parse(raw.replace(/^\uFEFF/, '')), 'Request recorded')
      } else if (prompt.operation === 'pause') {
        await send('/service/settings', { paused: true }, 'Dispatch paused')
      } else if (prompt.operation === 'configure') {
        await send('/service/settings', {
          cpu_limit: Number(form.get('cpu')), memory_budget_gib: Number(form.get('memory')), disk_budget_gib: Number(form.get('disk')),
        }, 'Resource budgets saved')
      } else {
        const values = prompt.operation === 'priority' ? { priority: Number(form.get('priority')) }
          : prompt.operation === 'verdict' ? { verdict: form.get('verdict'), note: form.get('note') }
          : prompt.values
        await queueAction(prompt.operation, prompt.ids!, values)
      }
    } catch (reason) { setFailure((reason as Error).message) }
  }

  return <div className="shell">
    <aside className="sidebar">
      <a href="#overview" className="brand" onClick={() => navigate('overview')}><span className="brand-mark"><Waves size={23} strokeWidth={2.3} /></span><span><strong>Quant</strong><small>RESEARCH CONTROL</small></span></a>
      <div className="workspace"><span className="workspace-icon"><Activity size={15} /></span><span><strong>Local research</strong><small>Liquid 50 · Development</small></span></div>
      <nav aria-label="Workspace navigation">
        {navigation.map((entry, index) => <div key={entry.id} className={index === 6 ? 'nav-divider' : undefined}>
          <a href={`#${entry.id}`} className={page === entry.id ? 'active' : ''} aria-label={entry.label} aria-current={page === entry.id ? 'page' : undefined} onClick={() => navigate(entry.id)}>
            <entry.icon size={18} strokeWidth={1.8} /> <span>{entry.label}</span>{entry.id === 'inbox' && unread > 0 && <b>{unread}</b>}
          </a>
        </div>)}
      </nav>
      <div className="sidebar-bottom"><span className={`health-dot ${connected ? '' : 'warning'}`} /><div><strong>{connected ? 'Local service connected' : 'Local service disconnected'}</strong><small>127.0.0.1:{location.port || '80'}</small></div><Radio size={15} /></div>
    </aside>
    <div className="workspace-main">
      <header className="topbar"><div className="crumb"><span>Research workspace</span><ChevronRight size={14} /> <strong>{current.label}</strong></div>
        <div className="top-actions"><span className={`connection ${connected ? '' : 'offline'}`}><span className="health-dot" /> {connected ? service?.stopping ? 'Stopping' : 'Live' : 'Offline'}</span>
          <button onClick={flipPause} disabled={!connected || saving || service?.stopping}>{service?.budgets.paused ? <Play size={15} /> : <Pause size={15} />}{service?.budgets.paused ? 'Resume dispatch' : 'Pause dispatch'}</button>
          <button className="primary" disabled={!connected || saving || service?.stopping} onClick={() => showPrompt('submit')}><Plus size={17} /> Submit hypothesis</button>
        </div></header>
      <main>
        <div className="page-head"><div><h1>{current.label}</h1><p>{current.description}</p></div>{page === 'overview' && service && <div className="live-label"><Gauge size={17} /><span>{service.worker_error ? 'Dispatcher error' : service.budgets.paused ? 'Dispatch paused' : active ? `${num(active)} active hypotheses` : 'Dispatcher idle'}</span></div>}</div>
        {data && !connected && <div className="disconnect" role="status"><CircleAlert size={17} /><span>Event stream disconnected. Showing the last received state.</span><button onClick={refresh}>Reconnect</button></div>}
        {service?.worker_error && <ErrorBox>Dispatcher stopped. {service.worker_error}</ErrorBox>}
        {error && <ErrorBox>{error} <button onClick={refresh}>Retry snapshot</button></ErrorBox>}
        {failure && !prompt && <ErrorBox>{failure}</ErrorBox>}
        {!data && !error && <Loading />}
        {data && <>
          {page === 'overview' && <Overview data={data} open={open} navigate={navigate} />}
          {page === 'queue' && <Queue rows={items} open={open} bulk={operate} />}
          {page === 'library' && <Library items={items} open={open} />}
          {page === 'results' && <Suspense fallback={<Loading />}><Results rows={items} selected={resultId} pick={setResultId} /></Suspense>}
          {page === 'explorer' && <Suspense fallback={<Loading />}><Explorer rows={items} connected={connected} /></Suspense>}
          {page === 'inbox' && <section className="panel"><header className="panel-heading"><div><h2>Research outcomes</h2><p>{unread} unreviewed</p></div><label className="toggle"><input type="checkbox" checked={showReviewed} onChange={event => setShowReviewed(event.target.checked)} /> Include reviewed</label></header><InboxRows items={items.filter(h => h.revision > 0 && h.finished === h.total && (showReviewed || h.unreviewed)).sort((a, b) => b.revision - a.revision)} open={open} /></section>}
          {page === 'requests' && <Requests requests={data.requests} items={items} open={open} submit={() => showPrompt('request')} />}
          {page === 'system' && <System service={data.service} connected={connected} configure={() => showPrompt('configure')} />}
        </>}
      </main>
    </div>
    {item && <Modal drawer title={item.name} close={() => setSelected('')}><Details item={item} operate={operate} showResults={() => { setResultId(item.id); setSelected(''); navigate('results') }} /></Modal>}
    {prompt && <Modal title={{ submit: 'Submit research', request: 'Request a capability', configure: 'Configure resource budgets', cancel: 'Cancel queued or running work', pause: 'Pause dispatch', priority: 'Set priority', verdict: 'Record research verdict', audit: 'Queue audit replay' }[prompt.operation] ?? prompt.operation} close={() => { if (!saving) { setFailure(''); setPrompt(null) } }}>
      <form className="control-form" onSubmit={submitForm}>
        {['submit', 'request'].includes(prompt.operation) && <>
          <p>{prompt.operation === 'submit' ? 'Upload a hypothesis or campaign JSON file. You can also paste the full specification.' : 'Upload or paste a capability request specification.'}</p>
          <label><span><ArrowDownToLine size={15} /> JSON file</span><input type="file" name="file" accept=".json,application/json" /></label>
          <label>JSON specification<textarea name="spec" rows={8} placeholder={prompt.operation === 'submit' ? '{ "campaign": "…", "hypotheses": […] }' : '{ "module": "quant.…", "name": "…" }'} /></label>
        </>}
        {prompt.operation === 'pause' && <p>The dispatcher will finish its current wave and wait for you to resume.</p>}
        {prompt.operation === 'cancel' && <p>Cancel active and queued trials for {prompt.ids?.join(', ')}? Committed results will stay available. Running trials stop at a shared block boundary.</p>}
        {prompt.operation === 'audit' && <p>Replay the selected native experiment with full accounting logs?</p>}
        {prompt.operation === 'priority' && <label>Priority (−100 to 100)<input name="priority" type="number" min={-100} max={100} defaultValue={item?.priority ?? 0} required /></label>}
        {prompt.operation === 'verdict' && <><label>Research verdict<select name="verdict" defaultValue={item?.verdict ?? 'inconclusive'}><option>promising</option><option>rejected</option><option>inconclusive</option></select></label>
          <label>Supporting evidence<textarea name="note" defaultValue={item?.verdict_note ?? ''} required rows={4} /></label></>}
        {prompt.operation === 'configure' && service && <>
          <label>CPU limit<input type="number" name="cpu" required min={1} max={service.hardware.usable_cpus} defaultValue={service.budgets.cpu_limit} /></label>
          <label>Memory reservation (GiB)<input type="number" name="memory" required min={0.01} max={1024} step="any" defaultValue={service.budgets.memory_budget_gib} /></label>
          <label>Prepared cache disk reservation (GiB)<input type="number" name="disk" required min={0.01} max={1024} step="any" defaultValue={service.budgets.disk_budget_gib} /></label>
        </>}
        {failure && <ErrorBox>{failure}</ErrorBox>}
        <div className="form-actions"><button type="button" onClick={() => { setFailure(''); setPrompt(null) }} disabled={saving}>Close</button><button type="submit" className={prompt.operation === 'cancel' ? 'danger' : 'primary'} disabled={saving || !connected}>{saving ? 'Saving…' : prompt.operation === 'submit' ? 'Submit' : prompt.operation === 'cancel' ? 'Confirm cancellation' : 'Confirm'}</button></div>
      </form>
    </Modal>}
    {notice && <div className="toast" role="status"><Badge value="accepted" /><span>{notice}</span></div>}
  </div>
}
