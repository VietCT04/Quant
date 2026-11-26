import assert from 'node:assert/strict'
import { mkdir, readFile } from 'node:fs/promises'
import { chromium } from 'playwright'

const origin = process.argv[2] ?? 'http://127.0.0.1:8791'
const artifacts = process.argv[3] ?? 'data/research/v2'
const receipt = JSON.parse(await readFile(`${artifacts}/audit.json`))
const job = receipt.original
const browser = await chromium.launch({ channel: process.env.QUANT_BROWSER_CHANNEL || 'msedge', headless: true })
const page = await browser.newPage({ viewport: { width: 1440, height: 980 } })
const errors = []
page.on('pageerror', error => errors.push(String(error)))
const get = async path => {
  const res = await page.request.get(origin + '/api' + path)
  assert.equal(res.status(), 200, await res.text())
  return res.json()
}

try {
  await mkdir(artifacts, { recursive: true })
  const meta = await get(`/explorer/${job}/meta`)
  assert.equal(meta.execution_model, 'next_open_v2')
  assert.ok(meta.detailed_job)
  const base = `/explorer/${job}`
  const symbol = meta.symbols[0]
  const fills = await get(`${base}/fills?symbol=${symbol}&type=terminal_close`)
  assert.equal(fills.total, 1)
  const terminal = fills.rows[0]
  assert.equal(terminal.ms, meta.end)
  assert.equal(terminal.reason_label, 'END_OF_BACKTEST')
  assert.equal(terminal.virtual, true)
  assert.equal(terminal.after, 0)
  const positions = await get(`${base}/positions?symbol=${symbol}&sort=exit_ms&desc=1`)
  const last = positions.rows[0]
  assert.equal(last.terminal_close, true)
  assert.equal(positions.reconciled.qty, 0)
  const h = await get(`/hypotheses/${meta.hypothesis_id}/results`)
  const outcome = h.experiments.find(item => item.id === job).outcome.metrics
  assert.equal(outcome.execution_diagnostics.success_rate, 1)
  assert.equal(outcome.completed_positions, positions.total)
  assert.ok(outcome.pre_close_positions[0].qty)

  await page.goto(origin + '/#results')
  await page.getByLabel('Hypothesis', { exact: true }).selectOption(meta.hypothesis_id)
  await page.getByRole('heading', { name: 'Native results' }).waitFor()
  await page.getByRole('button', { name: 'ROI', exact: true }).click()
  await page.getByText('Execution diagnostics', { exact: true }).waitFor()
  await page.screenshot({ path: `${artifacts}/results.png`, fullPage: true })

  await page.goto(origin + `/#explorer?hypothesis=${meta.hypothesis_id}&candidate=${job}&symbol=${symbol}&sort=exit_ms&desc=1`)
  await page.getByRole('heading', { name: 'Execution ledger' }).waitFor()
  await page.getByRole('button', { name: last.id, exact: true }).click()
  await page.getByText('Virtual terminal exit', { exact: false }).waitFor()
  await page.getByRole('heading', { name: 'Execution timeline' }).waitFor()
  await page.screenshot({ path: `${artifacts}/terminal-position.png`, fullPage: true })
  assert.deepEqual(errors, [])
  console.log('V2 results, sort, detailed replay, terminal fill and lifecycle verified')
} finally {
  await browser.close()
}
