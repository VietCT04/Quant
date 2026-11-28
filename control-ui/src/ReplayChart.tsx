import { useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react'
import {
  BaselineSeries, CandlestickSeries, ColorType, CrosshairMode, HistogramSeries, LineSeries, LineType,
  PriceScaleMode, createChart, type IChartApi, type ISeriesApi, type LineData, type Time,
} from 'lightweight-charts'
import { ReplayMarkers, BUY, SELL, actionName, bucket, chartTime, markerLabel } from './ReplayMarkers'
import { ReplaySteps } from './ReplaySteps'
import { utc, type Candle, type Candles, type Execution, type Features, type Points, type Window } from './explorerTypes'

export type ReplayWindow = { candles: Candles; markers: Execution[]; marker_total: number; series: Points; features: Features & { error?: string } }
export type PanelId = 'inventory' | 'unrealized' | 'realized' | 'signals' | 'equity' | 'funding'
export type ChartPrefs = {
  light: boolean; log: boolean; labels: boolean; volume: boolean; marked: boolean; net: boolean; entry: boolean;
  initial: boolean; features: boolean; breakeven: boolean; connections: boolean; panels: Record<PanelId, boolean>; heights: Partial<Record<PanelId, number>>
}
type Props = {
  symbol: string; data?: ReplayWindow; selected?: Execution; cursor?: number; visible: Window; options: ChartPrefs; step: number;
  onPick: (id: string) => void; onWindow: (view: Window) => void; onSizes: (items: ChartPrefs['heights']) => void
}
type PaneLine = ISeriesApi<'Line'> | ISeriesApi<'Baseline'>
type Plot = { key: string; pane: PanelId; label: string; color: string; step?: boolean; baseline?: boolean }
type Runtime = {
  chart: IChartApi; price: ISeriesApi<'Candlestick'>; volume: ISeriesApi<'Histogram'>
  anchor: ISeriesApi<'Line'>; markers: ReplayMarkers
  panes: Map<PanelId, { title: HTMLElement; pane: ReturnType<IChartApi['addPane']> }>
  lines: Map<string, PaneLine>; overlays: Map<string, ISeriesApi<'Line'>>
  steps: Map<string, ReplaySteps>
}
const lineData = (points: Points[string]) => points?.map(([ms, value]) => ({
  time: chartTime(ms), ...(value != null && Number.isFinite(value) ? { value } : {}),
})) || []
export const precise = (price: unknown) => typeof price === 'number' && Number.isFinite(price)
  ? price.toLocaleString('en-US', { maximumFractionDigits: Math.min(13, Math.max(4, 6 - Math.floor(Math.log10(Math.abs(price) || 1)))) }) : '—'
export const usd = (price: unknown) => typeof price === 'number' && Number.isFinite(price)
  ? price.toLocaleString('en-US', { maximumFractionDigits: 2, minimumFractionDigits: 2 }) : '—'
export const covered = ['Traded', 'No trades', 'Market data unavailable', 'Quarantined', 'Pre-listing']
const emptyMarkers: Execution[] = []

export function ReplayChart(props: Props) {
  const host = useRef<HTMLDivElement>(null)
  const replayCursor = useRef<HTMLDivElement>(null)
  const live = useRef<Runtime | undefined>(undefined)
  const current = useRef(props)
  current.current = props
  const [reading, setReading] = useState<Candle>()
  const [group, setGroup] = useState<{ rows: Execution[]; x: number; y: number; open: boolean }>()
  const { data, options, selected, visible, step } = props
  const lookup = useMemo(() => new Map(data?.candles.rows.map(x => [x.t, x])), [data?.candles.rows])
  const currentLookup = useRef(lookup)
  currentLookup.current = lookup

  useLayoutEffect(() => {
    const root = host.current
    if (!root) return
    const chart = createChart(root, { autoSize: true,
      layout: { attributionLogo: true, fontSize: 11, fontFamily: '"Inter", system-ui, sans-serif',
        panes: { enableResize: true, separatorColor: '#263142', separatorHoverColor: '#536174' } },
      crosshair: { mode: CrosshairMode.Normal, vertLine: { color: '#65778e', width: 1, style: 2 },
        horzLine: { color: '#65778e', width: 1, style: 2 } },
      rightPriceScale: { minimumWidth: 82, scaleMargins: { top: .11, bottom: .10 } },
      timeScale: { timeVisible: true, minBarSpacing: .2, rightOffset: 0, fixLeftEdge: false, fixRightEdge: false },
      localization: { locale: 'en-GB', timeFormatter: (t: Time) => typeof t === 'number' ? utc(t * 1000) + ' UTC' : String(t) },
    })
    const price = chart.addSeries(CandlestickSeries, { upColor: BUY, downColor: SELL, wickUpColor: BUY, wickDownColor: SELL,
      borderVisible: false, priceLineVisible: false, lastValueVisible: false })
    const volume = chart.addSeries(HistogramSeries, { priceScaleId: '', priceFormat: { type: 'volume' },
      priceLineVisible: false, lastValueVisible: false })
    chart.panes()[0].setStretchFactor(5.3)
    volume.priceScale().applyOptions({ scaleMargins: { top: .83, bottom: 0 } })
    const anchor = chart.addSeries(LineSeries, { lineVisible: false, lastValueVisible: false,
      priceLineVisible: false, crosshairMarkerVisible: false })
    const markers = new ReplayMarkers(root)
    anchor.attachPrimitive(markers)
    const instance: Runtime = { chart, price, volume, anchor, markers, lines: new Map(), panes: new Map(), overlays: new Map(), steps: new Map() }
    live.current = instance
    const refreshCursor = () => {
      const element = replayCursor.current
      const stamp = current.current.cursor ?? current.current.selected?.ms
      const view = chart.timeScale().getVisibleRange()
      if (!element || stamp == null || typeof view?.from !== 'number' || typeof view.to !== 'number') return
      const x = chart.timeScale().timeToCoordinate(chartTime(bucket(stamp, current.current.step)))
      element.hidden = x == null || x < 0 || x >= root.clientWidth - 82
      if (x != null) element.style.transform = `translateX(${x}px)`
      root.dataset.viewport = JSON.stringify({ from: view.from, to: view.to, spacing: chart.timeScale().options().barSpacing })
    }
    chart.timeScale().subscribeVisibleLogicalRangeChange(refreshCursor)
    let hoverFrame = 0, delay = 0, gesture = false
    chart.subscribeClick(event => {
      if (!event.point || event.paneIndex && event.paneIndex !== 0) { setGroup(undefined); return }
      const { x, y } = event.point
      const matches = markers.group(x, y)
      if (matches.length === 1 && matches[0].id) {
        current.current.onPick(matches[0].id)
        setGroup(undefined)
      } else if (matches.length > 1) setGroup({ rows: matches, x, y, open: true })
      else setGroup(undefined)
    })
    chart.subscribeCrosshairMove(event => {
      cancelAnimationFrame(hoverFrame)
      hoverFrame = requestAnimationFrame(() => {
        const stamp = typeof event.time === 'number' ? event.time * 1000 : undefined
        setReading(stamp != null ? currentLookup.current.get(stamp) : undefined)
        const point = event.point
        const matches = point && !event.paneIndex ? markers.group(point.x, point.y) : []
        setGroup(old => old?.open ? old : matches.length && point ? { rows: matches, x: point.x, y: point.y, open: false } : undefined)
      })
    })
    const notify = () => {
      clearTimeout(delay)
      delay = window.setTimeout(() => {
        if (!gesture) return
        gesture = false
        const view = chart.timeScale().getVisibleRange()
        if (view && typeof view.from === 'number' && typeof view.to === 'number' && view.to > view.from) {
          current.current.onWindow({ start: Math.round(view.from * 1000), end: Math.round(view.to * 1000) })
        }
      }, 160)
    }
    const wheel = () => { gesture = true; notify() }
    const pointerDown = () => { gesture = true }
    const pointerUp = () => {
      if (!gesture) return
      notify()
      const heights: ChartPrefs['heights'] = {}
      for (const [key, { pane }] of instance.panes) heights[key] = pane.getHeight()
      current.current.onSizes(heights)
    }
    const escape = (event: KeyboardEvent) => { if (event.key === 'Escape') setGroup(undefined) }
    root.addEventListener('wheel', wheel, { passive: true })
    root.addEventListener('pointerdown', pointerDown)
    window.addEventListener('pointerup', pointerUp)
    window.addEventListener('keydown', escape)
    return () => {
      cancelAnimationFrame(hoverFrame)
      clearTimeout(delay)
      root.removeEventListener('wheel', wheel)
      root.removeEventListener('pointerdown', pointerDown)
      window.removeEventListener('pointerup', pointerUp)
      window.removeEventListener('keydown', escape)
      chart.remove()
      live.current = undefined
    }
  }, [])

  useLayoutEffect(() => {
    const view = live.current
    if (!view) return
    const { light, log } = options
    const background = light ? '#f8fafc' : '#0e151f'
    const grid = light ? '#e7edf4' : '#1b2635'
    const border = light ? '#d4deec' : '#263142'
    view.chart.applyOptions({ layout: { background: { type: ColorType.Solid, color: background },
      textColor: light ? '#465a71' : '#8fa2b9', panes: { separatorColor: border } },
      grid: { vertLines: { color: grid }, horzLines: { color: grid } },
      rightPriceScale: { borderColor: border },
      timeScale: { borderColor: border, secondsVisible: step < 60_000 },
    })
    view.price.priceScale().applyOptions({ mode: log ? PriceScaleMode.Logarithmic : PriceScaleMode.Normal })
  }, [options.light, options.log, step])

  useLayoutEffect(() => {
    const instance = live.current
    if (!instance) return
    const { chart, price, volume, anchor } = instance
    const candles = data?.candles.rows || []
    setReading(undefined)
    const reference = candles.find(c => c.close)?.close || data?.markers.find(f => f.price)?.price || 1
    const decimals = Math.max(2, Math.min(12, 5 - Math.floor(Math.log10(reference))))
    const priceFormat = { type: 'price' as const, precision: decimals, minMove: 10 ** -decimals }
    price.applyOptions({ priceFormat })
    anchor.applyOptions({ priceFormat })
    const bars = candles.map(row => row.status === 0
      ? { time: chartTime(row.t), open: row.open!, high: row.high!, low: row.low!, close: row.close! }
      : { time: chartTime(row.t) })
    if (data && candles.length) for (let stamp = candles[candles.length - 1].t + step; stamp < data.candles.end; stamp += step) {
      bars.push({ time: chartTime(stamp) }) // Empty time slots maintain the manually chosen viewport during true replay.
    }
    price.setData(bars)
    const plotted = new Map<number, number>()
    for (const row of data?.markers || []) if (typeof row.price === 'number') plotted.set(bucket(row.ms, step), row.price)
    anchor.setData([...plotted].sort((a, b) => a[0] - b[0]).map(([ms, value]) => ({ time: chartTime(ms), value })))
    volume.applyOptions({ visible: options.volume })
    volume.setData(candles.map(row => row.volume == null ? { time: chartTime(row.t) } : {
      time: chartTime(row.t), value: row.volume, color: (row.close || 0) >= (row.open || 0) ? '#23705885' : '#863f4e85',
    }))
    const plots: Plot[] = [
      { key: options.marked ? 'notional' : 'qty', pane: 'inventory', label: options.marked ? 'Signed inventory · marked USDT' : 'Signed inventory · base',
        color: '#6d9deb', step: !options.marked },
      { key: 'unrealized', pane: 'unrealized', label: 'Unrealized P&L · USDT', color: BUY, baseline: true },
      { key: options.net ? 'net' : 'realized', pane: 'realized', label: options.net ? 'Cumulative realized · net USDT' : 'Cumulative realized / reported · USDT',
        color: '#c4a8fb', step: true },
      { key: 'equity', pane: 'equity', label: 'Account equity · USDT', color: '#87aaf5' },
      { key: 'funding', pane: 'funding', label: 'Cumulative funding · USDT', color: '#e0bd7e', step: true },
    ]
    const features = data?.features.features || []
    for (const item of features) if (item.pane === 'signal') plots.push({ key: item.name, pane: 'signals', label: 'Strategy signals', color: item.color })
    const combined = { ...data?.series, ...data?.features.series }
    const names = new Set<PanelId>()
    const keys = new Set<string>()
    for (const plot of plots) if (options.panels[plot.pane]) {
      names.add(plot.pane)
      keys.add(plot.key)
    }
    for (const [id, { title, pane }] of instance.panes) if (!names.has(id)) {
      title.remove()
      const index = pane.paneIndex()
      for (const [key, line] of instance.lines) if (line.getPane().paneIndex() === index) {
        instance.lines.delete(key)
        instance.steps.delete(key)
      }
      if (index > 0) chart.removePane(index)
      instance.panes.delete(id)
    }
    for (const [key, line] of instance.lines) if (!keys.has(key)) {
      chart.removeSeries(line)
      instance.lines.delete(key)
      instance.steps.delete(key)
    }
    for (const plot of plots) {
      if (!names.has(plot.pane)) continue
      let panel = instance.panes.get(plot.pane)
      if (!panel) {
        const pane = chart.addPane(true)
        pane.setStretchFactor((options.heights[plot.pane] || 80) / 80)
        const title = document.createElement('div')
        title.className = 'replay-pane-caption'
        panel = { pane, title }
        instance.panes.set(plot.pane, panel)
      }
      panel.title.textContent = plot.label + (combined[plot.key]?.some(row => row[1] != null) ? '' : ' · unavailable')
      let line = instance.lines.get(plot.key)
      if (!line) {
        const common = { priceLineVisible: false, lastValueVisible: false, lineWidth: 1 as const }
        line = plot.baseline ? chart.addSeries(BaselineSeries, { ...common,
          baseValue: { type: 'price', price: 0 }, topLineColor: BUY, topFillColor1: '#17ae8936',
          topFillColor2: '#17ae8908', bottomLineColor: SELL, bottomFillColor1: '#ed596c08', bottomFillColor2: '#ed596c36',
        }, panel.pane.paneIndex()) : chart.addSeries(LineSeries, { ...common, color: plot.color,
          lineType: plot.step ? LineType.WithSteps : LineType.Simple }, panel.pane.paneIndex())
        if (plot.pane !== 'signals' && plot.pane !== 'equity') line.createPriceLine({
          price: 0, color: '#60748975', lineWidth: 1, lineStyle: 2, axisLabelVisible: false,
        })
        instance.lines.set(plot.key, line)
      }
      line.setData(lineData(combined[plot.key]))
      if (plot.step && 'lineType' in line.options()) {
        const events = combined[plot.key + '_events'] ?? []
        ;(line as ISeriesApi<'Line'>).applyOptions({ lineVisible: !events.length })
        let primitive = instance.steps.get(plot.key)
        if (events.length && !primitive) {
          primitive = new ReplaySteps(plot.color)
          ;(line as ISeriesApi<'Line'>).attachPrimitive(primitive)
          instance.steps.set(plot.key, primitive)
        }
        primitive?.set(events, step)
      }
    }
    const overlays = new Map<string, [string, Points[string] | undefined]>()
    if (options.entry) overlays.set('entry', ['#efb971', combined.entry])
    if (options.initial) overlays.set('initial', ['#d9a0e1', combined.initial])
    if (options.breakeven) overlays.set('breakeven', ['#67baa9', combined.breakeven])
    if (options.features) for (const signal of features.filter(s => s.pane === 'price')) {
      overlays.set(signal.name, [signal.color, combined[signal.name]])
    }
    if (options.connections) for (const [name, points] of Object.entries(combined)) {
      if (name.startsWith('connection:')) overlays.set(name, ['#718bb680', points])
    }
    for (const [name, line] of instance.overlays) if (!overlays.has(name)) {
      chart.removeSeries(line)
      instance.overlays.delete(name)
      instance.steps.delete('price:' + name)
    }
    for (const [name, [color, series]] of overlays) {
      const existing = instance.overlays.get(name)
      const line = existing || chart.addSeries(LineSeries, {
        color, lineWidth: 1, lineType: ['entry', 'breakeven', 'initial'].includes(name) ? LineType.WithSteps : LineType.Simple,
        lineStyle: name === 'entry' ? 2 : 0, priceLineVisible: false, lastValueVisible: false, crosshairMarkerRadius: 2,
      })
      line.applyOptions({ priceFormat })
      instance.overlays.set(name, line)
      if (!existing) {
        const levels = features.find(feature => feature.name === name)?.parameters.thresholds
        if (Array.isArray(levels)) for (const level of levels) {
          if (typeof level === 'number' && Number.isFinite(level)) line.createPriceLine({
            price: level, title: name, color, lineStyle: 2, lineWidth: 1, axisLabelVisible: true,
          })
        }
      }
      line.setData(lineData(series || []) as LineData<Time>[])
      const events = combined[name + '_events'] ?? []
      line.applyOptions({ lineVisible: !events.length })
      let primitive = instance.steps.get('price:' + name)
      if (events.length && !primitive) {
        primitive = new ReplaySteps(color)
        line.attachPrimitive(primitive)
        instance.steps.set('price:' + name, primitive)
      }
      primitive?.set(events, step)
    }
    requestAnimationFrame(() => {
      if (live.current !== instance) return
      let index = 1
      for (const name of names) {
        const pane = instance.panes.get(name)?.pane
        if (pane && pane.paneIndex() !== index) pane.moveTo(index)
        index += 1
      }
      for (const { pane, title } of instance.panes.values()) {
        const element = pane.getHTMLElement()
        if (element && title.parentElement !== element) {
          element.style.position = 'relative'
          element.append(title)
        }
      }
    })
  }, [data, options.marked, options.net, options.panels, options.volume, options.entry, options.initial,
    options.breakeven, options.features, options.connections, step])

  useLayoutEffect(() => {
    const view = live.current
    if (!view) return
    view.markers.update(data?.markers || emptyMarkers, selected?.id, options.labels, options.light, step)
  }, [data?.markers, selected?.id, options.labels, options.light, step])

  useLayoutEffect(() => {
    const element = replayCursor.current
    const stamp = props.cursor ?? selected?.ms
    if (!element) return
    if (stamp == null) { element.hidden = true; return }
    const x = live.current?.chart.timeScale().timeToCoordinate(chartTime(bucket(stamp, step)))
    element.hidden = x == null
    if (x != null) element.style.transform = `translateX(${x}px)`
  }, [props.cursor, selected?.ms, step, data])

  useLayoutEffect(() => {
    if (!data?.candles.rows.length) return
    const scale = live.current?.chart.timeScale()
    if (scale) {
      const last = data.candles.end - step
      const first = data.candles.rows[0].t
      const from = chartTime(Math.max(first, Math.min(last, bucket(visible.start, step))))
      const to = chartTime(Math.min(last, Math.max(first, bucket(visible.end, step))))
      if (from !== to) scale.setVisibleRange({ from, to })
    }
    setGroup(undefined)
  }, [visible.start, visible.end, step, data?.candles.rows])

  const displayed = reading || (selected && lookup.get(bucket(selected.ms, step)))
  return <div className="replay-chart-frame">
    <div className="replay-ohlc" aria-live="off"><strong>{props.symbol}</strong><span>{displayed ? utc(displayed.t) : 'UTC'}</span>
      {displayed?.status === 0 ? <>
        <span>O <b>{precise(displayed.open)}</b></span><span>H <b>{precise(displayed.high)}</b></span>
        <span>L <b>{precise(displayed.low)}</b></span><span>C <b>{precise(displayed.close)}</b></span>
        <span>V <b>{precise(displayed.volume)}</b></span>
      </> : displayed && <span className="replay-warning">{covered[displayed.status]}</span>}
    </div>
    <div className="replay-canvas" ref={host} data-testid="market-chart" aria-label="Market and synchronized inventory charts" />
    <div className="replay-cursor" ref={replayCursor} hidden />
    {group && <div className={'replay-marker-pop ' + (group.open ? 'active' : '')}
      style={{ left: `clamp(6px, ${group.x + 12}px, calc(100% - 222px))`, top: Math.max(30, group.y + 7) }}>
      {group.open ? <><strong>{group.rows.length} overlapping fills</strong>{group.rows.map(row => <button type="button" key={row.id}
        onClick={() => { if (row.id) props.onPick(row.id); setGroup(undefined) }}>
        <span>{actionName(row)}</span><span>{precise(row.price)} · {utc(row.ms).slice(11)}</span>
      </button>)}</> : <span>{group.rows.length > 1 ? `${group.rows.length} fills · click to inspect` : markerLabel(group.rows[0])}</span>}
    </div>}
  </div>
}
