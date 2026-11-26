import { useEffect, useMemo, useState, type FormEvent } from 'react'
import { CalendarClock, ChevronLeft, ChevronRight, Clock3, Expand, ListFilter, Maximize2, Minimize2, RotateCcw, SlidersHorizontal } from 'lucide-react'
import { api, useResource, type Detail, type Hypothesis } from './api'
import { Badge, Empty, ErrorBox, Json, Loading, num } from './components'
import { ExecutionCard, Ledger, PositionCard } from './ExplorerLedger'
import { MarketChart, MarkerLegend } from './MarketChart'
import { TIMEFRAMES, params, timeframe, utc, type Candles, type Execution, type Features, type Meta, type Page, type Panels, type Position, type Series, type Timeframe, type Window } from './explorerTypes'
import './explorer.css'

const initialPanels: Panels = { price: true, volume: true, executions: true, signal: true, exposure: true, pnl: true, portfolio: true }
const controls: [keyof Panels, string][] = [['price', 'Price overlays'], ['volume', 'Volume'], ['executions', 'Executions'],
  ['signal', 'Signal'], ['exposure', 'Symbol exposure'], ['pnl', 'Symbol P&L'], ['portfolio', 'Portfolio']]
const emptyEvents: Execution[] = []
const initialRoute = () => new URLSearchParams(location.hash.split('?')[1] ?? '')
type Changes = Record<string, string | number | null>

function clamp(range: Window, bounds: Window, tf: Timeframe): Window {
  const width = Math.min(bounds.end - bounds.start, Math.max(5000, Math.min(range.end - range.start, 1900 * TIMEFRAMES[tf] * 1000)))
  const start = Math.max(bounds.start, Math.min(bounds.end - width, range.start))
  return { start, end: start + width }
}

export default function Explorer({ rows, connected }: { rows: Hypothesis[]; connected: boolean }) {
  const [route, setRoute] = useState(initialRoute)
  useEffect(() => {
    const listener = () => setRoute(initialRoute())
    window.addEventListener('hashchange', listener)
    return () => window.removeEventListener('hashchange', listener)
  }, [])
  function set(values: Changes, reset = false) {
    const updated = reset ? new URLSearchParams() : initialRoute()
    for (const [key, value] of Object.entries(values)) {
      if (value == null || value === '') updated.delete(key)
      else updated.set(key, String(value))
    }
    history.replaceState(history.state, '', '#explorer' + (updated.size ? '?' + updated : ''))
    setRoute(updated)
  }
  const hypotheses = rows.filter(row => row.state === 'completed')
  const selected = route.get('hypothesis') ?? hypotheses.find(row => row.stage !== 'screening')?.id ?? hypotheses[0]?.id ?? ''
  const hypothesis = hypotheses.find(row => row.id === selected)
  const detail = useResource<Detail>(selected ? `/hypotheses/${selected}/results` : null, hypothesis?.revision)
  const candidates = detail?.value?.experiments?.filter(trial => trial.status === 'completed' && ['native', 'audit'].includes(trial.kind)) ?? []
  const job = route.get('candidate') ?? candidates[0]?.id
  const revision = rows.filter(row => row.id === selected || row.stage === 'audit').map(row => `${row.id}:${row.revision}:${row.state}`).join('/')
  return <div className="explorer">
    <section className="panel explorer-pick"><label>Hypothesis<select aria-label="Explorer hypothesis" value={selected}
      onChange={event => set({ hypothesis: event.target.value }, true)}><option value="" disabled>Select a hypothesis</option>
      {hypotheses.map(row => <option value={row.id} key={row.id}>{row.id} · {row.name}</option>)}</select></label>
      <label>Native candidate<select aria-label="Explorer candidate" value={job ?? ''} disabled={!candidates.length}
        onChange={event => set({ hypothesis: selected, candidate: event.target.value }, true)}><option value="" disabled>Select a candidate</option>
        {candidates.map(item => <option value={item.id} key={item.id}>{item.id.slice(0, 10)} · {item.kind}{item.specification.strategy ? ` · ${item.specification.strategy.id} @ ${item.specification.strategy.version}` : item.specification.parameters.fast != null ? ` · EMA ${item.specification.parameters.fast}/${item.specification.parameters.slow}` : ''}</option>)}</select></label>
      {job && <span className="mono candidate-ref">{job}</span>}
    </section>
    {!selected ? <section className="panel"><Empty title="No backtests to explore">Completed native candidates appear here.</Empty></section>
      : detail?.error ? <ErrorBox>{detail.error}</ErrorBox> : !detail ? <Loading />
      : !job ? <section className="panel"><Empty title="No completed native candidates">Choose a completed native simulation.</Empty></section>
      : <CandidateExplorer key={job} job={job} hypothesis={selected} route={route} set={set} revision={revision} connected={connected} />}
  </div>
}

