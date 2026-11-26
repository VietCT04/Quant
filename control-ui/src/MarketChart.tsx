import { useEffect, useRef, useState } from 'react'
import { CandlestickSeries, ColorType, CrosshairMode, HistogramSeries, LineSeries, LineType, createChart, createSeriesMarkers, type IChartApi, type SeriesMarker, type Time, type UTCTimestamp } from 'lightweight-charts'
import { executionLabel, utc, type Candles, type Execution, type Features, type Panels, type Points, type Position, type Series, type Window } from './explorerTypes'
import { num } from './components'

type Props = {
  candles: Candles; features?: Features; series?: Series; events: Execution[]; panels: Panels
  selected?: Execution; position?: Position; expanded: boolean; raisedPane: string; visible: Window;
  onPick: (event: Execution) => void; onWindow: (range: Window) => void;
}
const time = (ms: number): UTCTimestamp => (ms / 1000) as UTCTimestamp
const executionColors: Record<string, string> = {
  long_entry: '#78e2b5', long_exit: '#8bb7e8', short_entry: '#f18d9d', short_exit: '#ebb774',
  terminal_close: '#b9a6f2', reversal: '#c3a2f8', scale_in: '#89d2d7', scale_out: '#9aaafe', rejected: '#e9b77b', funding: '#84cfe8',
}
const markText: Record<string, string> = {
  terminal_close: 'END', long_entry: 'L+', long_exit: 'L−', short_entry: 'S+', short_exit: 'S−', reversal: 'REV',
  scale_in: '+', scale_out: '−', rejected: '×', funding: 'F',
}
const windowOf = (range: { from: Time; to: Time }): Window | null => typeof range.from === 'number' && typeof range.to === 'number'
  ? { start: Math.floor(range.from * 1000), end: Math.ceil(range.to * 1000) } : null

