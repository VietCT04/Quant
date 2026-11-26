import { useState } from 'react'
import { ArrowDown, ArrowDownToLine, ArrowUp, ChevronLeft, ChevronRight, List } from 'lucide-react'
import { useResource } from './api'
import { Badge, duration, Empty, ErrorBox, Loading, num, pct } from './components'
import { executionLabel, params, utc, type Execution, type LedgerTab, type Page, type Position, type Row, type Window } from './explorerTypes'

const tabs: [LedgerTab, string][] = [['positions', 'Positions'], ['fills', 'Fills'], ['orders', 'Requested orders'],
  ['targets', 'Target changes'], ['funding', 'Funding'], ['rejected', 'Rejected orders']]
const pageSize = 50
const positionCols: [string, string][] = [
  ['id', 'Position'], ['symbol', 'Symbol'], ['side', 'Side'], ['state', 'State'], ['entry_ms', 'Entry UTC'], ['exit_ms', 'Exit UTC'],
  ['duration_ms', 'Held'], ['average_entry', 'Avg entry'], ['average_exit', 'Avg exit'], ['opened_qty', 'Opened qty'],
  ['closed_qty', 'Closed qty'], ['max_qty', 'Peak qty'], ['max_notional', 'Peak mark · USDT'],
  ['gross_realized', 'Gross realized'], ['fees', 'Fees'], ['slippage', 'Slippage'], ['funding', 'Funding'],
  ['net_pnl', 'Net realized'], ['unrealized', 'Open P&L'], ['return_on_opening_notional', 'Return / opened'],
  ['executions', 'Fills'], ['mfe', 'MFE'], ['mae', 'MAE'], ['coverage', 'Mark coverage'],
]
const orderCols: [string, string][] = [
  ['ms', 'Booked UTC'], ['symbol_name', 'Symbol'], ['type', 'Execution type'], ['side', 'Side'], ['requested', 'Requested qty'],
  ['rounded', 'Rounded qty'], ['filled', 'Filled qty'], ['price', 'Fill price'], ['reference', 'Reference'], ['fee', 'Fees'],
  ['slippage', 'Slippage'], ['realized', 'Realized P&L'], ['after', 'Position after'], ['reason_label', 'Outcome'],
]
const eventCols: Record<Exclude<LedgerTab, 'positions'>, [string, string][]> = {
  fills: orderCols, orders: orderCols, rejected: orderCols,
  targets: [['ms', 'Decision UTC'], ['symbol_name', 'Symbol'], ['notional', 'Requested target USDT']],
  funding: [['ms', 'Settlement UTC'], ['symbol_name', 'Symbol'], ['qty', 'Held qty'], ['mark', 'Settlement mark'],
    ['rate', 'Funding rate'], ['payment', 'Cash flow USDT']],
}

function label(row: Row, key: string) {
  const value = row[key]
  if (key === 'coverage') return typeof row.held_ticks === 'number' && row.held_ticks > 0 ? pct(Number(row.sampled_ticks) / row.held_ticks) : 'Unavailable'
  if (value == null) return 'Unavailable'
  if (key === 'duration_ms') return duration(Number(value) / 1000)
  if (key === 'ms' || key === 'entry_ms' || key === 'exit_ms') return utc(value)
  if (key === 'return_on_opening_notional' || key === 'rate') return pct(value)
  if (key === 'type') return (executionLabel[String(value)] ?? String(value)) + (row.partial ? ' · Partial' : '')
  if (typeof value === 'number') return num(value, /qty|filled|requested/.test(key) ? 8 : key === 'executions' ? 0 : 4)
  return String(value)
}

export function ExecutionCard({ event, symbol, clear }: { event: Execution; symbol: string; clear: () => void }) {
  const fields: [string, unknown][] = [
    ['Symbol', symbol], ['Side', event.side ?? (event.type === 'funding' ? Number(event.qty) > 0 ? 'long' : 'short' : undefined)],
    ['Booked fill / settlement UTC', utc(event.ms)], ['Decision UTC', utc(event.decision)],
    ['Decision input bar start UTC', utc(event.signal_bar_start)],
    ['Execution interval start UTC', utc(event.bar_start)], ['Execution interval end UTC', utc(event.execution_end)],
    ['Order ID', event.order_id], ['Requested notional ? USDT', event.requested_notional],
    ['Rounded quantity', event.rounded], ['Available buying power ? USDT', event.buying_power],
    ['Reference price', event.reference], ['Modeled fill price', event.price], ['Requested quantity', event.requested],
    ['Filled quantity', event.filled], ['Filled notional · USDT', event.filled_notional], ['Fees · USDT', event.fee],
    ['Spread / slippage / impact · USDT', event.slippage], ['Resulting signed quantity', event.after],
    ['Average entry price', event.entry], ['Gross realized P&L · USDT', event.realized],
    ['Settlement mark', event.mark], ['Funding cash flow', event.payment],
    ['Strategy rationale', event.decision_rationale],
    ['Execution reason', event.reason_label != null ? `${event.reason}: ${event.reason_label}` : undefined],
  ]
  return <section className="panel selection-card" aria-label="Execution detail"><header className="panel-heading">
    <div><h2>{executionLabel[event.type ?? 'funding'] ?? 'Execution'}</h2><p>Native record · {event.id ?? utc(event.ms)}</p></div>
    <button onClick={clear}>Clear execution</button></header>
    <dl className="execution-fields">{fields.map(([key, value]) => <div key={key}><dt>{key}</dt><dd>
      {value == null ? 'Unavailable' : typeof value === 'number' ? num(value, 8) : String(value)}
    </dd></div>)}</dl>
  </section>
}

