export const TIMEFRAMES = { '5s': 5, '15s': 15, '30s': 30, '1m': 60, '5m': 300, '15m': 900, '1h': 3600, '4h': 14400, '1d': 86400 }
export type Timeframe = keyof typeof TIMEFRAMES
export type Window = { start: number; end: number }
export type Point = [number, number | null, number?, number?]
export type Points = Record<string, Point[]>
export type Candle = { t: number; status: number; available_ms: number; open?: number; high?: number; low?: number; close?: number; volume?: number }
export type Candles = Window & { step: number; rows: Candle[] }
export type Series = { portfolio: Points; symbol: Points; symbol_error?: string }
export type Feature = { name: string; function: string; label: string; parameters: Record<string, unknown>; units: string; pane: 'price' | 'signal'; color: string; provenance: Record<string, unknown> }
export type Features = { features: Feature[]; series: Points }
export type Replay = { hypothesis_id: string; job: string; status: string; failure: { message?: string; type?: string } | null }
export type Meta = Window & { strategy?: { id: string; version: string } | null; job: string; hypothesis_id: string; symbols: string[]; detailed_job: string | null; replay: Replay | null; compatibility: string | null; funding_warnings: unknown[] }
export type Row = Record<string, string | number | boolean | null | undefined>
export type Execution = Row & { ms: number; id?: string; type?: string; side?: string; partial?: boolean; price?: number | null; reference?: number | null; mark?: number | null; symbol_name?: string }
export type Position = Row & { id: string; symbol: string; side: string; state: string; entry_ms: number; exit_ms: number | null; duration_ms: number; net_pnl: number; sampled_ticks: number; held_ticks: number }
export type Page<T> = { rows: T[]; total: number; position?: Position; reconciled?: Record<string, number> }
export type Panels = { price: boolean; volume: boolean; executions: boolean; signal: boolean; exposure: boolean; pnl: boolean; portfolio: boolean }
export type LedgerTab = 'positions' | 'fills' | 'orders' | 'targets' | 'funding' | 'rejected'

export function explorerLink(hypothesis: string, candidate?: string) {
  const query = new URLSearchParams({ hypothesis })
  if (candidate) query.set('candidate', candidate)
  return '#explorer?' + query.toString()
}

export function params(values: Record<string, string | number | null | undefined>) {
  const query = new URLSearchParams()
  for (const [key, value] of Object.entries(values)) if (value !== undefined && value !== null && value !== '') query.set(key, String(value))
  return query.toString()
}

export function utc(ms: unknown, short = false) {
  if (typeof ms !== 'number') return 'Unavailable'
  return new Date(ms).toISOString().replace('T', ' ').replace(short || ms % 1000 === 0 ? /\.\d{3}Z$/ : /Z$/, '')
}

export function timeframe(range: Window): Timeframe {
  return (Object.keys(TIMEFRAMES) as Timeframe[]).find(key => (range.end - range.start) / (1000 * TIMEFRAMES[key]) <= 1500) ?? '1d'
}

export const executionLabel: Record<string, string> = {
  long_entry: 'Long entry', short_entry: 'Short entry', long_exit: 'Long exit', short_exit: 'Short exit',
  scale_in: 'Scale-in', scale_out: 'Scale-out', reversal: 'Reversal', rejected: 'Rejected', funding: 'Funding',
}
