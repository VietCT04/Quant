import type { IPrimitivePaneRenderer, ISeriesPrimitive, SeriesAttachedParameter, Time, UTCTimestamp } from 'lightweight-charts'
import type { Execution } from './explorerTypes'

export const chartTime = (ms: number): UTCTimestamp => (ms / 1000) as UTCTimestamp
export const bucket = (ms: number, step: number) => Math.floor(ms / step) * step
export const BUY = '#17ae89'
export const SELL = '#ed596c'
export const signalColor = (row: Execution) => row.side === 'buy' ? BUY : SELL

export function actionName(row: Execution) {
  const side = row.type === 'scale_out' || row.type === 'terminal_close' ? row.before : row.after
  const direction = typeof side === 'number' && side !== 0 ? side < 0 ? 'SHORT' : 'LONG'
    : typeof row.direction === 'string' ? row.direction.toUpperCase() : ''
  const transaction = row.side?.toUpperCase() || 'SIDE UNAVAILABLE'
  return ({
    long_entry: 'OPEN LONG', short_entry: 'OPEN SHORT', long_exit: 'CLOSE LONG', short_exit: 'CLOSE SHORT',
    scale_in: `ADD ${direction}`, scale_out: `REDUCE ${direction}`, reversal: 'REVERSE',
    terminal_close: `VIRTUAL CLOSE ${direction}`, realized: transaction, unclassified: transaction,
  })[row.type || ''] || transaction
}

export const markerLabel = (row: Execution) => actionName(row) + (typeof row.filled_notional === 'number'
  ? ' $' + row.filled_notional.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 }) : '')

type Point = { x: number; y: number; row: Execution; label?: [number, number, number, string] }

export class ReplayMarkers implements ISeriesPrimitive<Time> {
  private api?: SeriesAttachedParameter<Time>
  private rows: Execution[] = []
  private pixels: Point[] = []
  private selected?: string
  private labels = true
  private light = false
  private step = 5000
  private memo = { first: Number.NaN, last: Number.NaN, rows: [] as Execution[] }

  constructor(private readonly inspection: HTMLElement) {}

  attached(api: SeriesAttachedParameter<Time>) { this.api = api }
  detached() { this.api = undefined; this.pixels = [] }
  update(rows: Execution[], selected: string | undefined, labels: boolean, light: boolean, step: number) {
    this.rows = rows
    this.selected = selected
    this.labels = labels
    this.light = light
    this.step = step
    this.memo.first = Number.NaN
    this.api?.requestUpdate()
  }

  private visible(first: number, last: number) {
    if (first === this.memo.first && last === this.memo.last) return this.memo.rows
    let lo = 0, hi = this.rows.length
    while (lo < hi) {
      const mid = (lo + hi) >>> 1
      if (this.rows[mid].ms < first) lo = mid + 1
      else hi = mid
    }
    const rows = []
    for (let i = lo; i < this.rows.length && this.rows[i].ms < last + this.step; ++i) rows.push(this.rows[i])
    this.memo = { first, last, rows }
    return rows
  }

  autoscaleInfo(from: number, to: number) {
    const api = this.api
    if (!api) return null
    const a = api.series.dataByIndex(Math.floor(from), 1)
    const b = api.series.dataByIndex(Math.ceil(to), -1)
    if (typeof a?.time !== 'number' || typeof b?.time !== 'number') return null
    let minValue = Infinity, maxValue = -Infinity
    for (const row of this.visible(a.time * 1000, b.time * 1000)) {
      if (typeof row.price === 'number' && row.price > 0) {
        minValue = Math.min(minValue, row.price)
        maxValue = Math.max(maxValue, row.price)
      }
    }
    return Number.isFinite(minValue) ? { priceRange: { minValue, maxValue }, margins: { above: 8, below: 8 } } : null
  }

