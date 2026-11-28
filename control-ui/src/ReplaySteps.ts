import type { IPrimitivePaneRenderer, ISeriesPrimitive, SeriesAttachedParameter, Time } from 'lightweight-charts'
import type { Point } from './explorerTypes'
import { bucket, chartTime } from './ReplayMarkers'

/** Booked changes at sub-candle time on the same horizontal grid as price. */
export class ReplaySteps implements ISeriesPrimitive<Time> {
  private api?: SeriesAttachedParameter<Time>
  private data: Point[] = []
  private step = 5000
  constructor(private readonly color: string) {}
  attached(api: SeriesAttachedParameter<Time>) { this.api = api }
  detached() { this.api = undefined }
  set(data: Point[], step: number) {
    this.data = data
    this.step = step
    this.api?.requestUpdate()
  }
  autoscaleInfo(start: number, end: number) {
    const series = this.api?.series
    const left = series?.dataByIndex(Math.floor(start), 1)?.time
    const right = series?.dataByIndex(Math.ceil(end), -1)?.time
    if (typeof left !== 'number' || typeof right !== 'number') return null
    let minValue = Infinity, maxValue = -Infinity, prev: number | null = null
    for (const [ms, value] of this.data) {
      if (ms < left * 1000) { prev = value; continue }
      if (ms > right * 1000 + this.step) break
      if (value !== null) { minValue = Math.min(minValue, value); maxValue = Math.max(maxValue, value) }
    }
    if (prev !== null) { minValue = Math.min(minValue, prev); maxValue = Math.max(maxValue, prev) }
    return Number.isFinite(minValue) ? { priceRange: { minValue, maxValue } } : null
  }
  paneViews() { return [this.view] }
  private readonly view = { zOrder: () => 'normal' as const, renderer: () => this.renderer }
  private readonly renderer: IPrimitivePaneRenderer = {
    draw: target => {
      const api = this.api
      if (!api || !this.data.length) return
      const scale = api.chart.timeScale()
      const range = scale.getVisibleRange()
      if (!range || typeof range.from !== 'number' || typeof range.to !== 'number') return
      const lo = range.from * 1000, hi = range.to * 1000 + this.step
      const spacing = scale.options().barSpacing
      target.useMediaCoordinateSpace(({ context: ctx }) => {
        let prior: number | null = null, lastX = 0, lastY: number | null = null
        const left = scale.timeToCoordinate(chartTime(lo)) ?? 0
        const right = (scale.timeToCoordinate(chartTime(hi - this.step)) ?? left) + spacing
        ctx.strokeStyle = this.color
        ctx.lineWidth = 1.2
        ctx.beginPath()
        for (const [stamp, value] of this.data) {
          if (stamp < lo) { prior = value; continue }
          if (stamp > hi) break
          const base = bucket(stamp, this.step)
          const x = (scale.timeToCoordinate(chartTime(base)) ?? (base >= hi ? right : left)) + (stamp - base) / this.step * spacing
          const y = value !== null ? api.series.priceToCoordinate(value) : null
          if (prior !== null && lastY === null) {
            lastY = api.series.priceToCoordinate(prior)
            if (lastY !== null) ctx.moveTo(left, lastY)
          }
          if (lastY !== null) ctx.lineTo(x, lastY)
          if (y !== null) {
            if (lastY === null) ctx.moveTo(x, y)
            else ctx.lineTo(x, y)
          }
          prior = null
          lastY = y
          lastX = x
        }
        if (lastY !== null && lastX < right) ctx.lineTo(right, lastY)
        ctx.stroke()
      })
    },
  }
}