export function PositionCard({ position, base, symbol, pick, clear, fit }: {
  position: Position; base: string; symbol: string; pick: (row: Execution) => void; clear: () => void; fit: () => void;
}) {
  const [offset, setOffset] = useState(0)
  const resource = useResource<Page<Execution>>(`${base}/positions?${params({ symbol, position: position.id, offset, limit: 25 })}`)
  return <section className="panel selection-card" aria-label="Position detail"><header className="panel-heading">
    <div><h2>{position.id} <Badge value={position.state} /></h2><p>{position.terminal_close ? 'Virtual terminal exit / ' : ''}{position.side} · {utc(position.entry_ms)} UTC → {position.exit_ms ? utc(position.exit_ms) + ' UTC' : 'Open at run end'}</p></div>
    <div className="ledger-actions"><button onClick={fit}>Fit position</button><button onClick={clear}>Clear position</button></div></header>
    <dl className="execution-fields">{positionCols.filter(([field]) => ['net_pnl', 'gross_realized', 'fees', 'slippage', 'funding', 'unrealized', 'max_notional', 'mfe', 'mae', 'coverage'].includes(field))
      .map(([field, title]) => <div key={field}><dt>{title}</dt><dd>{label(position, field)}</dd></div>)}</dl>
    <h3 className="timeline-heading">Execution timeline</h3>
    {resource?.error ? <ErrorBox>{resource.error}</ErrorBox> : !resource ? <Loading /> : <>
      <div className="timeline-list">{resource.value!.rows.map((row, index) => <button key={`${row.ms}-${index}`} onClick={() => pick(row)}>
        <span className="mono">{utc(row.ms)} UTC</span><span>{label(row, 'type')}</span><span>{row.allocation ? `${row.allocation} ${num(row.allocated_qty, 8)}` : `Cash flow ${num(row.payment, 4)}`}</span>
        <span>{row.allocated_fee != null ? `Fee ${num(row.allocated_fee, 6)}` : ''}</span><span>{row.allocated_realized != null ? `Realized ${num(row.allocated_realized, 4)}` : ''}</span>
      </button>)}</div>
      {resource.value!.total > 25 && <Pager offset={offset} limit={25} total={resource.value!.total} set={setOffset} />}
    </>}
  </section>
}

function Pager({ offset, limit, total, set }: { offset: number; limit: number; total: number; set: (offset: number) => void }) {
  return <footer className="table-footer"><span>{total ? `${offset + 1}–${Math.min(total, offset + limit)} of ${total}` : '0 records'}</span><div>
    <button aria-label="Previous page" onClick={() => set(Math.max(0, offset - limit))} disabled={offset === 0}><ChevronLeft size={15} /></button>
    <button aria-label="Next page" onClick={() => set(offset + limit)} disabled={offset + limit >= total}><ChevronRight size={15} /></button></div></footer>
}

