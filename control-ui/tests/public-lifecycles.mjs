import assert from 'node:assert/strict'
import { mkdir } from 'node:fs/promises'
import { chromium } from 'playwright'

const origin = process.argv[2] || 'http://127.0.0.1:8765'
const output = process.argv[3] || 'data/research/lead-trader/reconstruction-browser'
const base = origin + '/api/replay/binance/'
const browser = await chromium.launch({ channel: process.env.QUANT_BROWSER_CHANNEL || 'msedge', headless: true })
const page = await browser.newPage({ viewport: { width: 1600, height: 1000 } })
const errors = []
page.on('pageerror', error => errors.push(error.message))
async function api(resource, args = {}) {
  const response = await page.request.get(base + resource + '?' + new URLSearchParams({ symbol: 'BTWUSDT', ...args }))
  assert.equal(response.status(), 200, await response.text())
  return response.json()
}
const inspector = page.getByRole('region', { name: 'Execution detail' })
const badge = inspector.getByRole('region', { name: 'Lifecycle completeness' })
const execution = () => page.evaluate(() => new URLSearchParams(location.hash.split('?')[1]).get('execution'))
async function selected(id) {
  await page.waitForFunction(wanted => new URLSearchParams(location.hash.split('?')[1]).get('execution') === wanted, id)
  await inspector.getByText(id, { exact: true }).waitFor()
}

try {
  const complete = await api('lifecycles', { complete: 1 })
  const short = complete.rows.find(r => r.id === '1165320199225315328:54')
  assert.ok(short)
  assert.equal(short.opening_quantity, -289)
  assert.equal(short.covered_order_ms, short.report_span_ms)
  assert.equal(short.captured_executions, 6)
  const walk = await api('executions', { lifecycle: short.id })
  assert.deepEqual(walk.rows.map(row => row.type), ['reversal', ...Array(4).fill('scale_out'), 'reversal'])
  assert.equal(walk.rows.at(-1).position_info.id, short.id)
  const window = await api('window', { lifecycle: short.id, tf: '15m',
    start: short.opening_execution_ms - 2 * 3600000, end: short.exit_ms + 2 * 3600000 })
  assert.equal(window.markers.length, 6)
  assert.deepEqual(window.series.qty_events[0], [short.opening_execution_ms, -289])
  assert.deepEqual(window.series.qty_events.at(-1), [short.exit_ms, null])
  const first = (await api('navigate', { directionStep: 'first' })).row
  assert.equal(first.type, 'scale_out')
  assert.equal(first.position_info.opening_status, 'outside_public_retention')
  assert.equal(first.position_info.first_inventory_before, null)
  const missing = await api('navigate', { directionStep: 'opening', position: first.position })
  assert.equal(missing.row, null)
  assert.match(missing.notice, /Opening execution unavailable/)
  assert.equal((await api('lifecycles', { clock: short.exit_ms, complete: 1 })).total, 0)

  await page.goto(origin + '/#explorer?source=binance&symbol=BTWUSDT&tf=15m&execution=' + first.id)
  await selected(first.id)
  await badge.getByText('Opening missing', { exact: true }).waitFor()
  await badge.getByText(/Pre-existing long inventory. Starting quantity unknown/).waitFor()
  assert.equal(await badge.getByRole('button', { name: 'Jump to position opening' }).count(), 0)
  await mkdir(output, { recursive: true })
  await page.screenshot({ path: output + '/btw-opening-missing.png', animations: 'disabled' })

  await page.getByRole('button', { name: 'Positions', exact: true }).click()
  await page.getByRole('checkbox', { name: 'Complete lifecycles only' }).check()
  const row = page.getByRole('row').filter({ hasText: short.id })
  await row.getByRole('button', { name: 'Jump to position opening' }).click()
  await selected(short.opening_execution_id)
  await badge.getByText('Complete', { exact: true }).waitFor()
  assert.equal(await page.getByLabel('Chart timeframe').inputValue(), '15m')
  assert.match(page.url(), /lifecycle=1165320199225315328%3A54/)
  for (const next of walk.rows.slice(1)) {
    await page.getByRole('button', { name: 'Next execution', exact: true }).click()
    await selected(next.id)
  }
  await Promise.all([
    page.waitForResponse(r => r.url().includes('/navigate?')),
    page.getByRole('button', { name: 'Next execution', exact: true }).click(),
  ])
  assert.equal(await execution(), short.closing_execution_id)
  await badge.getByRole('button', { name: 'Jump to position opening' }).click()
  await selected(short.opening_execution_id)
  await page.screenshot({ path: output + '/btw-complete-lifecycle.png', animations: 'disabled' })
  assert.deepEqual(errors, [])
  console.log(JSON.stringify({ complete: short.id, orders: walk.total, opening: short.opening_execution_ms }))
} finally {
  await browser.close()
}
