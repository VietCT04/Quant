import assert from 'node:assert/strict'
import { mkdir, writeFile } from 'node:fs/promises'
import { chromium } from 'playwright'
import { performance } from 'node:perf_hooks'

const origin = process.argv[2] || 'http://127.0.0.1:8765'
const output = process.argv[3] || 'data/research/replay-workstation'
const job = '48b299ec64e74adc9f24d3315ea04bc3'
const browser = await chromium.launch({ channel: process.env.QUANT_BROWSER_CHANNEL || 'msedge', headless: true })
const page = await browser.newPage({ viewport: { width: 1600, height: 1020 }, deviceScaleFactor: 1 })
const errors = []
const timings = {}
page.on('pageerror', error => errors.push(error.message))
const button = name => page.getByRole('button', { name, exact: true })
const chart = page.getByTestId('market-chart')
const selected = () => page.evaluate(() => new URLSearchParams(location.hash.split('?')[1]).get('execution'))
const ready = async (id = null) => {
  await page.waitForFunction(wanted => {
    const node = document.querySelector('[data-marker]')
    return !!node && (!wanted || JSON.parse(node.dataset.marker).id === wanted)
  }, id)
  return JSON.parse(await chart.getAttribute('data-marker'))
}
const position = async () => JSON.parse(await chart.getAttribute('data-viewport'))

async function get(url) {
  const now = performance.now()
  const response = await page.request.get(origin + '/api/replay/' + url)
  assert.equal(response.status(), 200, await response.text())
  const raw = await response.body()
  ;(timings[url.split('?')[0]] ||= []).push({ ms: Math.round(performance.now() - now), bytes: raw.length })
  return JSON.parse(raw)
}

async function geometry(id, row, step, color) {
  await page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))))
  const marker = await ready(id)
  assert.equal(marker.candle, Math.floor(row.ms / step) * step)
  assert.equal(marker.timeAtTip, marker.candle)
  assert.equal(marker.price, row.price)
  assert.ok(Math.abs(marker.priceAtTip - row.price) <= Math.max(1e-12, row.price * 1e-10))
  assert.equal(marker.color, color)
  assert.equal(marker.right - marker.left, 13)
  assert.equal(marker.left, marker.x)
  const found = await chart.evaluate((host, geometry) => {
    const rect = host.getBoundingClientRect()
    const x = rect.left + geometry.x + 8
    const y = rect.top + geometry.y
    const rgb = geometry.color.slice(1).match(/../g).map(code => parseInt(code, 16))
    return Array.from(host.querySelectorAll('canvas')).some(canvas => {
      const area = canvas.getBoundingClientRect()
      if (x < area.left || x >= area.right || y < area.top || y >= area.bottom) return false
      const cx = Math.round((x - area.left) * canvas.width / area.width)
      const cy = Math.round((y - area.top) * canvas.height / area.height)
      const pixels = canvas.getContext('2d').getImageData(Math.max(0, cx - 1), Math.max(0, cy - 1), 3, 3).data
      for (let i = 0; i < pixels.length; i += 4) {
        if (pixels[i + 3] > 65 && rgb.every((value, j) => Math.abs(value - pixels[i + j]) < 40)) return true
      }
      return false
    })
  }, marker)
  assert.ok(found, 'The canvas body contains the expected transaction color at the chart coordinate')
  return marker
}