export function Ledger({ base, symbol, range, route, set, selected, select, pick }: {
  base: string; symbol: string; range: Window; route: URLSearchParams;
  set: (values: Record<string, string | number | null>) => void;
  selected?: string; select: (position: Position) => void; pick: (event: Execution) => void;
}) {
  const tab = tabs.find(([id]) => id === route.get('tab'))?.[0] ?? 'positions'
  const [offset, setOffset] = useState(0)
  const [expanded, setExpanded] = useState<string | null>(null)
  const side = route.get('side') ?? ''
  const type = route.get('execution') ?? ''
  const state = route.get('state') ?? ''
  const sort = route.get('sort') ?? 'entry_ms'
  const desc = route.get('desc') === '1'
  const onlyRange = route.get('visibleLedger') === '1'
  const filters = params({ symbol, side, type, state: tab === 'positions' ? state : '', sort, desc: desc ? 1 : 0,
    reason: tab === 'positions' ? '' : route.get('reason'), start: onlyRange ? range.start : undefined, end: onlyRange ? range.end : undefined })
  const resource = useResource<Page<Row>>(`${base}/${tab}?${filters}&limit=${pageSize}&offset=${offset}`)
  const change = (values: Record<string, string | number | null>) => { setOffset(0); setExpanded(null); set(values) }
  const cols = tab === 'positions' ? positionCols : eventCols[tab]
  const rows = resource?.value?.rows ?? []
  return <section className="panel explorer-ledger"><header className="panel-heading"><div><h2>Execution ledger</h2>
    <p>Native records · {symbol}</p></div>{tab === 'positions' && <a className="text-button" href={`/api${base}/positions?${filters}&format=csv`} download><ArrowDownToLine size={15} /> Export positions CSV</a>}</header>
    <div className="ledger-toolbar"><div className="segments" aria-label="Ledger table">{tabs.map(([id, title]) => <button key={id} aria-pressed={tab === id}
      onClick={() => change({ tab: id, side: null, state: null, execution: null, reason: null })}>{title}</button>)}</div>
      <div className="ledger-filters"><label className="toggle"><input type="checkbox" checked={onlyRange} onChange={e => change({ visibleLedger: e.target.checked ? 1 : null })} /> Chart window</label>
        <select aria-label="Ledger side" value={side} onChange={e => change({ side: e.target.value })}><option value="">All sides</option>
          {(tab === 'positions' || tab === 'funding' || tab === 'targets' ? ['long', 'short'] : ['buy', 'sell']).map(item => <option key={item}>{item}</option>)}</select>
        {tab === 'positions' ? <select aria-label="Position state" value={state} onChange={e => change({ state: e.target.value })}><option value="">Open and closed</option><option value="closed">Closed only</option><option value="open">Open only</option></select>
          : ['fills', 'orders', 'rejected'].includes(tab) && <>
            <select aria-label="Execution type" value={type} onChange={e => change({ execution: e.target.value })}><option value="">All execution types</option>
              {[...Object.entries(executionLabel).filter(([key]) => key !== 'funding'), ['partial', 'Partial fill']].map(([key, title]) => <option key={key} value={key}>{title}</option>)}</select>
            <select aria-label="Execution outcome" value={route.get('reason') ?? ''} onChange={e => change({ reason: e.target.value })}><option value="">All outcomes</option><option value="0">Filled</option><option value="1">Partial</option><option value="2">Risk limit</option><option value="3">Lot, liquidity, minimum</option></select>
          </>}
      </div>
    </div>
    {resource?.error ? <ErrorBox>{resource.error}</ErrorBox> : !resource ? <Loading /> : rows.length ? <>
      <div className="table-scroll ledger-scroll"><table className="dense-table ledger-table"><thead><tr>{cols.map(([field, title]) =>
        <th scope="col" key={field} aria-sort={tab === 'positions' && sort === field ? desc ? 'descending' : 'ascending' : undefined}>
          {tab === 'positions' && field !== 'coverage' ? <button className="sort" onClick={() => change({ sort: field, desc: sort === field && !desc ? 1 : null })}>{title}{sort === field && (desc ? <ArrowDown size={12} /> : <ArrowUp size={12} />)}</button> : title}</th>)}</tr></thead>
        <tbody>{rows.map((row, index) => {
          const id = String(row.id ?? row.ms) + '-' + index
          return <TableRow key={id} row={row} tab={tab} cols={cols} expanded={expanded === id} active={selected === row.id} toggle={() => setExpanded(expanded === id ? null : id)}
            open={() => tab === 'positions' ? select(row as Position) : pick({ ...row, type: row.type ?? (tab === 'funding' ? 'funding' : 'target') } as Execution)}
            base={base} symbol={symbol} pick={pick} />
        })}</tbody></table></div>
      <Pager offset={offset} limit={pageSize} total={resource.value!.total} set={setOffset} />
    </> : <Empty title="No matching records">Try a different side, time window, or execution outcome.</Empty>}
  </section>
}

function TableRow({ row, tab, cols, active, expanded, open, toggle, base, symbol, pick }: {
  row: Row; tab: LedgerTab; cols: [string, string][]; active: boolean; expanded: boolean;
  open: () => void; toggle: () => void; base: string; symbol: string; pick: (record: Execution) => void;
}) {
  const history = useResource<Page<Execution>>(expanded ? `${base}/positions?${params({ symbol, position: String(row.id), limit: 10 })}` : null)
  return <>
    <tr className={active ? 'selected' : ''}>
      {cols.map(([field], index) => <td key={field} title={label(row, field)} className={['net_pnl', 'gross_realized', 'unrealized'].includes(field) && typeof row[field] === 'number' ? Number(row[field]) < 0 ? 'pnl-loss' : 'pnl-gain' : ''}>
        {index === 0 ? <div className="ledger-row-action"><button className="text-button mono" onClick={open}>{label(row, field)}</button>
          {tab === 'positions' && <button className="icon-button" aria-label={`Expand ${row.id} executions`} aria-expanded={expanded} onClick={toggle}><List size={13} /></button>}</div> : label(row, field)}
      </td>)}</tr>
    {expanded && <tr><td colSpan={cols.length}>{history?.error ? <ErrorBox>{history.error}</ErrorBox> : !history ? <Loading /> :
      <div className="row-expansion">{history.value!.rows.map((entry, i) => <button key={i} onClick={() => pick(entry)}>{utc(entry.ms)} · {label(entry, 'type')}</button>)}
        {history.value!.total > 10 && <button onClick={open}>All {history.value!.total} events</button>}</div>}</td></tr>}
  </>
}
