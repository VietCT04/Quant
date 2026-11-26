import assert from 'node:assert/strict'
import { mkdir, writeFile } from 'node:fs/promises'
import { chromium } from 'playwright'
import { performance } from 'node:perf_hooks'

const origin = process.argv[2] ?? 'http://127.0.0.1:8765'
const output = process.argv[3] ?? 'bench-data/explorer'
const job = process.env.QUANT_EXPLORER_JOB ?? '48b299ec64e74adc9f24d3315ea04bc3'
const base = '/api/explorer/' + job
const browser = await chromium.launch({ channel: process.env.QUANT_BROWSER_CHANNEL || 'msedge', headless: true })
const context = await browser.newContext({ viewport: { width: 1600, height: 1020 }, deviceScaleFactor: 1 })
const page = await context.newPage()
const errors = []
const ms = {}
page.on('pageerror', err => errors.push(String(err)))

async function get(path) {
  const before = performance.now()
  const response = await context.request.get(origin + path)
  assert.equal(response.status(), 200, await response.text())
  const value = await response.json()
  ms[path] = { latency: Math.round(performance.now() - before), bytes: (await response.body()).length }
  return value
}

async function verifyReplayReceipt(meta) {
  if (meta.detailed_job) return
  await page.getByText('Detailed execution data is not available. Generate detailed replay.', { exact: true }).waitFor()
  const settings = async paused => {
    const result = await context.request.post(origin + '/api/service/settings', {
      headers: { 'X-Quant-Client': 'control' }, data: { paused },
    })
    assert.equal(result.status(), 200)
  }
  await settings(true)
  const button = page.getByRole('button', { name: 'Generate detailed replay' })
  const before = performance.now()
  const accepted = page.waitForResponse(r => r.url().endsWith('/api/hypotheses/H-0001/audit') && r.request().method() === 'POST')
  await button.click()
  const response = await accepted
  assert.equal(response.status(), 202, await response.text())
  ms.submit_ms = Math.round(performance.now() - before)
  const first = await response.json()
  const duplicate = await context.request.post(origin + '/api/hypotheses/H-0001/audit', {
    headers: { 'X-Quant-Client': 'control' }, data: { experiment_id: job },
  })
  assert.equal(duplicate.status(), 202)
  assert.deepEqual(await duplicate.json(), first)
  assert.equal((await get(base + '/meta')).replay.status, 'queued')
  await settings(false)
  await page.getByText('Detailed native execution data', { exact: true }).waitFor({ timeout: 45000 })
}

try {
  await mkdir(output, { recursive: true })
  await page.goto(origin + '/#results')
  await page.getByRole('button', { name: 'Native simulation', exact: true }).click()
  await page.getByRole('link', { name: 'Open Backtest Explorer', exact: true }).click()
  await page.getByTestId('market-chart').locator('canvas').first().waitFor()
  const initial = await get(base + '/meta')
  assert.deepEqual(initial.symbols, ['BTCUSDT'])
  await verifyReplayReceipt(initial)
  const meta = await get(base + '/meta')
  assert.ok(meta.detailed_job)
  const candles = await get(base + '/candles?tf=15m')
  assert.equal(candles.rows.length, 672)
  assert.ok(candles.rows[0].open > 1000)
  const marks = await get(base + '/fills?limit=500')
  assert.ok(marks.total > 0)
  assert.ok(marks.rows.every(row => row.ms === row.bar_start + 5100 && row.price > 0 && row.filled !== 0))
  const view = await get(base + '/features?tf=15m')
  assert.deepEqual(view.features.map(x => x.name), ['fast', 'slow', 'raw_signal'])
  assert.equal(view.series.fast[0][3] - view.series.fast[0][2], 5100)
  const panels = await get(base + '/series?tf=15m')
  assert.ok(panels.portfolio.equity.length > 0 && panels.symbol.qty.length === 672)
  await page.getByRole('heading', { name: 'Execution ledger' }).waitFor()
  await page.getByText('Fast EMA 10 · USDT/base', { exact: true }).waitFor()
  await page.getByText('Portfolio equity / exposure / drawdown', { exact: true }).waitFor()
  await page.evaluate(() => window.scrollTo(0, 0))
  await page.screenshot({ path: output + '/market.png', fullPage: true })
  const positions = await get(base + '/positions?state=closed&sort=net_pnl')
  assert.ok(positions.total > 0)
  await page.getByLabel('Position state').selectOption('closed')
  await page.getByRole('button', { name: 'Net realized', exact: true }).click()
  const first = page.getByRole('table').locator('tbody tr').first()
  await first.getByRole('button', { name: positions.rows[0].id, exact: true }).click()
  await page.getByRole('region', { name: 'Position detail' }).waitFor()
  assert.ok(page.url().includes('position=' + positions.rows[0].id))
  const fitStart = Number(new URL(page.url().replace('#explorer?', '?')).searchParams.get('from'))
  assert.ok(fitStart <= positions.rows[0].entry_ms && fitStart >= meta.start)
  await page.getByRole('heading', { name: 'Execution timeline' }).waitFor()
  await page.getByRole('region', { name: 'Position detail' }).locator('.timeline-list button').first().click()
  await page.getByRole('region', { name: 'Execution detail' }).waitFor()
  const booked = await page.getByRole('region', { name: 'Execution detail' }).locator('dt').filter({ hasText: 'Booked fill / settlement UTC' }).locator('..').textContent()
  await page.getByLabel('Chart timeframe').selectOption('5m')
  const after = await page.getByRole('region', { name: 'Execution detail' }).locator('dt').filter({ hasText: 'Booked fill / settlement UTC' }).locator('..').textContent()
  assert.equal(after, booked)
  await page.getByRole('button', { name: 'Expand chart', exact: true }).click()
  assert.ok(page.url().includes('expanded=1'))
  await page.getByRole('button', { name: 'Restore chart', exact: true }).click()
  await page.getByLabel('Chart timeframe').selectOption('1m')
  await page.getByText('Portfolio equity / exposure / drawdown', { exact: true }).waitFor()
  await page.evaluate(() => window.scrollTo(0, 0))
  await page.screenshot({ path: output + '/position.png', fullPage: true })
  await page.getByRole('button', { name: 'Reset zoom', exact: true }).click()
  await page.getByLabel('Ledger table').getByRole('button', { name: 'Fills', exact: true }).click()
  await page.getByLabel('Execution type').selectOption('partial')
  const fillRows = await get(base + '/fills?type=partial&limit=50')
  assert.ok(fillRows.rows.every(fill => fill.partial))
  const csv = await context.request.get(origin + base + '/positions?format=csv')
  assert.equal(csv.status(), 200)
  assert.match(await csv.text(), /symbol,side,entry_ms/)
  assert.deepEqual(errors, [])
  await writeFile(output + '/browser-verification.json', JSON.stringify({ candidate: job, replay: meta.replay, fills: marks.total, positions: positions.total, measurements: ms }, null, 2))
  console.log(JSON.stringify({ job, replay: meta.replay, fills: marks.total, closed: positions.total, measurements: ms }))
} finally {
  await browser.close()
}
