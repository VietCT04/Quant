import { useMemo, useState } from 'react'
import { flexRender, getCoreRowModel, getPaginationRowModel, getSortedRowModel, useReactTable, type ColumnDef, type RowSelectionState, type SortingState } from '@tanstack/react-table'
import { ArrowDownUp, ChevronLeft, ChevronRight, Search } from 'lucide-react'
import type { Hypothesis } from './api'
import { Badge, between, Empty, Progress } from './components'

export const states = ['running', 'queued', 'blocked', 'completed', 'failed', 'cancelled']

export function Queue({ rows, open, bulk }: {
  rows: Hypothesis[]; open: (id: string) => void
  bulk: (operation: string, ids: string[], values?: object) => void
}) {
  const [query, setQuery] = useState('')
  const [filter, setFilter] = useState('')
  const [sorting, setSorting] = useState<SortingState>([])
  const [selected, setSelected] = useState<RowSelectionState>({})
  const ids = Object.keys(selected).filter(id => selected[id])
  const data = useMemo(() => {
    const match = query.trim().toLowerCase()
    return rows.filter(row => (!filter || row.state === filter) &&
      [row.id, row.name, row.family, row.stage].some(value => value.toLowerCase().includes(match)))
  }, [rows, query, filter])
  const columns = useMemo<ColumnDef<Hypothesis>[]>(() => [
    { id: 'selection', header: ({ table }) => <input type="checkbox" aria-label="Select page" checked={table.getIsAllPageRowsSelected()} onChange={table.getToggleAllPageRowsSelectedHandler()} />, cell: ({ row }) => <input type="checkbox" aria-label={`Select ${row.original.id}`} checked={row.getIsSelected()} onChange={row.getToggleSelectedHandler()} />, enableSorting: false },
    { accessorKey: 'name', header: 'Hypothesis', cell: ({ row }) => <button className="row-link" onClick={() => open(row.original.id)}><strong>{row.original.name}</strong><small>{row.original.id} <span>·</span> {row.original.family}</small></button> },
    { accessorKey: 'state', header: 'Execution', cell: ({ getValue }) => <Badge value={getValue<string>()} /> },
    { accessorKey: 'stage', header: 'Stage', cell: ({ getValue }) => <span className="stage">{getValue<string>()}</span> },
    { accessorKey: 'priority', header: 'Priority', cell: ({ getValue }) => <span className="mono">{getValue<number>()}</span> },
    { accessorFn: item => item.finished / item.total, id: 'progress', header: 'Trials', cell: ({ row }) => <Progress item={row.original} /> },
    { accessorKey: 'created', header: 'Elapsed', cell: ({ row }) => <span className="mono muted">{between(row.original.created, row.original.finished === row.original.total ? row.original.last_result : null)}</span> },
    { id: 'dependencies', header: 'Dependencies / errors', cell: ({ row }) => {
      const item = row.original
      return <div className="dependency">{item.dependencies.length ? item.dependencies.map(id => <button key={id} className="text-button mono" onClick={() => open(id)}>{id}</button>) : '—'}
        {item.errors.length > 0 && <small title={item.errors.map(error => error.message || error.type).join('\n')}>{item.errors[0].type.replace(/([a-z])([A-Z])/g, '$1 $2')}</small>}</div>
    } },
  ], [open])
  const table = useReactTable({
    data, columns, getRowId: item => item.id, state: { sorting, rowSelection: selected },
    onSortingChange: setSorting, onRowSelectionChange: setSelected,
    getCoreRowModel: getCoreRowModel(), getSortedRowModel: getSortedRowModel(),
    getPaginationRowModel: getPaginationRowModel(), autoResetPageIndex: false,
    initialState: { pagination: { pageSize: 15 } },
  })
  function changeFilter(update: () => void) { table.setPageIndex(0); update() }
  return <section className="panel">
    <div className="table-tools">
      <label className="search"><Search size={16} /><input value={query} placeholder="Search hypotheses…" onChange={event => changeFilter(() => setQuery(event.target.value))} aria-label="Search hypotheses" /></label>
      <select aria-label="Execution state" value={filter} onChange={event => changeFilter(() => setFilter(event.target.value))}><option value="">All execution states</option>{states.map(state => <option key={state}>{state}</option>)}</select>
      {!!ids.length && <div className="bulk"><span>{ids.length} selected</span>
        <button onClick={() => bulk('retry', ids)}>Retry</button>
        <button onClick={() => bulk('priority', ids)}>Priority</button>
        <button className="danger" onClick={() => bulk('cancel', ids)}>Cancel</button>
        <button className="text-button" onClick={() => setSelected({})}>Clear</button>
      </div>}
    </div>
    <div className="table-scroll">
      <table className="dense-table"><thead>{table.getHeaderGroups().map(group => <tr key={group.id}>{group.headers.map(header => <th key={header.id} scope="col" aria-sort={header.column.getIsSorted() === 'asc' ? 'ascending' : header.column.getIsSorted() === 'desc' ? 'descending' : undefined}>
        {header.column.getCanSort() ? <button className="sort" onClick={header.column.getToggleSortingHandler()}>{flexRender(header.column.columnDef.header, header.getContext())}<ArrowDownUp size={11} /></button> : flexRender(header.column.columnDef.header, header.getContext())}
      </th>)}</tr>)}</thead><tbody>{table.getRowModel().rows.map(row => <tr key={row.id} className={row.getIsSelected() ? 'selected' : ''}>{row.getVisibleCells().map(cell => <td key={cell.id}>{flexRender(cell.column.columnDef.cell, cell.getContext())}</td>)}</tr>)}</tbody></table>
    </div>
    {(!data.length || !table.getRowModel().rows.length) && <Empty title={rows.length ? 'No matching hypotheses' : 'Queue is empty'}>{rows.length ? 'Change the search or execution filter.' : 'Submit a hypothesis or campaign to start.'}</Empty>}
    <footer className="table-footer"><span>{data.length} hypotheses</span><div><button aria-label="Previous page" className="icon-button" disabled={!table.getCanPreviousPage()} onClick={() => table.previousPage()}><ChevronLeft size={16} /></button>
      <span>Page {table.getState().pagination.pageIndex + 1} / {Math.max(1, table.getPageCount())}</span>
      <button aria-label="Next page" className="icon-button" disabled={!table.getCanNextPage()} onClick={() => table.nextPage()}><ChevronRight size={16} /></button></div></footer>
  </section>
}
