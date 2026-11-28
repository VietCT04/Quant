import assert from 'node:assert/strict'
import { mkdir, writeFile } from 'node:fs/promises'
import { chromium } from 'playwright'

const origin = process.argv[2] || 'http://127.0.0.1:8765'
const output = process.argv[3] || 'data/research/lead-trader/reconstruction-browser'
const browser = await chromium.launch({ channel: process.env.QUANT_BROWSER_CHANNEL || 'msedge', headless: true })
const page = await browser.newPage({ viewport: { width: 1600, height: 1000 } })
const errors = []
page.on('pageerror', error => errors.push(error.message))
const measurements = {}
async function request(path, label = path) {
  const start = performance.now()
  const response = await page.request.get(origin + '/api/replay/binance/' + path)
  assert.equal(response.status(), 200, await response.text())
  const raw = await response.body()
  ;(measurements[label] ||= []).push({ ms: Math.round(performance.now() - start), bytes: raw.length })
  return JSON.parse(raw)
}
const inspector = page.getByRole('region', { name: 'Execution detail' })
const report = async (symbol) => request('reconstruction?symbol=' + symbol, 'coverage')
async function executions(symbol) {
  const data = await request('executions?symbol=' + symbol + '&limit=200', 'orders')
  if (data.total <= 200) return data.rows
  const tail = await request('executions?symbol=' + symbol + '&offset=' + Math.max(200, data.total - 200), 'orders')
  return [...data.rows, ...tail.rows]
}
function url(symbol, row) {
  return origin + '/#explorer?source=binance&symbol=' + symbol + '&tf=1m&execution=' + row.id
}
function field(name) {
  return inspector.locator('dl > div').filter({ has: page.getByText(name, { exact: true }) }).locator('dd')
}

try {
  await mkdir(output, { recursive: true })
  const symbols = ['BTWUSDT', 'ZEREBROUSDT', 'EVAAUSDT', 'CCUSDT', 'FLUXUSDT']
  const audits = Object.fromEntries(await Promise.all(symbols.map(async symbol => [symbol, await report(symbol)])))
  for (const symbol of symbols) {
    const a = audits[symbol]
    assert.ok(a.orders >= 400, symbol)
    assert.ok((a.actions.unknown || 0) / a.orders < .12, symbol)
  }
  const btw = await executions('BTWUSDT')
  const picked = btw.findLast(r => r.type === 'scale_in' && r.entry_after != null && r.after != null)
    || btw.find(r => r.type === 'scale_out' && r.entry_after != null && r.after != null)
  assert.ok(picked, 'BTW has a classified fill with inventory and weighted entry')
  assert.ok(Math.abs(picked.before + picked.filled - picked.after) < 1e-7)
  const start = Math.floor(picked.ms / 60000) * 60000 - 30 * 60000
  const end = start + 60 * 60000
  const query = { symbol: 'BTWUSDT', start, end, tf: '1m', selected: picked.id }
  const name = 'window?' + new URLSearchParams(query).toString()
  const analysis = await request(name, 'analysis window')
  await request(name, 'analysis window')
  await request(name, 'analysis window')
  assert.ok(analysis.series.qty_events.some(([, q]) => q != null))
  assert.ok(analysis.series.unrealized_at_fill.some(([, value]) => value != null))
  const replay = await request('window?' + new URLSearchParams({ ...query, clock: picked.ms }).toString(), 'true replay window')
  assert.ok(replay.markers.every(r => r.ms <= picked.ms && ['realized', 'unclassified'].includes(r.type)))
  assert.ok(!replay.series.unrealized_at_fill && replay.series.qty.every(([, value]) => value == null))
  assert.deepEqual(replay.reconstruction, {})

  await page.goto(url('BTWUSDT', picked))
  await inspector.getByText(picked.id, { exact: true }).waitFor()
  assert.match(await inspector.locator('.replay-inspector-action strong').innerText(), /^(ADD|REDUCE) LONG$/)
  await page.getByTestId('reconstruction-coverage').click()
  await page.getByText(/Inventory: .* confirmed · .* inferred · .* unknown/).waitFor()
  assert.match(await field('Inventory confidence').innerText(), /Confirmed|Inferred/)
  assert.notEqual(await field('Executed USDT').innerText(), await field('Reported order P&L').innerText())
  await page.getByText('Unrealized P&L · line: market mark · dot: fill price').waitFor()
  await page.getByText('Loading ranks', { exact: true }).waitFor({ state: 'hidden' })
  await page.screenshot({ path: output + '/btw-reconstructed.png', animations: 'disabled' })
  await page.getByRole('button', { name: 'Refresh Binance archive' }).click()
  await inspector.getByText(picked.id, { exact: true }).waitFor()
  assert.match(page.url(), /symbol=BTWUSDT/)
  assert.equal(await page.getByLabel('Chart timeframe').inputValue(), '1m')

  // True replay exposes only reported data at the clock. Every archived order must be inspectable there.
  const raw = await request('navigate?' + new URLSearchParams({ symbol: 'BTWUSDT', id: picked.id,
    directionStep: 'select', clock: picked.ms }).toString(), 'true replay execution')
  assert.equal(raw.row.reported_pnl, picked.reported_pnl)
  assert.equal(raw.row.filled_notional, picked.filled_notional)
  assert.equal(raw.row.after, undefined)
  await page.getByRole('button', { name: 'True replay', exact: true }).click()
  await page.waitForFunction(side => document.querySelector('.replay-inspector-action strong')?.textContent === side,
    picked.side.toUpperCase())
  assert.equal(await field('Inventory confidence').innerText(), 'Unknown')
  assert.deepEqual(errors, [])
  await writeFile(output + '/browser-results.json', JSON.stringify({ audits, measurements }, null, 2) + '\n')
  console.log(JSON.stringify({ executions: audits.BTWUSDT.orders, measurements }))
} finally {
  await browser.close()
}