function CandidateExplorer({ job, hypothesis, route, set, revision, connected }: {
  job: string; hypothesis: string; route: URLSearchParams; set: (values: Changes) => void; revision: string; connected: boolean;
}) {
  const base = `/explorer/${job}`
  const [receipt, setReceipt] = useState('')
  const [pending, setPending] = useState(false)
  const [error, setError] = useState('')
  const response = useResource<Meta>(`${base}/meta`, revision + '/' + receipt)
  const meta = response?.value
  useEffect(() => {
    if (meta && (!route.get('candidate') || !route.get('hypothesis'))) set({ candidate: job, hypothesis })
  }, [meta, job, hypothesis, route, set])

  async function replay() {
    setPending(true)
    setError('')
    try {
      const accepted = await api<{ submission_ids: string[] }>(`/hypotheses/${hypothesis}/audit`, { experiment_id: job })
      setReceipt(accepted.submission_ids[0])
    } catch (reason) {
      setError((reason as Error).message)
    } finally {
      setPending(false)
    }
  }
  return response?.error ? <ErrorBox>{response.error}</ErrorBox> : !meta ? <Loading /> : <>
    <section className="panel replay-strip" aria-label="Detailed replay status"><div className="replay-caption"><Clock3 size={18} />
      <div><strong>{meta.detailed_job ? 'Detailed native execution data' : 'Detailed execution data is not available. Generate detailed replay.'}</strong>
        <small>{meta.detailed_job ? `${meta.replay ? 'Exact replay ' + meta.replay.hypothesis_id + ' · ' : ''}${utc(meta.start, true)} → ${utc(meta.end, true)} UTC`
          : meta.compatibility ?? 'The existing research service will queue the exact replay and attach completed artifacts here.'}</small></div></div>
      {!meta.detailed_job && !meta.compatibility && <button className="primary" disabled={pending || !connected || !!receipt || ['running', 'queued'].includes(meta.replay?.status ?? '')} onClick={() => void replay()}>
        {pending ? 'Submitting…' : 'Generate detailed replay'}</button>}
      {(meta.replay || receipt) && <span className="replay-status"><span className="mono">{meta.replay?.hypothesis_id ?? receipt}</span><Badge value={meta.replay?.status ?? 'queued'} /></span>}
    </section>
    {error && <ErrorBox>{error}</ErrorBox>}
    {meta.replay?.failure && <ErrorBox>{meta.replay.failure.message ?? meta.replay.failure.type ?? 'Detailed replay failed'} <a href="#queue">Open queue</a></ErrorBox>}
    <Workspace base={base} meta={meta} route={route} set={set} />
  </>
}