export function MarketChart(props: Props) {
  const ref = useRef<HTMLDivElement>(null)
  const api = useRef<IChartApi | null>(null)
  const latest = useRef(props)
  latest.current = props
  const [hover, setHover] = useState<{ stamp: number; open?: number; high?: number; low?: number; close?: number; volume?: number; status?: number } | null>(null)
  const { candles, features, series, events, panels, selected, position, expanded, raisedPane } = props

  useEffect(() => {
    const element = ref.current
    if (!element) return
    const chart = createChart(element, {
      autoSize: true,
      layout: { background: { type: ColorType.Solid, color: '#13171e' }, textColor: '#99a8bb', attributionLogo: true,
        panes: { separatorColor: '#303845', separatorHoverColor: '#526379', enableResize: true } },
      grid: { vertLines: { color: '#202731' }, horzLines: { color: '#202731' } },
      crosshair: { mode: CrosshairMode.Normal },
      rightPriceScale: { borderColor: '#354052', minimumWidth: 81 },
      timeScale: { borderColor: '#354052', timeVisible: true, secondsVisible: candles.step < 60000,
        rightOffset: 3, minBarSpacing: 0.5 },
      localization: { locale: 'en-GB', timeFormatter: (value: Time) => typeof value === 'number' ? utc(value * 1000) + ' UTC' : String(value) },
    })
    api.current = chart
    const price = chart.addSeries(CandlestickSeries, {
      upColor: '#57c8a0', downColor: '#ea8092', wickUpColor: '#57c8a0', wickDownColor: '#ea8092',
      borderVisible: false, priceLineVisible: false, lastValueVisible: false,
    })
    const lookup = new Map(candles.rows.map(row => [row.t, row]))
    price.setData(candles.rows.map(row => row.status === 0
      ? { time: time(row.t), open: row.open!, high: row.high!, low: row.low!, close: row.close! }
      : { time: time(row.t) }))
    if (panels.volume) {
      const volume = chart.addSeries(HistogramSeries, { priceScaleId: '', priceFormat: { type: 'volume' }, priceLineVisible: false, lastValueVisible: false })
      volume.priceScale().applyOptions({ scaleMargins: { top: 0.83, bottom: 0 } })
      volume.setData(candles.rows.map(row => row.volume != null
        ? { time: time(row.t), value: row.volume, color: (row.close ?? 0) < (row.open ?? 0) ? '#883e50' : '#296e61' }
        : { time: time(row.t) }))
    }
    function addLine(points: Points | undefined, field: string, color: string, pane: number, stepped = false, scale = 'right') {
      if (!points?.[field]?.length) return
      const line = chart.addSeries(LineSeries, { color, lineWidth: 1, lineType: stepped ? LineType.WithSteps : LineType.Simple,
        priceScaleId: scale, priceLineVisible: false, lastValueVisible: false, crosshairMarkerRadius: 3 }, pane)
      line.setData(points[field].map(([stamp, value]) => value == null ? { time: time(stamp) } : { time: time(stamp), value }))
      return line
    }
    if (panels.price) for (const feature of features?.features ?? []) {
      if (feature.pane === 'price') addLine(features?.series, feature.name, feature.color, 0)
    }
    const palette = ['#7de0b6', '#91aaff', '#efc276', '#f090a3', '#aaa2ed', '#71c5dc']
    chart.panes()[0].setStretchFactor(expanded ? 510 : 340)
    let paneIndex = 0
    const paneTitles: Record<string, string> = { signal: 'Signal · fast − slow', exposure: 'Symbol exposure · target / actual',
      pnl: 'Symbol P&L · USDT', portfolio: 'Portfolio equity / exposure / drawdown' }
    const labels: Array<{ name: string; index: number }> = []
    const addPane = (name: string) => {
      const pane = chart.addPane(true)
      pane.setStretchFactor(raisedPane === name ? 260 : 119)
      labels.push({ name, index: ++paneIndex })
      return paneIndex
    }
    if (panels.signal && features?.features.some(feature => feature.pane === 'signal')) {
      const pane = addPane('signal')
      for (const feature of features.features.filter(item => item.pane === 'signal')) {
        const line = addLine(features.series, feature.name, feature.color, pane)
        const thresholds = feature.parameters.thresholds
        if (Array.isArray(thresholds)) for (const threshold of thresholds) {
          if (typeof threshold === 'number') line?.createPriceLine({ price: threshold, color: '#677285', lineWidth: 1, axisLabelVisible: false })
        }
      }
    }
    if (series?.symbol.qty && panels.exposure) {
      const pane = addPane('exposure')
      ;['target', 'notional', 'qty', 'entry'].forEach((field, index) => {
        const key = series.symbol[field + '_steps'] ? field + '_steps' : field
        addLine(series.symbol, key, palette[index], pane, true, index === 2 ? 'left' : index === 3 ? 'entry' : 'right')
      })
    }
    if (series?.symbol.marked_pnl && panels.pnl) {
      const pane = addPane('pnl')
      ;['marked_pnl', 'realized', 'unrealized', 'fees', 'funding'].forEach((field, index) => addLine(series.symbol, field, palette[index], pane))
    }
    if (series && panels.portfolio) {
      const pane = addPane('portfolio')
      ;['equity', 'gross', 'net', 'risk_drawdown'].forEach((field, index) => addLine(series.portfolio, field, palette[index], pane, false, index === 3 ? 'left' : index === 0 ? 'right' : 'exposure'))
    }

    const markersByTime = new Map<number, Execution[]>()
    if (panels.executions) {
      const anchors = chart.addSeries(LineSeries, { lineVisible: false, priceLineVisible: false, crosshairMarkerVisible: false, lastValueVisible: false })
      const data = new Map<number, number>()
      const markers: SeriesMarker<Time>[] = []
      for (const row of events) {
        const value = row.price ?? row.reference ?? row.mark
        if (typeof value !== 'number' || !Number.isFinite(value)) continue
        const clock = candles.rows[0]?.t ?? candles.start
        if (row.ms < clock || row.ms > candles.end + 1000) continue
        const active = selected?.id != null ? selected.id === row.id : selected?.ms === row.ms && selected?.type === row.type
        const inside = position && (row.type == 'funding' || row.filled) && row.ms >= position.entry_ms && row.ms <= (position.exit_ms ?? candles.end + 1000)
        const kind = row.type ?? 'funding'
        data.set(row.ms, value)
        const existing = markersByTime.get(row.ms) ?? []
        existing.push(row)
        markersByTime.set(row.ms, existing)
        markers.push({
          time: time(row.ms), price: value, position: 'atPriceMiddle',
          shape: kind === 'rejected' || kind === 'funding' ? 'circle' : row.side === 'buy' ? 'arrowUp' : 'arrowDown',
          id: row.id ?? `${kind}:${row.ms}`, size: active ? 2 : inside ? 1.5 : 1,
          color: active ? '#ffffff' : executionColors[kind] ?? '#aac0e1',
          text: events.length <= 40 || active || inside ? (markText[kind] ?? '') + (row.partial ? '·P' : '') : undefined,
        })
      }
      anchors.setData([...data].sort((a, b) => a[0] - b[0]).map(([stamp, value]) => ({ time: time(stamp), value })))
      createSeriesMarkers(anchors, markers.sort((a, b) => Number(a.time) - Number(b.time)), { zOrder: 'top' })
    }
    chart.subscribeClick(event => {
      if (typeof event.time !== 'number') return
      const matches = markersByTime.get(Math.round(event.time * 1000))
      const selection = matches?.find(row => row.id === event.hoveredObjectId) ?? matches?.[0]
      if (selection) latest.current.onPick(selection)
    })
    chart.subscribeCrosshairMove(event => {
      if (typeof event.time !== 'number') { setHover(null); return }
      const stamp = Math.round(event.time * 1000)
      const row = lookup.get(Math.floor(stamp / candles.step) * candles.step)
      setHover({ stamp, ...row })
    })

    const labelled = window.requestAnimationFrame(() => {
      for (const { name, index } of labels) {
        const holder = chart.panes()[index]?.getHTMLElement()
        if (!holder) continue
        const tag = document.createElement('span')
        tag.className = 'chart-pane-label'
        tag.textContent = paneTitles[name]
        holder.style.position = 'relative'
        holder.append(tag)
      }
    })
    const current = latest.current.visible
    if (candles.rows.length) chart.timeScale().setVisibleRange({ from: time(current.start), to: time(current.end) })
    const update = () => {
      const range = chart.timeScale().getVisibleRange()
      const mapped = range && windowOf(range)
      if (mapped && mapped.end > mapped.start) latest.current.onWindow(mapped)
    }
    let delay = 0
    const gesture = () => {
      clearTimeout(delay)
      delay = window.setTimeout(update, 280)
    }
    element.addEventListener('wheel', gesture, { passive: true })
    element.addEventListener('pointerup', gesture)
    return () => {
      window.cancelAnimationFrame(labelled)
      clearTimeout(delay)
      element.removeEventListener('wheel', gesture)
      element.removeEventListener('pointerup', gesture)
      chart.remove()
      api.current = null
    }
  }, [candles, features, series, events, panels, selected, position, expanded, raisedPane])

  useEffect(() => {
    if (candles.rows.length) api.current?.timeScale().setVisibleRange({ from: time(props.visible.start), to: time(props.visible.end) })
  }, [candles.rows.length, props.visible.start, props.visible.end])

  const paneCount = Number(panels.signal && !!features?.features.some(feature => feature.pane === 'signal'))
    + Number(!!series?.symbol.qty && panels.exposure) + Number(!!series?.symbol.marked_pnl && panels.pnl)
    + Number(!!series && panels.portfolio)
  return <div className={`market-chart ${expanded ? 'market-chart-expanded' : ''}`}>
    <div className="chart-reading" aria-live="off">
      {hover ? <><span className="mono">{utc(hover.stamp)} UTC</span>{hover.status != null && hover.status > 0 && <span>{['','Confirmed empty','Missing market data','Quarantined data','Pre-listing'][hover.status]}</span>}{hover.close != null && <span>O {num(hover.open, 2)} · H {num(hover.high, 2)} · L {num(hover.low, 2)} · C {num(hover.close, 2)} · V {num(hover.volume, 3)}</span>}</> : <span>UTC · Scroll to zoom · Drag to pan</span>}
    </div>
    <div ref={ref} className="market-canvas" data-testid="market-chart" style={{ height: (expanded ? 510 : 340) + paneCount * 119 + (raisedPane ? 141 : 0) }} aria-label="Candlestick chart and synchronized trading panes" />
  </div>
}

export function MarkerLegend() {
  return <div className="marker-legend">{Object.entries(executionLabel).map(([key, label]) => <span key={key}><i style={{ color: executionColors[key] }}>{markText[key]}</i>{label}</span>)}<span>·P Partial fill</span></div>
}
