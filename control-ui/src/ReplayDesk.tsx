import { useCallback, useEffect, useMemo, useRef, useState, type FormEvent, type ReactNode } from 'react'
import { ArrowDownUp, ChevronLeft, ChevronRight, ChevronsLeft, ChevronsRight, Crosshair, Expand, Filter,
  Keyboard, List, Maximize2, Minimize2, Moon, Pause, Play, RefreshCcw, RotateCcw, Settings2, Sun } from 'lucide-react'
import { api, useResource } from './api'
import { duration, ErrorBox } from './components'
import { ReplayChart, precise, usd, type ChartPrefs, type PanelId, type ReplayWindow } from './ReplayChart'
import { actionName } from './ReplayMarkers'
import { TIMEFRAMES, params, utc, type Execution, type Meta, type Timeframe, type Window } from './explorerTypes'
import './replay-desk.css'

export type ReplayMeta = Meta & { source?: 'native' | 'external'; revision?: string; order_coverage?: [number, number][] }
type Life = { id: string; symbol: string; side: string; state: string; entry_ms: number; exit_ms?: number; provenance?: string;
  duration_ms?: number; net_pnl?: number; reported_pnl?: number; max_notional?: number; mae?: number; mfe?: number; executions?: number }
type ReplayRow = Execution & { id: string; rank: number | null; ordinal: number; position_info?: Life; previous_ms?: number }
type Browse = { row: ReplayRow | null; total: number }
type Page<T> = { rows: T[]; total: number; rank?: number }
type WatchRow = { symbol: string; market: number | null; executed: number; coverage: number; pnl: number | null; positions: number; count: number }
type Watch = Window & { rows: WatchRow[] }
type Prefs = ChartPrefs & { tf: Timeframe; mode: 'analysis' | 'true'; watch: boolean; ledger: boolean; inspector: boolean;
  speed: number; period: string; sort: keyof WatchRow; descending: boolean; ledgerHeight: number }
type Filters = Record<string, string>
type Action = 'previous' | 'next' | 'first' | 'last' | 'previousPosition' | 'nextPosition' | 'rank' | 'select'

const defaults: Prefs = {
  tf: '1m', mode: 'analysis', light: false, log: false, labels: true, volume: true, marked: false, net: false,
  entry: true, initial: false, features: false, breakeven: false, connections: false,
  panels: { inventory: true, unrealized: true, realized: true, signals: false, equity: false, funding: false }, heights: {},
  watch: true, ledger: true, inspector: true, speed: 1, period: 'research', sort: 'count', descending: true, ledgerHeight: 224,
}
const optionNames: Record<PanelId, string> = { inventory: 'Signed inventory', unrealized: 'Unrealized P&L',
  realized: 'Cumulative realized', equity: 'Account equity', signals: 'Strategy signals', funding: 'Funding' }
const ROW = 26
const PAGE = 100
const same = (a: Window, b: Window) => a.start === b.start && a.end === b.end
const storageKey = 'quant.replay.preferences.v1'
const read = <T,>(key: string, fallback: T): T => {
  try { return JSON.parse(localStorage.getItem(key) ?? 'null') ?? fallback }
  catch { return fallback }
}
const save = (key: string, value: unknown) => { try { localStorage.setItem(key, JSON.stringify(value)) } catch { /* storage unavailable */ } }
const compact = (value: number | null) => value == null ? '—' : new Intl.NumberFormat('en', { notation: 'compact', maximumFractionDigits: 1 }).format(value)
const epoch = (text: string) => /^d{13}$/.test(text.trim()) ? Number(text) : Date.parse(/(?:Z|[+-]dd:dd)$/.test(text) ? text : text + 'Z')
const remembered = (job: string, symbol: string) => `quant.replay.event.${job}.${symbol}`

