import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import { chromium } from 'playwright'

const url = process.argv[2] ?? 'http://127.0.0.1:8781'
const report = JSON.parse(await readFile(process.argv[3] ?? 'bench-data/strategy-interface/final/acceptance.json', 'utf8'))
const browser = await chromium.launch({ channel: process.env.QUANT_BROWSER_CHANNEL || 'msedge' })
try {
  const page = await browser.newPage()
  const browserErrors = []
  page.on('pageerror', error => browserErrors.push(error.message))
  for (const [hypothesis, entry] of Object.entries(report.explorer)) {
    await page.goto(url + '/?browser-strategy=' + encodeURIComponent(entry.strategy) + '#explorer?' + new URLSearchParams({
      hypothesis, candidate: entry.job, symbol: entry.symbol,
    }))
    await page.getByText('Detailed native execution data', { exact: true }).waitFor()
    await page.getByTestId('market-chart').locator('canvas').first().waitFor()
    const provenance = page.locator('details.explorer-metadata')
    await provenance.locator('summary').click()
    for (const plot of entry.plots) await provenance.locator('pre').getByText(new RegExp('"name": "' + plot + '"')).waitFor()
    await page.getByRole('heading', { name: 'Execution ledger' }).waitFor()
    await page.getByLabel('Ledger table').getByRole('button', { name: 'Fills', exact: true }).click()
    const row = page.locator('.ledger-table tbody tr').first()
    await row.getByRole('button').first().click()
    await page.getByRole('region', { name: 'Execution detail' }).waitFor()
    if (entry.strategy === 'shock_reversal') {
      await page.getByRole('region', { name: 'Execution detail' }).getByText('Fade shock', { exact: true }).waitFor()
    }
  }
  assert.deepEqual(browserErrors, [])
  console.log(JSON.stringify({ verified: Object.values(report.explorer).map(value => value.strategy) }))
} finally {
  await browser.close()
}
