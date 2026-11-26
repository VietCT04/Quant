import { useEffect, useRef, type ReactNode } from 'react'
import { AlertCircle, Inbox, LoaderCircle, X } from 'lucide-react'
import type { Hypothesis } from './api'

export const num = (v: unknown, decimals = 0) => typeof v === 'number' && Number.isFinite(v) ? v.toLocaleString(undefined, { maximumFractionDigits: decimals }) : '—'
export const pct = (v: unknown) => typeof v === 'number' && Number.isFinite(v) ? `${num(v * 100, 2)}%` : '—'
export const gib = (v: unknown) => typeof v === 'number' && Number.isFinite(v) ? `${num(v / 2 ** 30, 2)} GiB` : '—'
export const clock = (v?: string | null) => v ? new Intl.DateTimeFormat('en-GB', { hour: '2-digit', minute: '2-digit', second: '2-digit', timeZone: 'UTC' }).format(new Date(v)) : '—'
export const duration = (seconds?: number) => {
  if (seconds === undefined || !Number.isFinite(seconds)) return '—'
  const s = Math.max(0, Math.floor(seconds))
  return s < 60 ? `${s}s` : s < 3600 ? `${Math.floor(s / 60)}m ${s % 60}s` : `${Math.floor(s / 3600)}h ${Math.floor(s / 60) % 60}m`
}
export const between = (start?: string | null, end?: string | null) => start ? duration(((end ? Date.parse(end) : Date.now()) - Date.parse(start)) / 1000) : '—'

export function Badge({ value }: { value: string }) {
  return <span className={`badge badge-${value}`}><i />{value}</span>
}

export function Progress({ item }: { item: Hypothesis }) {
  return <div className={`trial-progress progress-${item.state}`} title={`${item.counts.completed ?? 0} completed · ${item.counts.failed ?? 0} failed · ${item.counts.cancelled ?? 0} cancelled`}><progress max={item.total} value={item.finished} aria-label={`${item.finished} of ${item.total} trials finished`} />
    <span><strong>{item.finished}</strong> / {item.total}</span></div>
}

export function Empty({ title, children }: { title: string; children?: ReactNode }) {
  return <div className="empty"><Inbox size={22} /><strong>{title}</strong>{children && <p>{children}</p>}</div>
}

export function Loading() {
  return <div className="empty" role="status"><LoaderCircle className="animate-spin" size={22} /> Loading research data…</div>
}

export function ErrorBox({ children }: { children: ReactNode }) {
  return <div className="error-box" role="alert"><AlertCircle size={17} />{children}</div>
}

export function Modal({ title, close, children, drawer = false }: { title: string; close: () => void; children: ReactNode; drawer?: boolean }) {
  const ref = useRef<HTMLDialogElement>(null)
  useEffect(() => {
    const dialog = ref.current
    dialog?.showModal()
    return () => dialog?.close()
  }, [])
  return <dialog ref={ref} className={drawer ? 'drawer' : 'modal'} aria-label={title} onCancel={event => { event.preventDefault(); close() }}>
    <div className="dialog-heading"><h2>{title}</h2><button type="button" className="icon-button" onClick={close} aria-label="Close"><X size={19} /></button></div>
    <div className="dialog-content">{children}</div>
  </dialog>
}

export function Json({ value }: { value: unknown }) {
  return <pre className="json">{JSON.stringify(value, null, 2)}</pre>
}