export function ReplayDesk({ meta, refresh }: { meta: ReplayMeta; refresh: () => void }) {
  const base = `/replay/${meta.job}`
  const ready = !!meta.detailed_job
  const isExternal = meta.source === 'external'
  const initial = useMemo(() => new URLSearchParams(location.hash.split('?')[1] ?? ''), [])
  const [prefs, put] = useState<Prefs>(() => {
    const old = read<Partial<Prefs>>(storageKey, {})
    const fromUrl = initial.get('tf') as Timeframe
    return { ...defaults, ...old, ...(Object.hasOwn(TIMEFRAMES, fromUrl) ? { tf: fromUrl } : {}), panels: { ...defaults.panels, ...old.panels } }
  })
  const [symbol, setSymbol] = useState(() => [initial.get('symbol'), read(`quant.replay.symbol.${meta.job}`, '')]
    .find(s => s && meta.symbols.includes(s)) || meta.symbols[0])
  const step = TIMEFRAMES[prefs.tf] * 1000
  const defaultRange = useCallback((stamp = meta.start) => {
    const width = step * 250
    return bounded({ start: stamp - width * .32, end: stamp + width * .68 }, meta, step)
  }, [step, meta.start, meta.end])
  const [view, setView] = useState<Window>(() => {
    const from = Number(initial.get('from')), to = Number(initial.get('to'))
    return from > 0 && to > from ? bounded({ start: from, end: to }, meta, step) : defaultRange()
  })
  const [loaded, setLoaded] = useState<Window>(() => buffered(view, meta, step))
  const [selected, setSelected] = useState<ReplayRow>()
  const [total, setTotal] = useState(0)
  const [notice, setNotice] = useState('')
  const [filters, setFilters] = useState<Filters>(() => read('quant.replay.filters', {}))
  const [settings, setSettings] = useState(false)
  const [search, setSearch] = useState('')
  const [jump, setJump] = useState('')
  const [shortcuts, setShortcuts] = useState(false)
  const [expanded, setExpanded] = useState(false)
  const [playing, setPlaying] = useState(false)
  const [seek, setSeek] = useState<number>()
  const [cursor, setCursor] = useState<number>()
  const [actualTab, setActualTab] = useState<'executions' | 'lifecycles'>('executions')
  const abort = useRef<AbortController | null>(null)
  const initialized = useRef(false)
  const latest = useRef({ symbol, view, selected, filters, meta, step, prefs })
  latest.current = { symbol, view, selected, filters, meta, step, prefs }
  const trueMode = prefs.mode === 'true'
  const clock = trueMode ? cursor ?? selected?.ms ?? view.start : undefined
  const filterScope = useMemo(() => ({ ...filters,
    from: filters.from && Number.isFinite(epoch(filters.from)) ? epoch(filters.from) : null,
    to: filters.to && Number.isFinite(epoch(filters.to)) ? epoch(filters.to) : null,
  }), [filters])
  const filterRef = useRef(filterScope)
  filterRef.current = filterScope

  function replace(next: Partial<Prefs>) { put(old => ({ ...old, ...next })) }
  useEffect(() => { save(storageKey, prefs) }, [prefs])
  useEffect(() => { save('quant.replay.filters', filters) }, [filters])

  function route(values: Record<string, string | number | null | undefined>) {
    const q = new URLSearchParams(location.hash.split('?')[1] ?? '')
    for (const [key, value] of Object.entries(values)) {
      if (value == null) q.delete(key)
      else q.set(key, String(value))
    }
    history.replaceState(history.state, '', '#explorer?' + q.toString())
  }

  const range = useCallback((candidate: Window, resolution = latest.current.step) => {
    const limits = latest.current.meta
    const next = bounded(candidate, limits, resolution)
    setView(old => same(old, next) ? old : next)
    setLoaded(old => {
      const margin = resolution * 18
      if (resolution === latest.current.step && next.end - next.start <= 1400 * resolution
        && (next.start >= old.start + margin || old.start <= limits.start)
        && (next.end <= old.end - margin || old.end >= limits.end)) return old
      return buffered(next, limits, resolution)
    })
    route({ from: next.start, to: next.end })
  }, [])

  const move = useCallback(async (action: Action, fields: Record<string, string | number | null> = {}, target?: string) => {
    const state = latest.current
    if (!state.meta.detailed_job) return
    const forSymbol = target || state.symbol
    if (abort.current && !target && action === 'next') return
    abort.current?.abort()
    const request = new AbortController()
    abort.current = request
    setNotice('')
    const scoped = filterRef.current
    try {
      const result = await api<Browse>(`${base}/navigate?${params({
        ...scoped, symbol: forSymbol, directionStep: action, id: state.selected?.symbol_name === forSymbol ? state.selected.id : null,
        clock: state.prefs.mode === 'true' ? state.selected?.ms ?? state.view.start : null, ...fields,
      })}`, undefined, request.signal)
      if (request.signal.aborted || latest.current.symbol !== forSymbol) return
      setTotal(result.total)
      if (!result.row) {
        if (fields.id && target) {
          abort.current = null
          void move('first', {}, target)
        } else setNotice('No execution matches this selection.')
        return
      }
      const row = result.row
      if (row.id === latest.current.selected?.id && (action === 'next' || action === 'previous')) setPlaying(false)
      setSelected(row)
      setCursor(undefined)
      const current = latest.current.view
      const resolution = latest.current.step
      const width = current.end - current.start
      const padding = Math.min(width * .07, resolution * 16)
      if (row.ms < current.start + padding || row.ms >= current.end - padding) {
        const start = row.ms < current.start + padding ? row.ms - width * .20 : row.ms - width * .80
        range({ start, end: start + width })
      }
      save(remembered(meta.job, forSymbol), row.id)
      route({ symbol: forSymbol, execution: row.id, position: row.position as string | undefined })
    } catch (error) {
      if (!request.signal.aborted) setNotice((error as Error).message)
    } finally {
      if (abort.current === request) abort.current = null
    }
  }, [base, range, meta.job])

  useEffect(() => {
    if (!ready) return
    const explicit = initialized.current ? null : initial.get('execution')
    const position = initialized.current ? null : initial.get('position')
    const saved = explicit || read(remembered(meta.job, symbol), '')
    initialized.current = true
    setPlaying(false)
    setSelected(undefined)
    setCursor(undefined)
    setTotal(0)
    save(`quant.replay.symbol.${meta.job}`, symbol)
    route({ symbol, position: null, execution: null })
    if (position && !explicit) void move('select', { position }, symbol)
    else if (saved) void move('select', { id: saved }, symbol)
    else void move('first', {}, symbol)
    return () => { abort.current?.abort(); abort.current = null }
  }, [symbol, ready, base, move, initial, meta.job])

  function pickSymbol(next: string) {
    if (next === symbol) return
    abort.current?.abort()
    setSelected(undefined)
    setTotal(0)
    setSymbol(next)
  }

  const watchAnchor = clock == null ? undefined : Math.floor(clock / 60_000) * 60_000
  const watchResult = useResource<Watch>(`${base}/watch?${params({ period: prefs.period, clock: watchAnchor })}`, meta.revision)
  const watchRows = useMemo(() => (watchResult?.value?.rows ?? []).filter(row => row.symbol.toLowerCase().includes(search.trim().toLowerCase()))
    .sort((a, b) => {
      const x = a[prefs.sort], y = b[prefs.sort], direction = prefs.descending ? -1 : 1
      if (x == null || y == null) return x == null ? y == null ? a.symbol.localeCompare(b.symbol) : 1 : -1
      return direction * (typeof x === 'number' && typeof y === 'number' ? x - y : String(x).localeCompare(String(y)))
    }), [watchResult?.value, prefs.sort, prefs.descending, search])
  const windowPath = `${base}/window?${params({
    symbol, tf: prefs.tf, start: loaded.start, end: loaded.end, clock, selected: trueMode ? selected?.id : null,
    features: prefs.features || prefs.panels.signals ? 1 : null, equity: prefs.panels.equity ? 1 : null,
    connections: prefs.connections ? 1 : null,
  })}`
  const windowResult = useResource<ReplayWindow>(windowPath, meta.revision)
  const retained = useRef<{ key: string; data: ReplayWindow } | undefined>(undefined)
  const chartKey = `${symbol}/${prefs.tf}/${prefs.mode}/${clock ?? ''}/${trueMode ? selected?.id : ''}`
  if (windowResult?.value) retained.current = { key: chartKey, data: windowResult.value }
  const chartData = windowResult?.value || (retained.current?.key === chartKey ? retained.current.data : undefined)
  const matching = typeof selected?.rank === 'number' ? selected.rank + 1 : null
  const activeCount = trueMode ? matching ?? 0 : total
  const visibleSelection = useMemo<ReplayRow | undefined>(() => {
    if (clock == null || !selected) return selected
    if (selected.ms > clock) return undefined
    if (isExternal) {
      const { id, ms, price, filled, filled_notional, side, reported_pnl, reported_cumulative, rank, ordinal, previous_ms } = selected
      return { id, ms, price, filled, filled_notional, side, reported_pnl, reported_cumulative, rank, ordinal, previous_ms,
        type: reported_pnl ? 'realized' : 'unclassified', provenance: 'Public order as reported at the replay clock' } as ReplayRow
    }
    const life = selected.position_info
    return { ...selected, position_info: life ? { id: life.id, symbol: life.symbol, side: life.side,
      entry_ms: life.entry_ms, state: life.exit_ms != null && life.exit_ms <= clock ? 'closed' : 'open' } : undefined } as ReplayRow
  }, [clock, selected, isExternal])

  function focus(stamp: number) {
    const width = view.end - view.start
    range({ start: stamp - width / 2, end: stamp + width / 2 })
  }
  function fitLife(position = visibleSelection?.position_info) {
    if (!position) return
    const stop = position.exit_ms ?? (trueMode ? clock ?? position.entry_ms : selected?.ms ?? view.end)
    const pad = Math.max(step * 12, (stop - position.entry_ms) * .10)
    if (stop - position.entry_ms + 2 * pad > step * 1450) {
      setNotice('Select a coarser candle resolution to fit this lifecycle.')
      return
    }
    range({ start: position.entry_ms - pad, end: stop + pad })
  }
  function fitExecutions() {
    const fills = chartData?.markers.filter(row => row.ms >= view.start && row.ms <= view.end) || []
    if (!fills.length) return
    const first = fills[0].ms, last = fills[fills.length - 1].ms
    const pad = Math.max(step * 8, (last - first) * .12)
    range({ start: first - pad, end: last + pad })
  }
  function candle(dir: number) {
    const now = cursor ?? selected?.ms ?? view.start
    const target = Math.min(meta.end, Math.max(meta.start, Math.floor(now / step) * step + dir * step))
    setCursor(target)
    if (target < view.start || target >= view.end) focus(target)
  }
  function chooseTime(event: FormEvent) {
    event.preventDefault()
    if (!jump.trim()) return
    if (jump.startsWith('position:')) void move('select', { position: jump.slice(9).trim() })
    else if (/^d{13}$|^d{4}-dd-dd[T ]/.test(jump.trim())) {
      const stamp = epoch(jump)
      if (Number.isFinite(stamp) && stamp >= meta.start && stamp <= meta.end) {
        setCursor(stamp)
        focus(stamp)
      } else setNotice('Enter a UTC timestamp inside the recorded window.')
    } else void move('select', { id: jump.trim() })
  }
  function resolution(next: Timeframe) {
    replace({ tf: next })
    route({ tf: next })
    const ms = TIMEFRAMES[next] * 1000
    const span = Math.max(ms * 40, Math.min(ms * 1100, view.end - view.start))
    const oldCenter = (view.end + view.start) / 2
    const center = selected && Math.abs(selected.ms - oldCenter) > span * .45 ? selected.ms : oldCenter
    const updated = bounded({ start: center - span / 2, end: center + span / 2 }, meta, ms)
    setLoaded(buffered(updated, meta, ms))
    range(updated, ms)
  }

  useEffect(() => {
    if (!playing) return
    const timer = window.setInterval(() => void move('next'), 850 / prefs.speed)
    return () => clearInterval(timer)
  }, [playing, prefs.speed, move])
  const keys = useRef<(event: KeyboardEvent) => void>(() => {})
  keys.current = event => {
    if (event.defaultPrevented || event.ctrlKey || event.metaKey
      || (event.target as HTMLElement | null)?.closest('input,textarea,select,[contenteditable]')) return
    const handlers: Record<string, () => void> = {
      ArrowLeft: () => event.altKey ? candle(-1) : void move(event.shiftKey ? 'previousPosition' : 'previous'),
      ArrowRight: () => event.altKey ? candle(1) : void move(event.shiftKey ? 'nextPosition' : 'next'),
      Home: () => void move('first'), End: () => void move('last'),
      ' ': () => setPlaying(v => !v), c: () => selected && focus(selected.ms), f: () => fitLife(),
      Escape: () => { setShortcuts(false); setSettings(false) },
    }
    const handler = handlers[event.key.length === 1 ? event.key.toLowerCase() : event.key]
    if (handler) { event.preventDefault(); handler() }
  }
  useEffect(() => {
    const listener = (event: KeyboardEvent) => keys.current(event)
    window.addEventListener('keydown', listener)
    return () => window.removeEventListener('keydown', listener)
  }, [])

  function sort(column: keyof WatchRow) { replace({ sort: column, descending: prefs.sort !== column || !prefs.descending }) }
  const placements = [prefs.watch && 'has-watch', prefs.inspector && 'has-inspector'].filter(Boolean).join(' ')
  const navButton = (title: string, child: ReactNode, action: () => void, enabled = ready) =>
    <button type="button" title={title} aria-label={title} onClick={action} disabled={!enabled}>{child}</button>
  const alert = notice || windowResult?.error
  const candleWarnings = chartData?.candles.rows.filter(row => row.status >= 2).length || 0
  const coveredWindow = meta.order_coverage?.some(([lo, hi]) => lo <= view.start && hi >= Math.min(view.end, clock ?? view.end))

  return <section className={`replay-desk ${prefs.light ? 'light' : ''} ${expanded ? 'full' : ''}`} aria-label="Symbol replay workstation">
    <div className="replay-top">
      <div className="replay-product"><i /><strong>{symbol}</strong><span>{isExternal ? 'Binance · Public archive' : 'Native simulation'}</span></div>
      <div className="replay-segments" role="group" aria-label="Replay mode">
        <button className={!trueMode ? 'on' : ''} aria-pressed={!trueMode} onClick={() => { setCursor(undefined); replace({ mode: 'analysis' }) }}>Analysis</button>
        <button className={trueMode ? 'on' : ''} aria-pressed={trueMode} onClick={() => { setCursor(undefined); replace({ mode: 'true' }) }}>True replay</button>
      </div>
      <div className="replay-utils">
        {isExternal && navButton('Refresh Binance archive', <RefreshCcw size={15} />, refresh, true)}
        {navButton('Keyboard shortcuts', <Keyboard size={16} />, () => setShortcuts(!shortcuts), true)}
        {navButton(prefs.light ? 'Dark mode' : 'Light mode', prefs.light ? <Moon size={15} /> : <Sun size={15} />, () => replace({ light: !prefs.light }), true)}
        {navButton(expanded ? 'Exit fullscreen' : 'Fullscreen workspace', expanded ? <Minimize2 size={15} /> : <Maximize2 size={15} />,
          () => { setExpanded(!expanded); route({ expanded: expanded ? null : 1 }) }, true)}
      </div>
    </div>
    <div className={`replay-body ${placements}`}>
      {prefs.watch && <aside className="replay-watch" aria-label="Symbol watchlist">
        <header><strong>Watchlist</strong><select aria-label="Watchlist period" value={prefs.period} onChange={e => replace({ period: e.target.value })}>
          <option value="1h">1h</option><option value="24h">24h</option><option value="7d">7d</option><option value="research">Research period</option>
        </select></header>
        <div className="replay-search"><input aria-label="Search symbols" placeholder="Find symbol" value={search} onChange={e => setSearch(e.target.value)} />
          <button title="Most active symbols" aria-label="Most active symbols" onClick={() => { setSearch(''); replace({ sort: 'count', descending: true }) }}><ArrowDownUp size={14}/></button></div>
        <div className="replay-watch-caption"><span>Market / Own volume · USDT</span>
          <small>{watchResult?.value ? utc(watchResult.value.end, true).slice(5) + ' UTC' : 'Loading ranks'}</small></div>
        <div className="replay-watch-table">
          <table><thead><tr>{([['symbol', 'Symbol'], ['market', 'Market'], ['executed', 'Own'], ['count', 'Fills'],
            ['positions', 'Pos'], ['pnl', 'P&L'], ['coverage', 'Data']] as [keyof WatchRow, string][]).map(([key, title]) =>
              <th key={key}><button onClick={() => sort(key)} title={`Sort by ${title}`}>{title}{prefs.sort === key ? prefs.descending ? ' ↓' : ' ↑' : ''}</button></th>)}</tr></thead>
            <tbody>{(watchResult?.value ? watchRows : (trueMode && isExternal ? [symbol] : meta.symbols).filter(s => s.toLowerCase().includes(search.toLowerCase())).map(s => (
              { symbol: s, market: null, pnl: null, coverage: 0, executed: 0, count: 0, positions: 0 }
            ))).map(row => <tr key={row.symbol} className={row.symbol === symbol ? 'selected' : ''} onClick={() => pickSymbol(row.symbol)}>
              <td><button onClick={() => pickSymbol(row.symbol)} aria-current={row.symbol === symbol}>{row.symbol}</button></td>
              <td>{compact(row.market)}</td><td>{compact(row.executed)}</td><td>{compact(row.count)}</td><td>{compact(row.positions)}</td>
              <td className={Number(row.pnl) > 0 ? 'gain' : Number(row.pnl) < 0 ? 'loss' : ''}>{compact(row.pnl)}</td>
              <td title={row.coverage ? `${Math.round(row.coverage * 100)}% of requested five-second bars are archived` : 'No audited market coverage'}>
                <span className={`coverage-dot ${row.coverage > .98 ? 'good' : row.coverage ? 'some' : ''}`} /></td>
            </tr>)}</tbody></table>
          {watchResult?.error && <small className="replay-warning">{watchResult.error}</small>}
        </div>
      </aside>}

      <div className="replay-center">
        <div className="replay-chart-tools">
          <select aria-label="Chart timeframe" value={prefs.tf} onChange={e => resolution(e.target.value as Timeframe)}>
            {Object.keys(TIMEFRAMES).map(tf => <option key={tf}>{tf}</option>)}
          </select>
          <button title="Toggle logarithmic price scale" aria-pressed={prefs.log} onClick={() => replace({ log: !prefs.log })}>Log</button>
          <button title="Toggle marker labels" aria-pressed={prefs.labels} onClick={() => replace({ labels: !prefs.labels })}>Labels</button>
          {navButton('Center on selected execution', <Crosshair size={14} />, () => selected && focus(selected.ms), !!selected)}
          {navButton('Fit current position', <Expand size={14} />, () => fitLife(), !!visibleSelection?.position_info)}
          <button title="Fit visible executions" onClick={fitExecutions}>Fit fills</button>
          {navButton('Reset viewport', <RotateCcw size={14} />, () => range(defaultRange(selected?.ms)), true)}
          <div className="replay-push" />
          <button aria-label="Chart settings" aria-expanded={settings} onClick={() => setSettings(!settings)}><Settings2 size={15} /> Panels</button>
          <button aria-label="Toggle execution ledger" aria-pressed={prefs.ledger} onClick={() => replace({ ledger: !prefs.ledger })}><List size={15} /></button>
        </div>
        {settings && <div className="replay-settings">
          <section><strong>Panels</strong>{Object.entries(optionNames).map(([key, label]) => <label key={key}><input type="checkbox"
            checked={prefs.panels[key as PanelId]} onChange={e => replace({ panels: { ...prefs.panels, [key]: e.target.checked } })} />{label}</label>)}</section>
          <section><strong>Price overlays</strong>{([
            ['volume', 'Volume'], ['entry', 'Weighted average entry'], ['initial', 'Initial entry'], ['breakeven', 'Break-even · known costs'],
            ['features', 'Recorded strategy features'], ['connections', 'Lifecycle connections'],
          ] as [keyof ChartPrefs, string][]).map(([key, title]) => <label key={key}><input type="checkbox" checked={Boolean(prefs[key])}
            onChange={e => replace({ [key]: e.target.checked })} />{title}</label>)}</section>
          <section><strong>Workspace</strong>{([
            ['watch', 'Watchlist'], ['inspector', 'Execution inspector'], ['marked', 'Inventory in marked USDT'],
            ['net', 'Net realized P&L'],
          ] as [keyof Prefs, string][]).map(([key, title]) => <label key={key}><input type="checkbox" checked={Boolean(prefs[key])}
            onChange={e => replace({ [key]: e.target.checked })} />{title}</label>)}</section>
        </div>}
        {alert && <div className="replay-alert" role="status">{alert}</div>}
        <div className="replay-chart-host">
          <ReplayChart symbol={symbol} data={chartData} selected={visibleSelection} visible={view} step={step} options={prefs} cursor={cursor}
            onPick={id => void move('select', { id })} onWindow={range}
            onSizes={heights => put(old => Object.keys(heights).some(key => old.heights[key as PanelId] !== heights[key as PanelId]) ? { ...old, heights } : old)} />
          {!windowResult && !chartData && <span className="replay-loading">Loading market window…</span>}
        </div>
        <div className="replay-chart-foot">
          <span>{utc(view.start, true)} → {utc(view.end, true)} UTC</span>
          {candleWarnings > 0 && <span className="replay-warning">{candleWarnings} unavailable buckets</span>}
          {!!chartData && chartData.marker_total > chartData.markers.length && <span className="replay-warning">Dense window · zoom for remaining fills</span>}
          {!isExternal && <span>Audited five-second market data</span>}
          {isExternal && <span className={coveredWindow ? '' : 'replay-warning'}>{coveredWindow ? 'Order window verified' : 'Public order gaps possible'}</span>}
          {isExternal && !!chartData?.reconstruction?.orders && (() => {
            const info = chartData.reconstruction
            const unknown = (counts: Record<string, number>) => counts.unknown || 0
            const details = [
              `Actions: ${info.actions.confirmed || 0} confirmed · ${info.actions.inferred || 0} inferred · ${unknown(info.actions)} unknown`,
              `Inventory: ${info.quantity.confirmed || 0} confirmed · ${info.quantity.inferred || 0} inferred · ${unknown(info.quantity)} unknown`,
              ...Object.entries(info.action_issues).map(([reason, count]) => `${reason}: ${count} fills`),
              ...Object.entries(info.inventory_issues).map(([reason, count]) => `${reason}: ${count} fills`),
            ]
            return <details className="replay-coverage">
              <summary data-testid="reconstruction-coverage">Actions {info.orders - unknown(info.actions)}/{info.orders} · Inventory {info.orders - unknown(info.quantity)}/{info.orders}</summary>
              <div>{details.map(value => <div key={value}>{value}</div>)}</div>
            </details>
          })()}
          {chartData?.features.error && <span className="replay-warning" title={chartData.features.error}>Features unavailable</span>}
        </div>
        <div className="replay-transport" aria-label="Replay navigation">
          <div className="replay-transport-buttons">
            {navButton('First execution', <ChevronsLeft size={15}/>, () => void move('first'))}
            {navButton('Previous position', <span>‹ P</span>, () => void move('previousPosition'))}
            {navButton('Previous candle', <span>‹ C</span>, () => candle(-1), true)}
            {navButton('Previous execution', <ChevronLeft size={17}/>, () => void move('previous'))}
            {navButton(playing ? 'Pause replay' : 'Play replay', playing ? <Pause size={15}/> : <Play size={15}/>, () => setPlaying(!playing))}
            {navButton('Next execution', <ChevronRight size={17}/>, () => void move('next'))}
            {navButton('Next candle', <span>C ›</span>, () => candle(1), true)}
            {navButton('Next position', <span>P ›</span>, () => void move('nextPosition'))}
            {navButton('Last execution', <ChevronsRight size={15}/>, () => void move('last'))}
          </div>
          <span className="replay-counter" data-testid="execution-counter">{matching ?? '—'} / {activeCount || '—'}{trueMode ? ' observed' : ''}</span>
          <select aria-label="Playback speed" value={prefs.speed} onChange={e => replace({ speed: Number(e.target.value) })}>
            {[.25, .5, 1, 2, 4].map(speed => <option value={speed} key={speed}>{speed}×</option>)}
          </select>
          <form onSubmit={chooseTime} className="replay-jump"><input value={jump} onChange={e => setJump(e.target.value)}
            aria-label="Jump to timestamp, execution or position ID" placeholder="UTC time / execution ID / position:ID" /><button>Jump</button></form>
        </div>
        <input className="replay-seek" type="range" aria-label="Seek execution index" min={1} max={Math.max(1, activeCount)}
          value={seek != null && seek < 200_000 ? seek : matching || 1} disabled={activeCount < 2}
          onChange={e => setSeek(Number(e.target.value))} onPointerUp={event => {
            const value = Number(event.currentTarget.value)
            setSeek(undefined)
            void move('rank', { rank: value - 1 })
          }} onKeyUp={e => { if (e.key.startsWith('Arrow')) { void move('rank', { rank: Number(e.currentTarget.value) - 1 }); setSeek(undefined) } }} />
      </div>
      {prefs.inspector && <Inspector selected={visibleSelection} symbol={symbol} isExternal={isExternal} />}
    </div>
    {prefs.ledger && ready && <TapeLedger base={base} symbol={symbol} selected={visibleSelection} clock={clock} filterScope={filterScope}
      filters={filters} setFilters={setFilters} revision={meta.revision} tab={actualTab} setTab={setActualTab} height={prefs.ledgerHeight}
      onHeight={ledgerHeight => replace({ ledgerHeight })} pick={id => void move('select', { id })}
      pickLife={id => void move('select', { position: id })} />}
    {!!chartData?.features.features.length && <details className="replay-provenance">
      <summary>Feature definitions and provenance</summary>
      <pre>{JSON.stringify(chartData.features.features, null, 2)}</pre>
    </details>}
    {shortcuts && <div className="replay-shortcuts" role="dialog" aria-modal="false" aria-label="Replay keyboard shortcuts">
      <header><strong>Keyboard shortcuts</strong><button onClick={() => setShortcuts(false)}>Close</button></header>
      {Object.entries({ '← / →': 'Previous / next execution', 'Shift + ← / →': 'Previous / next position',
        'Alt + ← / →': 'Previous / next candle', Space: 'Play / pause', 'Home / End': 'First / last execution',
        C: 'Center execution', F: 'Fit position', Escape: 'Dismiss popup' }).map(([k, value]) => <div key={k}><kbd>{k}</kbd>{value}</div>)}
    </div>}
  </section>
}