try {
  await mkdir(output, { recursive: true })
  const native = await get(job + '/executions?symbol=BTCUSDT&limit=200')
  assert.ok(native.total > 80)
  const first = native.rows[0], second = native.rows[1]
  const exit = native.rows.at(-1)
  for (let i = 1; i < native.rows.length; ++i) {
    const a = native.rows[i - 1], b = native.rows[i]
    assert.ok(a.ms <= b.ms && a.ordinal + 1 === b.ordinal)
    assert.ok(Math.abs(b.before + b.filled - b.after) < 1e-10)
  }
  let positionRow = first
  for (const next of native.rows.slice(1)) {
    const response = await get(job + '/navigate?' + new URLSearchParams({ symbol: 'BTCUSDT', id: positionRow.id, directionStep: 'next' }))
    assert.equal(response.row.id, next.id)
    positionRow = response.row
  }
  for (const previous of native.rows.slice(0, -1).reverse()) {
    const response = await get(job + '/navigate?' + new URLSearchParams({ symbol: 'BTCUSDT', id: positionRow.id, directionStep: 'previous' }))
    assert.equal(response.row.id, previous.id)
    positionRow = response.row
  }
  await page.goto(origin + '/#explorer?hypothesis=H-0001&candidate=' + job + '&symbol=BTCUSDT&tf=1m')
  await page.getByRole('region', { name: 'Execution detail' }).waitFor()
  await geometry(first.id, first, 60_000, '#17ae89')
  await page.waitForFunction(() => document.querySelectorAll('.replay-pane-caption').length === 3)
  assert.equal(await button('Labels').getAttribute('aria-pressed'), 'true')
  await button('Labels').click()
  assert.equal(await button('Labels').getAttribute('aria-pressed'), 'false')
  await button('Labels').click()
  const before = await position()
  await button('Next execution').click()
  await geometry(second.id, second, 60_000, '#ed596c')
  assert.deepEqual(await position(), before)
  await page.keyboard.press('ArrowLeft')
  await ready(first.id)
  await page.keyboard.press('Shift+ArrowRight')
  await ready(second.id)
  await button('Last execution').click()
  await ready(exit.id)
  assert.ok(Math.abs((await position()).spacing - before.spacing) < .25)
  assert.equal(await page.getByLabel('Chart timeframe').inputValue(), '1m')
  await button('First execution').click()
  await geometry(first.id, first, 60_000, '#17ae89')
  await button('Log').click()
  await geometry(first.id, first, 60_000, '#17ae89')
  await page.getByLabel('Chart timeframe').selectOption('15s')
  await geometry(first.id, first, 15_000, '#17ae89')
  await page.setViewportSize({ width: 1440, height: 960 })
  await geometry(first.id, first, 15_000, '#17ae89')
  await page.getByLabel('Chart timeframe').selectOption('1m')
  await ready(first.id)
  const rect = await chart.boundingBox()
  await page.mouse.move(rect.x + rect.width * .42, rect.y + 165)
  await page.mouse.wheel(0, -100)
  await page.waitForTimeout(450)
  await geometry(first.id, first, 60_000, '#17ae89')
  await button('Center on selected execution').click()
  await ready(first.id)
  await button('Chart settings').click()
  await page.getByLabel('Unrealized P&L', { exact: true }).uncheck()
  await page.waitForFunction(() => document.querySelectorAll('.replay-pane-caption').length === 2)
  await page.getByLabel('Unrealized P&L', { exact: true }).check()
  await button('Chart settings').click()
  await page.screenshot({ path: output + '/native.png', fullPage: true, animations: 'disabled' })
  const window = await get(job + '/window?' + new URLSearchParams({ symbol: 'BTCUSDT', tf: '1m',
    start: first.ms - 300_000, end: first.ms + 300_000, clock: first.ms, selected: first.id }))
  assert.ok(window.candles.rows.every(row => row.available_ms == null || row.available_ms <= first.ms))
  assert.ok(window.markers.every(row => row.ms <= first.ms))
  assert.equal(window.markers.length, 1)
  assert.ok(window.series.qty_events.every(([t]) => t <= first.ms))
  await Promise.all([page.waitForResponse(r => r.url().includes('/window?') && r.url().includes('clock=')), button('True replay').click()])
  await ready(first.id)
  assert.equal(await page.getByTestId('execution-counter').textContent(), '1 / 1 observed')
  await button('Analysis').click()
  await ready(first.id)
  await button('Fullscreen workspace').click()
  await ready(first.id)
  await button('Exit fullscreen').click()

  const publicMeta = await get('binance/meta')
  const publicRows = await get('binance/executions?symbol=AKEUSDT&limit=200')
  const count = publicRows.total
  assert.ok(count > 100 && publicMeta.symbols.length > 100)
  const choices = new Map()
  let pair = null
  for (const row of publicRows.rows) {
    const t = Math.floor(row.ms / 60_000)
    const prior = choices.get(t)
    if (prior && row.price !== prior.price) { pair = [prior, row]; break }
    choices.set(t, row)
  }
  assert.ok(pair)
  const watch = await get('binance/watch?period=research')
  const instrument = watch.rows.find(row => row.symbol === 'AKEUSDT')
  assert.ok(instrument.executed > 0 && instrument.market > instrument.executed)
  await page.goto(origin + '/#explorer?source=binance&symbol=AKEUSDT&tf=1m&execution=' + pair[0].id)
  await geometry(pair[0].id, pair[0], 60_000, pair[0].side === 'buy' ? '#17ae89' : '#ed596c')
  const x = (await ready(pair[0].id)).x
  const jump = page.getByLabel('Jump to timestamp, execution or position ID')
  await jump.fill(pair[1].id)
  await button('Jump').click()
  assert.equal((await geometry(pair[1].id, pair[1], 60_000, pair[1].side === 'buy' ? '#17ae89' : '#ed596c')).x, x)
  await page.getByRole('region', { name: 'Execution detail' }).getByText('Reported order P&L', { exact: true }).waitFor()
  const watchlist = page.getByRole('complementary', { name: 'Symbol watchlist' })
  const search = page.getByLabel('Search symbols')
  await search.fill('JCTUSDT')
  await watchlist.getByRole('button', { name: 'JCTUSDT', exact: true }).click()
  await page.waitForFunction(() => new URLSearchParams(location.hash.split('?')[1]).get('symbol') === 'JCTUSDT')
  await ready()
  assert.equal(await page.getByLabel('Chart timeframe').inputValue(), '1m')
  await button('Next execution').click()
  assert.match(page.url(), /symbol=JCTUSDT/)
  await search.fill('AKEUSDT')
  await watchlist.getByRole('button', { name: 'AKEUSDT', exact: true }).click()
  await ready(pair[1].id)
  await button('Refresh Binance archive').click()
  await ready(pair[1].id)
  await button('Light mode').click()
  await page.screenshot({ path: output + '/binance.png', fullPage: true, animations: 'disabled' })
  assert.equal(await selected(), pair[1].id)
  const busiest = watch.rows.reduce((a, b) => a.count > b.count ? a : b)
  assert.ok(busiest.count >= 1000)
  await search.fill(busiest.symbol)
  await watchlist.getByRole('button', { name: busiest.symbol, exact: true }).click()
  await page.waitForFunction((n) => document.querySelector('[data-testid="execution-counter"]')?.textContent.includes('/ ' + n), busiest.count)
  const t0 = performance.now()
  await button('Last execution').click()
  await page.waitForFunction(n => document.querySelector('[data-testid="execution-counter"]')?.textContent === n + ' / ' + n, busiest.count)
  const lastSeekMs = Math.round(performance.now() - t0)
  await page.locator('.replay-ledger tbody tr.selected').waitFor()
  const ledgerRows = await page.locator('.replay-ledger tbody tr:not(.spacer)').count()
  assert.ok(ledgerRows > 0 && ledgerRows < 60)
  assert.deepEqual(errors, [])
  const summary = { nativeFills: native.total, publicSymbolFills: count, publicSymbols: publicMeta.symbols.length, busiest,
    lastSeekMs, ledgerRows, timings: Object.fromEntries(Object.entries(timings).map(([key, samples]) => {
      const times = samples.map(x => x.ms).sort((a, b) => a - b)
      return [key, { requests: times.length, median_ms: times[Math.floor(times.length / 2)], p95_ms: times[Math.floor((times.length - 1) * .95)],
        bytes: samples[0].bytes }]
    })) }
  await writeFile(output + '/verification.json', JSON.stringify(summary, null, 2))
  console.log(JSON.stringify(summary))
} catch (error) {
  await page.screenshot({ path: output + '/failure.png', fullPage: true }).catch(() => {})
  console.error(errors)
  throw error
} finally {
  await browser.close()
}