function Workspace({ base, meta, route, set }: { base: string; meta: Meta; route: URLSearchParams; set: (values: Changes) => void }) {
  const symbol = meta.symbols.find(item => item === route.get('symbol')) ?? meta.symbols[0]
  const from = Number(route.get('from')), to = Number(route.get('to'))
  const prefer = from >= meta.start && to <= meta.end && to > from ? { start: from, end: to } : { start: meta.start, end: meta.end }
  const selectedTimeframe = route.get('tf') as Timeframe | null
  const tf = selectedTimeframe && Object.hasOwn(TIMEFRAMES, selectedTimeframe) ? selectedTimeframe : timeframe(prefer)
  const range = clamp(prefer, meta, tf)
  const auto = route.get('auto') !== '0'
  const extra = (range.end - range.start) * 0.12
  const fetched = clamp({ start: Math.round(range.start - extra), end: Math.round(range.end + extra) }, meta, tf)
  const revision = meta.detailed_job
  const query = params({ symbol, tf, start: fetched.start, end: fetched.end })
  const candleResult = useResource<Candles>(`${base}/candles?${query}`)
  const featureResult = useResource<Features>(!meta.strategy || revision ? `${base}/features?${query}` : null, revision)
  const timeSeries = useResource<Series>(revision ? `${base}/series?${query}` : null, revision)
  const markerQuery = params({ symbol, start: fetched.start, end: Math.min(fetched.end + 1000, meta.end + 1000), limit: 500 })
  const fills = useResource<Page<Execution>>(revision ? `${base}/fills?${markerQuery}` : null, revision)
  const rejected = useResource<Page<Execution>>(revision && route.get('reject') === '1' ? `${base}/rejected?${markerQuery}` : null, revision)
  const funding = useResource<Page<Execution>>(revision ? `${base}/funding?${markerQuery}` : null, revision)
  const events = useMemo(() => !fills?.value && !funding?.value && !rejected?.value ? emptyEvents : [
    ...(fills?.value?.rows ?? []), ...(rejected?.value?.rows ?? []),
    ...(funding?.value?.rows ?? []).map(row => ({ ...row, type: 'funding' })),
  ], [fills?.value, rejected?.value, funding?.value])
  const positionKey = route.get('position')
  const selectedPosition = useResource<Page<Execution>>(revision && positionKey ? `${base}/positions?${params({ symbol, position: positionKey, limit: 1 })}` : null, revision)
  const position = selectedPosition?.value?.position
  const [event, setEvent] = useState<Execution>()
  const activeEvent = event && (event.symbol_name == null || event.symbol_name === symbol) ? event : undefined
  const [jump, setJump] = useState('')
  const [notice, setNotice] = useState('')
  const [overlayMenu, setOverlayMenu] = useState(false)
  const expanded = route.get('expanded') === '1'
  const raisedPane = route.get('pane') ?? ''
  const hidden = route.get('hidden') ?? ''
  const panels = useMemo(() => Object.fromEntries(Object.keys(initialPanels).map(key => [key, !hidden.split(',').includes(key)])) as Panels, [hidden])

  function view(window: Window, automatic = auto, fixed?: Timeframe, additional: Changes = {}) {
    const next = fixed ?? (automatic ? timeframe(window) : tf)
    const visible = clamp(window, meta, next)
    set({ from: visible.start, to: visible.end, tf: next, ...additional })
  }

  function onViewport(next: Window) {
    const duration = next.end - next.start
    const delta = duration * 0.1
    if (Math.abs(next.start - range.start) < 500 && Math.abs(next.end - range.end) < 500) return
    // A pan to the loaded edge pulls the adjoining historical window.
    const left = next.start <= fetched.start + 5000 && fetched.start > meta.start
    const right = next.end >= fetched.end - 5000 && fetched.end < meta.end
    let shift = 0
    if (left && !right) shift = -Math.round(delta)
    if (right && !left) shift = Math.round(delta)
    view({ start: next.start + shift, end: next.end + shift })
  }

  function center(stamp: number) {
    const half = Math.round((range.end - range.start) / 2)
    view({ start: stamp - half, end: stamp + half })
  }

  function fit(item: Position) {
    const worst = item.exit_ms ?? meta.end
    const padding = Math.max(30_000, Math.round((worst - item.entry_ms) * 0.12))
    setEvent(undefined)
    view({ start: item.entry_ms - padding, end: worst + padding }, true, undefined,
      { position: item.id, symbol: item.symbol })
    window.requestAnimationFrame(() => document.querySelector('.chart-panel')?.scrollIntoView({ block: 'start' }))
  }

  function pick(row: Execution, navigate = false) {
    setEvent(row)
    if (navigate && (row.ms < range.start || row.ms >= range.end)) center(row.ms)
  }

  async function stepTrade(direction: 'previous' | 'next') {
    setNotice('')
    try {
      const time = activeEvent?.ms ?? (direction === 'previous' ? range.end : range.start - 1)
      const page = await api<Page<Execution>>(`${base}/fills?${params({ symbol, near: time, direction, limit: 1 })}`)
      if (page.rows[0]) pick(page.rows[0], true)
      else setNotice(`No ${direction} fill for ${symbol}.`)
    } catch (reason) { setNotice((reason as Error).message) }
  }

  function submitJump(event: FormEvent) {
    event.preventDefault()
    setNotice('')
    const stamp = Date.parse(jump + 'Z')
    if (!Number.isFinite(stamp) || stamp < meta.start || stamp >= meta.end) {
      setNotice('Enter a UTC timestamp inside the candidate’s date range.')
      return
    }
    center(stamp)
  }

  function toggle(key: keyof Panels) {
    const updated = Object.keys(panels).filter(item => item === key ? panels[key] : !panels[item as keyof Panels])
    set({ hidden: updated.join(','), ...(raisedPane === key ? { pane: null } : {}) })
  }
  const candleRows = candleResult?.value
  const gaps = candleRows?.rows.filter(row => row.status >= 2).length ?? 0
  const empty = candleRows?.rows.filter(row => row.status === 1).length ?? 0
  const bounded = [fills?.value, rejected?.value, funding?.value].some(result => result && result.total > result.rows.length)

  return <>
    <section className={`panel chart-panel ${expanded ? 'expanded-chart' : ''}`} aria-label="Market and strategy chart">
      <div className="chart-toolbar">
        <div className="chart-symbol"><label>Symbol<select aria-label="Chart symbol" value={symbol} onChange={event => { setEvent(undefined); set({ symbol: event.target.value, position: null }) }}>
          {meta.symbols.map(item => <option key={item}>{item}</option>)}</select></label>
          <label>Timeframe<select aria-label="Chart timeframe" value={tf} onChange={event => view(range, false, event.target.value as Timeframe, { auto: 0 })}>
            {Object.keys(TIMEFRAMES).map(item => <option key={item}>{item}</option>)}</select></label>
          <label className="toggle"><input type="checkbox" checked={auto} onChange={event => view(range, event.target.checked, undefined, { auto: event.target.checked ? null : 0 })} /> Auto resolution</label>
        </div>
        <div className="chart-action-buttons">
          <button aria-label="Previous trade" title="Previous fill" disabled={!revision} onClick={() => void stepTrade('previous')}><ChevronLeft size={16} /> Trade</button>
          <button aria-label="Next trade" title="Next fill" disabled={!revision} onClick={() => void stepTrade('next')}>Trade <ChevronRight size={16} /></button>
          <button aria-label="Reset zoom" title="Reset zoom" onClick={() => view(meta, true, undefined, { auto: null })}><RotateCcw size={16} /></button>
          <button aria-label="Chart overlays" aria-expanded={overlayMenu} onClick={() => setOverlayMenu(!overlayMenu)}><SlidersHorizontal size={16} /> Overlays</button>
          <button aria-label={expanded ? 'Restore chart' : 'Expand chart'} onClick={() => set({ expanded: expanded ? null : 1 })}>{expanded ? <Minimize2 size={16} /> : <Maximize2 size={16} />}</button>
        </div>
      </div>
      <div className="chart-context"><span className="mono">{utc(range.start, true)} → {utc(range.end, true)} UTC</span>
        <form onSubmit={submitJump}><input aria-label="Jump timestamp UTC" type="datetime-local" step={1} value={jump} onChange={event => setJump(event.target.value)} required /><button><CalendarClock size={15} /> Jump UTC</button></form>
        <div className="chart-action-buttons"><button aria-label="Load earlier chart window" disabled={range.start <= meta.start} onClick={() => view({ start: range.start - (range.end - range.start), end: range.start })}><ChevronLeft size={14} /></button>
          <button aria-label="Load later chart window" disabled={range.end >= meta.end} onClick={() => view({ start: range.end, end: range.end + (range.end - range.start) })}><ChevronRight size={14} /></button></div>
      </div>
      {overlayMenu && <div className="overlay-options">{controls.map(([key, title]) => <div className="overlay-choice" key={key}><label className="toggle">
        <input type="checkbox" checked={panels[key]} onChange={() => toggle(key)} />{title}</label>
        {panels[key] && ['signal', 'exposure', 'pnl', 'portfolio'].includes(key) && <button className="icon-button" title={`Expand ${title}`} aria-label={`Expand ${title}`}
          onClick={() => set({ pane: raisedPane === key ? null : key })}><Expand size={13} /></button>}</div>)}
        <label className="toggle"><input type="checkbox" checked={route.get('reject') === '1'} onChange={event => set({ reject: event.target.checked ? 1 : null })} /> Rejected orders</label></div>}
      {notice && <p className="chart-notice" role="status">{notice}</p>}
      {featureResult?.error && <ErrorBox>Feature overlays unavailable. {featureResult.error}</ErrorBox>}
      {timeSeries?.error && <ErrorBox>Marked series unavailable. {timeSeries.error}</ErrorBox>}
      {timeSeries?.value?.symbol_error && <p className="chart-notice">{timeSeries.value.symbol_error}</p>}
      {(fills?.error || funding?.error || rejected?.error) && <ErrorBox>{fills?.error ?? funding?.error ?? rejected?.error}</ErrorBox>}
      {candleResult?.error ? <ErrorBox>{candleResult.error}</ErrorBox> : !candleRows ? <Loading /> : candleRows.rows.length ? <MarketChart
        candles={candleRows} features={featureResult?.value} series={timeSeries?.value} events={events} panels={panels}
        selected={activeEvent} position={position} expanded={expanded} raisedPane={raisedPane} visible={range} onPick={pick} onWindow={onViewport} />
        : <Empty title="No complete candles in this window">Select a finer timeframe.</Empty>}
      {candleRows && <div className="chart-caption"><span>{num(candleRows.rows.length)} display buckets · {num(gaps)} missing / quarantined / pre-listing · {num(empty)} confirmed empty</span>
        {bounded && <span className="amber-text">Overlay limited to the first 500 events per type. Zoom in or use the ledger.</span>}</div>}
      {panels.executions && revision && <MarkerLegend />}
      <div className="series-legends">
        {featureResult?.value?.features.map(feature => <span key={feature.name}><i style={{ backgroundColor: feature.color }} />{feature.label} · {feature.units}</span>)}
        {revision && panels.exposure && <span>Exposure · target, actual marked notional, quantity, average entry{timeSeries?.value && !timeSeries.value.symbol.qty_steps ? ' · sampled view' : ''}</span>}
        {revision && panels.pnl && <span>Symbol P&L · marked, realized, unrealized, fees, funding</span>}
        {revision && panels.portfolio && <span>Portfolio · equity, gross, net, running maximum drawdown</span>}
      </div>
    </section>
    {featureResult?.value && <details className="panel explorer-metadata"><summary>Feature definitions and provenance</summary><Json value={featureResult.value.features} /></details>}
    {selectedPosition?.error && <ErrorBox>{selectedPosition.error}</ErrorBox>}
    {position && revision && <PositionCard key={position.id} position={position} base={base} symbol={symbol} clear={() => set({ position: null })} fit={() => fit(position)} pick={row => pick(row, true)} />}
    {activeEvent && <ExecutionCard event={activeEvent} symbol={symbol} clear={() => setEvent(undefined)} />}
    {revision && <Ledger key={symbol} base={base} symbol={symbol} range={range} route={route} set={set}
      selected={position?.id} select={fit} pick={row => pick(row, true)} />}
    <section className="panel explorer-notes"><ListFilter size={16} /><div><strong>Position and mark definitions</strong>
      <p>MFE and MAE are the highest and lowest sampled gross unrealized P&amp;L using native mark inputs at five-second post-fill snapshots. Coverage excludes stale carried marks. Returns divide realized net P&amp;L plus terminal open P&amp;L by the total opening fill notional. Slippage is included in modeled fill prices.</p>
      {!!meta.funding_warnings.length && <details><summary>Funding coverage warnings</summary><Json value={meta.funding_warnings} /></details>}
    </div></section>
  </>
}