function bounded(view: Window, meta: Window, step: number): Window {
  const width = Math.max(2 * step, Math.min(Math.max(2 * step, meta.end - meta.start), 1450 * step, view.end - view.start))
  const start = Math.round(Math.max(meta.start - step, Math.min(meta.end - width + step, view.start)))
  return { start, end: start + Math.round(width) }
}
function buffered(view: Window, meta: Window, step: number): Window {
  const extra = Math.min(step * 210, Math.max(24 * step, (view.end - view.start) * .28))
  return { start: Math.max(meta.start - step, view.start - extra), end: Math.min(meta.end + step, view.end + extra) }
}

function Inspector({ selected: row, symbol, isExternal }: { selected?: ReplayRow; symbol: string; isExternal: boolean }) {
  const numeric = (k: string) => typeof row?.[k] === 'number' ? Number(row[k]) : undefined
  const info = row?.position_info
  const certainty = (k: string) => { const value = String(row?.[k] || 'unknown'); return value[0].toUpperCase() + value.slice(1) }
  const observations = row?.observations as unknown as Record<string, number> | undefined
  const fields: [string, ReactNode][] = row ? [
    ['Execution ID', <code title={row.id}>{row.id}</code>], ['Lifecycle', row.position as string || '—'],
    ['Timestamp UTC', utc(row.ms)], ['Fill price', precise(row.price)],
    ['Filled quantity', precise(Math.abs(numeric('filled') ?? NaN))], ['Executed USDT', usd(row.filled_notional)],
    ['Inventory before → after', `${precise(row.before)} → ${precise(row.after)}`],
    ['USDT at fill before → after', `${usd(row.before_notional)} → ${usd(row.after_notional)}`],
    ['Average entry before → after', `${precise(row.entry_before)} → ${precise(row.entry_after)}`],
    [isExternal ? 'Reported order P&L' : 'Fill realized · gross', usd(isExternal ? row.reported_pnl : row.realized)],
    [isExternal ? 'Unrealized · marked at fill price' : 'Unrealized at fill', usd(row.unrealized)],
    ...(isExternal ? [
      ['Action confidence', certainty('action_certainty')],
      ['Inventory confidence', certainty('quantity_certainty')],
      ['Average entry confidence', certainty('entry_certainty')],
      ...(row.realizing_direction ? [['Reported P&L relates to', String(row.realizing_direction) + ' exposure']] : []),
    ] as [string, ReactNode][] : []),
    [isExternal ? 'Reported P&L · archived orders' : 'Cumulative realized · gross', usd(isExternal ? row.reported_cumulative : row.gross_cumulative)],
    ['Cumulative realized · net', usd(row.net_cumulative)], ['Fee on fill', usd(row.fee)],
    ['Cumulative funding · symbol', usd(row.funding_cumulative)],
    ['Time in lifecycle at fill', info ? duration((row.ms - info.entry_ms) / 1000) : '—'],
    ['Since prior execution', row.previous_ms != null ? duration((row.ms - row.previous_ms) / 1000) : '—'],
    ['Inventory adjustment', numeric('adjustment_pct') == null ? '—' : usd(row.adjustment_pct) + '%'],
  ] : []
  return <aside className="replay-inspector" role="region" aria-label="Execution detail">
    <header><strong>Execution inspector</strong><span>{symbol}</span></header>
    {row ? <>
      <div className="replay-inspector-action"><span className={row.side === 'buy' ? 'gain' : 'loss'}>{row.side?.toUpperCase()}</span>
        <strong>{actionName(row)}</strong><b>${usd(row.filled_notional)}</b></div>
      <dl>{fields.map(([name, value]) => <div key={name}><dt>{name}</dt><dd>{value}</dd></div>)}</dl>
      <div className="replay-rationale"><strong>Decision reason</strong>
        <p>{row.decision_rationale as string || (row.virtual ? 'End-of-backtest valuation' : 'Unavailable')}</p>
        {observations && <details><summary>Recorded features</summary>
          {Object.entries(observations).map(([name, value]) => <p key={name}>{name} {precise(value)}</p>)}
        </details>}
        <small>{(row.action_evidence || row.provenance) as string}</small>
        {isExternal && !!(row.quantity_evidence || row.inventory_issue) && <p className="muted">{(row.quantity_evidence || row.inventory_issue) as string}</p>}
        {isExternal && !!row.entry_evidence && <p className="muted">{row.entry_evidence as string}</p>}
      </div>
    </> : <p className="replay-inspector-empty">Select a marker or ledger row to inspect a fill.</p>}
  </aside>
}

