import { useEffect, useState } from 'react'
import { Clock3 } from 'lucide-react'
import { api, useResource, type Detail, type Hypothesis } from './api'
import { Badge, Empty, ErrorBox, Loading } from './components'
import { ReplayDesk, type ReplayMeta } from './ReplayDesk'
import { utc, type Meta } from './explorerTypes'
import './explorer.css'

const initialRoute = () => new URLSearchParams(location.hash.split('?')[1] ?? '')
type Changes = Record<string, string | number | null>

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
  const external = route.get('source') === 'binance'
  const [refresh, bump] = useState(0)
  const archive = useResource<ReplayMeta>(external ? '/replay/binance/meta' : null, refresh)
  const hypotheses = rows.filter(row => row.state === 'completed')
  const selected = route.get('hypothesis') ?? hypotheses.find(row => row.stage !== 'screening')?.id ?? hypotheses[0]?.id ?? ''
  const hypothesis = hypotheses.find(row => row.id === selected)
  const detail = useResource<Detail>(!external && selected ? `/hypotheses/${selected}/results` : null, hypothesis?.revision)
  const candidates = detail?.value?.experiments?.filter(trial => trial.status === 'completed' && ['native', 'audit'].includes(trial.kind)) ?? []
  const job = route.get('candidate') ?? candidates[0]?.id
  const revision = rows.filter(row => row.id === selected || row.stage === 'audit').map(row => `${row.id}:${row.revision}:${row.state}`).join('/')
  return <div className="explorer">
    <section className="panel explorer-pick replay-source"><label>Replay source<select aria-label="Replay source" value={external ? 'binance' : 'native'}
      onChange={e => set(e.target.value === 'binance' ? { source: 'binance' } : {}, true)}>
      <option value="native">Native backtest</option><option value="binance">Binance · 富一次就足够</option>
    </select></label>{external ? <a className="mono candidate-ref" href="https://www.binance.com/en/copy-trading/lead-details/5140488422016171264" target="_blank" rel="noreferrer">Public portfolio 5140488422016171264</a> : <><label>Hypothesis<select aria-label="Explorer hypothesis" value={selected}
      onChange={event => set({ hypothesis: event.target.value }, true)}><option value="" disabled>Select a hypothesis</option>
      {hypotheses.map(row => <option value={row.id} key={row.id}>{row.id} · {row.name}</option>)}</select></label>
      <label>Native candidate<select aria-label="Explorer candidate" value={job ?? ''} disabled={!candidates.length}
        onChange={event => set({ hypothesis: selected, candidate: event.target.value }, true)}><option value="" disabled>Select a candidate</option>
        {candidates.map(item => <option value={item.id} key={item.id}>{item.id.slice(0, 10)} · {item.kind}{item.specification.strategy ? ` · ${item.specification.strategy.id} @ ${item.specification.strategy.version}` : item.specification.parameters.fast != null ? ` · EMA ${item.specification.parameters.fast}/${item.specification.parameters.slow}` : ''}</option>)}</select></label>
      {job && <span className="mono candidate-ref">{job.slice(0, 12)}</span>}</>}
    </section>
    {external ? archive?.error ? <ErrorBox>{archive.error}</ErrorBox> : archive?.value
      ? <ReplayDesk key="binance" meta={archive.value} refresh={() => bump(n => n + 1)} /> : <Loading />
      : !selected ? <section className="panel"><Empty title="No backtests to explore">Completed native candidates appear here.</Empty></section>
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
    <ReplayDesk meta={meta} refresh={() => {}} />
  </>
}
