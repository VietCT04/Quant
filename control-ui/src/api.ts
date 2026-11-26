import { useCallback, useEffect, useState } from 'react'

export type State = 'queued' | 'running' | 'blocked' | 'completed' | 'failed' | 'cancelled'
export type Hypothesis = {
  id: string; name: string; family: string; stage: string; state: State; priority: number
  counts: Partial<Record<State, number>>; total: number; finished: number; dependencies: string[]
  execution_seconds: number; active_attempt: boolean; timing_sampled_at: string; cancelling: number;
  created: string; first_started: string | null; last_attempt: string | null; last_result: string | null
  verdict: string | null; verdict_note: string | null; revision: number; unreviewed: boolean;
  errors: { type: string; message?: string; name?: string; request_id?: string }[]
}
export type ResearchEvent = { id: number; at: string; type: string; hypothesis: string | null; job: string | null }
export type Budgets = { paused: boolean; cpu_limit: number; memory_budget_gib: number; disk_budget_gib: number }
export type Coverage = { symbols?: number; interval_ms?: number; source?: string; error?: string; planned_coverage?: number }
export type Service = {
  ready: boolean; stopping: boolean; worker_error: string | null; budgets: Budgets
  hardware: { usable_cpus: number; physical: number }
  worker: { phase: string; jobs?: string[]; profile?: Record<string, number>; bars_per_second?: number; range?: [number, number]; through_ms?: number }
  last_batch: Record<string, number> | null
  resources: Record<string, number>
  coverage?: Coverage
}
export type Capability = {
  id: string; status: string; created: string; experiment_ids: string[]
  request: { name: string; module: string; purpose: string; specification: string; required_inputs: string[]; parameters: Record<string, unknown>; acceptance_criteria: string[]; causal: boolean; streaming_required: boolean; output_units: string; missing_data: string; warmup: string }
  review: { reviewer: string; findings: string; commit?: string } | null
  review_history: { status: string; created: string; reviewer: string; findings: string }[]
}
export type Campaign = { id: string; name: string; created: string }
export type Snapshot = { cursor: number; hypotheses: Hypothesis[]; events: ResearchEvent[]; service: Service; requests: Capability[]; campaigns: Campaign[]; coverage: Coverage | null }
export type Trial = {
  id: string; kind: string; status: State; artifact_urls: string[]
  specification: { parameters: Record<string, unknown>; evaluation: Record<string, unknown>; costs: Record<string, unknown>; seed: number; start_ms: number; end_ms: number; universe: string[]; dataset_fingerprint: string; git: { commit: string; dirty: boolean } }
  attempts: { attempt: number; started: string; ended: string | null; failure: { type: string; message?: string } | null }[]
  outcome: { metrics: Record<string, unknown>; failure: { type: string; message?: string } | null; warnings: string[] } | null
}
export type Detail = Omit<Hypothesis, 'definition'> & {
  definition: { mechanism: string; definition: string; prediction: string; parameters?: Record<string, unknown>; capabilities?: { name: string }[]; protocol: Trial['specification']; audit_of?: string }
  verdict_history: { at: string; verdict: string; note: string; result_revision: number }[]
  experiments?: Trial[]
}

export async function api<T>(path: string, body?: unknown, signal?: AbortSignal): Promise<T> {
  const response = await fetch('/api' + path, {
    signal,
    ...(body === undefined ? {} : {
      method: 'POST', body: JSON.stringify(body),
      headers: { 'Content-Type': 'application/json', 'X-Quant-Client': 'control' },
    }),
  })
  if (!response.ok) {
    const error = await response.json().catch(() => null)
    throw new Error(error?.error || `Request failed (${response.status})`)
  }
  return response.json()
}

export function useControl() {
  const [data, setData] = useState<Snapshot | null>(null)
  const [connected, setConnected] = useState(false)
  const [error, setError] = useState('')
  const [generation, setGeneration] = useState(0)
  const refresh = useCallback(() => setGeneration(value => value + 1), [])
  useEffect(() => {
    const abort = new AbortController()
    let stream: EventSource | undefined
    let frame = 0
    let patches: Partial<Snapshot>[] = []
    const queue = (patch: Partial<Snapshot>) => {
      patches.push(patch)
      if (frame) return
      frame = requestAnimationFrame(() => {
        frame = 0
        const current = patches
        patches = []
        setData(old => {
          if (!old) return old
          const next = { ...old }
          let hypotheses: Map<string, Hypothesis> | undefined
          for (const update of current) {
            if (update.service) next.service = update.service
            if (update.requests) next.requests = update.requests
            if (update.campaigns) next.campaigns = update.campaigns
            if (update.hypotheses?.length) {
              hypotheses ??= new Map(old.hypotheses.map(item => [item.id, item]))
              for (const item of update.hypotheses) hypotheses.set(item.id, item)
            }
            if (update.events) next.events = [...next.events, ...update.events].slice(-60)
          }
          if (hypotheses) next.hypotheses = [...hypotheses.values()].sort((a, b) => b.priority - a.priority || a.id.localeCompare(b.id, undefined, { numeric: true }))
          return next
        })
      })
    }
    setError('')
    setConnected(false)
    api<Snapshot>('/snapshot', undefined, abort.signal).then(snapshot => {
      if (abort.signal.aborted) return
      setData(snapshot)
      stream = new EventSource(`/api/events?after=${snapshot.cursor}`)
      stream.onopen = () => setConnected(true)
      stream.onerror = () => setConnected(false)
      stream.addEventListener('update', event => queue(JSON.parse(event.data)))
      stream.addEventListener('telemetry', event => queue({ service: JSON.parse(event.data) }))
      stream.addEventListener('reconcile', refresh)
    }).catch(reason => { if (!abort.signal.aborted) setError(reason.message) })
    return () => { abort.abort(); stream?.close(); cancelAnimationFrame(frame) }
  }, [generation, refresh])
  return { data, connected, error, refresh }
}

export function useResource<T>(path: string | null, revision: unknown = 0) {
  const [state, setState] = useState<{ path: string; value?: T; error?: string } | null>(null)
  useEffect(() => {
    if (!path) return
    const abort = new AbortController()
    api<T>(path, undefined, abort.signal)
      .then(value => { if (!abort.signal.aborted) setState({ path, value }) })
      .catch(reason => { if (!abort.signal.aborted) setState({ path, error: reason.message }) })
    return () => abort.abort()
  }, [path, revision])
  return state?.path === path ? state : null
}