function TapeLedger({ base, symbol, selected, clock, revision, filterScope, filters, setFilters, tab, setTab,
  height, onHeight, pick, pickLife }: {
  base: string; symbol: string; selected?: ReplayRow; clock?: number; revision?: string;
  filterScope: Record<string, string | number | null>; filters: Filters; setFilters: (f: Filters) => void;
  tab: 'executions' | 'lifecycles'; setTab: (v: 'executions' | 'lifecycles') => void; height: number;
  onHeight: (h: number) => void; pick: (id: string) => void; pickLife: (id: string) => void;
}) {
  const scroller = useRef<HTMLDivElement>(null)
  const [top, setTop] = useState(0)
  const [open, setOpen] = useState(false)
  const [excursions, setExcursions] = useState(false)
  const [measured, setMeasured] = useState(height)
  const pageSize = PAGE
  const first = Math.max(0, Math.floor((top / ROW - 14) / pageSize) * pageSize)
  const q = { ...filterScope, symbol, clock, selected: selected?.id }
  const exec = useResource<Page<ReplayRow>>(tab === 'executions' ? `${base}/executions?${params({ ...q, offset: first, limit: PAGE * 2 })}` : null, revision)
  const lives = useResource<Page<Life>>(tab === 'lifecycles' ? `${base}/lifecycles?${params({ symbol, clock, selected: selected?.id,
    anchor: selected?.position as string | undefined, offset: first, excursions: excursions ? 1 : null })}` : null, revision)
  const page = tab === 'executions' ? exec?.value : lives?.value
  const counts = useRef({ total: 0, key: '' })
  const key = symbol + tab + JSON.stringify(filters)
  if (counts.current.key !== key) counts.current = { total: 0, key }
  if (page) counts.current.total = page.total
  const total = page?.total ?? counts.current.total
  const all = page?.rows || []
  const relative = Math.max(0, Math.floor(top / ROW) - first - 6)
  const rows = all.slice(relative, relative + Math.ceil(measured / ROW) + 14)
  useEffect(() => {
    const node = scroller.current
    if (!node) return
    const observer = new ResizeObserver(() => setMeasured(node.clientHeight))
    observer.observe(node)
    return () => observer.disconnect()
  }, [])
  useEffect(() => {
    const rank = tab === 'executions' ? selected?.rank : lives?.value?.rank
    if (rank == null || !scroller.current) return
    const node = scroller.current
    const target = rank * ROW
    if (target < node.scrollTop || target + ROW > node.scrollTop + node.clientHeight - ROW * 2) {
      node.scrollTop = Math.max(0, target - ROW * 3)
      setTop(node.scrollTop)
    }
  }, [selected?.id, selected?.rank, lives?.value?.rank, tab, total])
  function resize(event: React.PointerEvent<HTMLDivElement>) {
    const anchor = event.clientY
    const target = event.currentTarget
    target.setPointerCapture(event.pointerId)
    const move = (e: PointerEvent) => onHeight(Math.max(120, Math.min(950, height - (e.clientY - anchor))))
    const release = () => { target.removeEventListener('pointermove', move); target.removeEventListener('pointerup', release) }
    target.addEventListener('pointermove', move)
    target.addEventListener('pointerup', release)
  }
  const filter = (key: string, value: string) => {
    setFilters({ ...filters, [key]: value })
    if (scroller.current) scroller.current.scrollTop = 0
    setTop(0)
  }
  return <section className="replay-ledger" aria-label="Execution ledger">
    <div className="replay-ledger-resize" onPointerDown={resize} role="separator" aria-label="Resize ledger" />
    <header><div className="replay-segments">
      <button className={tab === 'executions' ? 'on' : ''} onClick={() => { setTop(0); setTab('executions') }}>Executions</button>
      <button className={tab === 'lifecycles' ? 'on' : ''} onClick={() => { setTop(0); if (scroller.current) scroller.current.scrollTop = 0; setTab('lifecycles') }}>Positions</button>
    </div><span>{compact(total)} {tab === 'executions' ? 'fills' : 'lifecycles'}</span>
      {tab === 'lifecycles' && clock == null && !base.endsWith('/binance') && <button aria-pressed={excursions} onClick={() => setExcursions(!excursions)}>Mark excursions</button>}
      {tab === 'lifecycles' && clock == null && !base.endsWith('/binance') && <a href={`/api${base.replace('/replay/', '/explorer/')}/positions?${params({ symbol, format: 'csv' })}`}>CSV</a>}
      <button onClick={() => setOpen(!open)} aria-expanded={open}><Filter size={14}/> Filters</button></header>
    {open && <div className="replay-filters">
      <label>Execution type<select value={filters.action || ''} onChange={e => filter('action', e.target.value)}>
        <option value="">All actions</option>{Object.entries({ long_entry: 'Open long', short_entry: 'Open short', scale_in: 'Add', scale_out: 'Reduce',
          long_exit: 'Close long', short_exit: 'Close short', reversal: 'Reverse', terminal_close: 'Virtual close', realized: 'Unknown action · reported P&L', unclassified: 'Unknown action' })
          .map(([v, label]) => <option value={v} key={v}>{label}</option>)}</select></label>
      <label>Direction<select disabled={clock != null} value={filters.direction || ''} onChange={e => filter('direction', e.target.value)}>
        <option value="">Both</option><option value="long">Long</option><option value="short">Short</option></select></label>
      <label>Lifecycle result<select disabled={clock != null} value={filters.outcome || ''} onChange={e => filter('outcome', e.target.value)}>
        <option value="">All outcomes</option><option value="win">Profitable</option><option value="loss">Losing</option></select></label>
      {Object.entries({ minSize: 'Min fill USDT', maxSize: 'Max fill USDT', minPosition: 'Min inventory USDT',
        minMinutes: 'Min hold minutes', minAdds: 'Min adds', minReduces: 'Min reductions' }).map(([key, label]) =>
          <label key={key}>{label}<input type="number" min="0" aria-label={label} value={filters[key] || ''}
            disabled={clock != null && ['minMinutes', 'minAdds', 'minReduces'].includes(key)} onChange={e => filter(key, e.target.value)} /></label>)}
      {['from', 'to'].map(key => <label key={key}>{key === 'from' ? 'From UTC' : 'To UTC'}<input type="datetime-local"
        value={filters[key] || ''} onChange={e => filter(key, e.target.value)} /></label>)}
    </div>}
    <div ref={scroller} className="replay-ledger-scroll" style={{ height }} onScroll={e => setTop(e.currentTarget.scrollTop)}>
      <table key={tab}>
        <thead><tr>{(tab === 'executions' ? ['Time · UTC', 'Action', 'Side', 'Price', 'Quantity', 'Notional', 'After', 'Realized P&L']
          : ['Position', 'Side', 'Entry · UTC', 'Exit · UTC', 'Peak USDT', 'P&L', 'MAE / MFE', 'Fills']).map(title => <th key={title}>{title}</th>)}</tr></thead>
        <tbody>
          <tr className="spacer"><td colSpan={8} style={{ height: (first + relative) * ROW }} /></tr>
          {tab === 'executions' ? (rows as ReplayRow[]).map(row => <tr key={row.id} className={row.id === selected?.id ? 'selected' : ''}
            aria-selected={row.id === selected?.id} onClick={() => pick(row.id)}>
            <td><button onClick={e => { e.stopPropagation(); pick(row.id) }}>{utc(row.ms)}</button></td><td>{actionName(row)}</td>
            <td className={row.side === 'buy' ? 'gain' : 'loss'}>{row.side?.toUpperCase()}</td><td>{precise(row.price)}</td>
            <td>{precise(row.filled)}</td><td>{usd(row.filled_notional)}</td><td>{precise(row.after)}</td>
            <td>{usd(row.realized ?? row.reported_pnl)}</td></tr>)
            : (rows as Life[]).map(row => <tr key={row.id} className={row.id === selected?.position ? 'selected' : ''} onClick={() => pickLife(row.id)}>
              <td><button disabled={row.state === 'reported only'} title={row.provenance}
                onClick={e => { e.stopPropagation(); pickLife(row.id) }}>{row.id}</button></td><td>{row.side}{row.state === 'reported only' ? ' · report' : ''}</td>
              <td>{utc(row.entry_ms)}</td><td>{utc(row.exit_ms)}</td><td>{usd(row.max_notional)}</td>
              <td>{usd(row.net_pnl ?? row.reported_pnl)}</td><td>{usd(row.mae)} / {usd(row.mfe)}</td><td>{row.executions ?? '—'}</td></tr>)}
          <tr className="spacer"><td colSpan={8} style={{ height: Math.max(0, total - first - relative - rows.length) * ROW }} /></tr>
        </tbody>
      </table>
      {!total && <span className="replay-empty">{(exec?.error || lives?.error) || (exec?.value || lives?.value ? 'No matching records.' : 'Loading…')}</span>}
    </div>
  </section>
}