  private render = (target: Parameters<IPrimitivePaneRenderer['draw']>[0]) => {
    const api = this.api
    const range = api?.chart.timeScale().getVisibleRange()
    this.pixels = []
    delete this.inspection.dataset.marker
    if (!api || typeof range?.from !== 'number' || typeof range.to !== 'number') return
    const scale = api.chart.timeScale()
    const first = range.from * 1000, last = range.to * 1000
    target.useMediaCoordinateSpace(({ context: ctx, mediaSize: size }) => {
      const points: Point[] = []
      for (const row of this.visible(first, last)) {
        if (typeof row.price !== 'number' || row.price <= 0) continue
        const x = scale.timeToCoordinate(chartTime(bucket(row.ms, this.step)))
        const y = api.series.priceToCoordinate(row.price)
        if (x === null || y === null || x < -14 || x > size.width || y < 0 || y > size.height) continue
        points.push({ x, y, row })
      }
      this.pixels = points
      const active = points.find(p => p.row.id === this.selected)
      if (active) {
        this.inspection.dataset.marker = JSON.stringify({
          id: active.row.id, x: active.x, y: active.y, left: active.x, right: active.x + 13,
          price: active.row.price, priceAtTip: api.series.coordinateToPrice(active.y),
          candle: bucket(active.row.ms, this.step), timeAtTip: Number(scale.coordinateToTime(active.x)) * 1000,
          color: signalColor(active.row),
        })
      }
      const drawArrow = ({ x, y, row }: Point, selected: boolean) => {
        if (selected) {
          ctx.fillStyle = this.light ? 'rgba(30,55,76,.15)' : 'rgba(199,220,246,.18)'
          ctx.beginPath()
          ctx.arc(x + 3, y, 8, 0, Math.PI * 2)
          ctx.fill()
        }
        ctx.strokeStyle = signalColor(row)
        ctx.lineWidth = selected ? 2 : 1.45
        ctx.lineCap = 'butt'
        ctx.lineJoin = 'round'
        ctx.beginPath()
        ctx.moveTo(x + 5, y - 3.2)
        ctx.lineTo(x, y)
        ctx.lineTo(x + 5, y + 3.2)
        ctx.moveTo(x, y)
        ctx.lineTo(x + 13, y)
        ctx.stroke()
      }
      if (this.labels) {
        ctx.font = '600 10px system-ui'
        ctx.textBaseline = 'middle'
        const occupied = new Set<string>()
        for (const point of active ? [active, ...points.filter(p => p !== active)] : points) {
          const text = markerLabel(point.row)
          const width = ctx.measureText(text).width + 8
          let x = point.x + 16
          if (point === active && x + width > size.width) x = Math.max(0, point.x - width - 5)
          if (x + width > size.width) continue
          const firstLane = Math.floor((point.y - 6) / 11)
          const lastLane = Math.floor((point.y + 6) / 11)
          const start = Math.floor(x / 24)
          const end = Math.floor((x + width) / 24)
          const keys = []
          for (let lane = firstLane; lane <= lastLane; ++lane) {
            for (let column = start; column <= end; ++column) keys.push(`${lane}:${column}`)
          }
          if (keys.some(k => occupied.has(k))) continue
          keys.forEach(k => occupied.add(k))
          ctx.fillStyle = this.light ? 'rgba(250,252,255,.94)' : 'rgba(18,26,37,.92)'
          ctx.fillRect(x, point.y - 6, width, 13)
          ctx.fillStyle = this.light ? point.row.side === 'buy' ? '#08735c' : '#af2e46' : signalColor(point.row)
          ctx.fillText(text, x + 4, point.y)
          point.label = [x, point.y, width, text]
        }
      }
      for (const point of points) if (point !== active) drawArrow(point, false)
      if (active) drawArrow(active, true)
    })
  }

  paneViews() { return [this.view] }
  private readonly view = { zOrder: () => 'top' as const, renderer: () => this.renderer }
  private readonly renderer: IPrimitivePaneRenderer = { draw: target => this.render(target) }

  group(x: number, y: number) {
    const near = this.pixels.filter(p => Math.abs(p.y - y) <= 6 && x >= p.x - 5 && x <= p.x + 16)
    if (near.length) return near.sort((a, b) => Math.abs(a.y - y) - Math.abs(b.y - y)).map(p => p.row)
    const label = this.pixels.find(p => p.label && Math.abs(p.label[1] - y) <= 7 && x >= p.label[0] && x <= p.label[0] + p.label[2])
    return label ? [label.row] : []
  }

  hitTest(x: number, y: number) {
    const [row] = this.group(x, y)
    return row?.id ? { externalId: row.id, cursorStyle: 'pointer', zOrder: 'top' as const,
      itemType: 'marker' as const, hitTestPriority: 2 } : null
  }
}
