import assert from 'node:assert/strict'
import { mkdir } from 'node:fs/promises'
import { chromium } from 'playwright'

const origin = process.argv[2] ?? 'http://127.0.0.1:8765'
const output = process.argv[3] ?? 'bench-data/control-ui'
const browser = await chromium.launch({ channel: process.env.QUANT_BROWSER_CHANNEL || 'msedge', headless: true })
const context = await browser.newContext({ viewport: { width: 1440, height: 1040 }, deviceScaleFactor: 1 })
const page = await context.newPage()
const errors = []
page.on('pageerror', error => errors.push(String(error)))
const screenshot = async name => page.screenshot({ path: `${output}/${name}.png`, fullPage: true, animations: 'disabled' })
const button = name => page.getByRole('button', { name, exact: true })
const nav = name => page.getByRole('link', { name, exact: true })
const heading = name => page.getByRole('heading', { name, exact: true })
const snapshot = async () => (await context.request.get(`${origin}/api/snapshot`)).json()
const wait = async (fn, message, timeout = 30000) => {
  const end = Date.now() + timeout
  while (Date.now() < end) {
    const result = await fn()
    if (result) return result
    await new Promise(resolve => setTimeout(resolve, 200))
  }
  throw new Error(message)
}

try {
  await mkdir(output, { recursive: true })
  await page.goto(origin)
  await heading('Hypothesis execution').waitFor()
  await page.getByText('Local service connected', { exact: true }).waitFor()
  await page.getByText('Prepared cache', { exact: true }).waitFor()
  const initial = await snapshot()
  const completed = initial.hypotheses.find(item => item.stage === 'simulation' && item.state === 'completed' && item.total > 1)
  assert.ok(completed, 'A completed native acceptance hypothesis is required')
  await screenshot('overview')

  await nav('Hypothesis Queue').click()
  await heading('Hypothesis Queue').waitFor()
  const search = page.getByRole('textbox', { name: 'Search hypotheses' })
  await search.fill(completed.id)
  assert.equal(await page.locator('tbody tr').count(), 1)
  await page.locator('tbody tr').filter({ hasText: completed.id }).getByRole('button', { name: new RegExp(completed.name) }).click()
  await page.getByRole('dialog', { name: completed.name }).waitFor()
  await page.getByText('Mathematical definition', { exact: true }).waitFor()
  await screenshot('hypothesis')
  await page.keyboard.press('Escape')
  assert.equal(await search.inputValue(), completed.id)
  await search.fill('')
  await screenshot('queue')

  await nav('Alpha Results').click()
  await page.getByLabel('Hypothesis', { exact: true }).selectOption(completed.id)
  await button('Native simulation').click()
  await heading('Net portfolio results').waitFor()
  await page.getByLabel('Compare with').selectOption({ index: 1 })
  await page.getByRole('columnheader', { name: 'Candidate 2' }).waitFor()
  await heading('Marked equity').waitFor()
  await screenshot('results')

  await nav('System').click()
  await heading('Dispatcher and budgets').waitFor()
  await button('Configure budgets').click()
  await page.getByRole('dialog', { name: 'Configure resource budgets' }).waitFor()
  await button('Close').last().click()
  await screenshot('system')

  await nav('Hypothesis Library').click()
  await heading('Registered hypotheses').waitFor()
  await page.getByRole('textbox', { name: 'Search library' }).fill(completed.id)
  await page.getByText(completed.name, { exact: true }).waitFor()

  await nav('SDK Requests').click()
  await heading('Capability requests').waitFor()
  const missing = `control_fixture_${Date.now()}`
  await button('New request').click()
  await page.getByLabel('JSON specification').fill(JSON.stringify({
    module: 'quant.ts', name: missing, purpose: 'Production browser verification',
    required_inputs: ['close'], parameters: {}, causal: true, streaming_required: false,
    acceptance_criteria: ['Calculate a known example'], specification: 'f(x) = x',
  }))
  await button('Confirm').click()
  await page.getByText(`quant.ts.${missing}`, { exact: true }).waitFor()

  await button('Pause dispatch').click()
  await button('Confirm').click()
  await button('Resume dispatch').waitFor()
  const original = initial.hypotheses.find(item => item.stage === 'screening')
  const response = await context.request.get(`${origin}/api/hypotheses/${original.id}`)
  const { definition } = await response.json()
  const testName = `Browser acceptance fixture ${Date.now()}`
  const submission = {
    ...definition, key: 'browser-acceptance-fixture', name: testName,
    candidates: [definition.candidates[0]], depends_on: [],
  }
  await button('Submit hypothesis').click()
  await page.getByLabel('JSON specification').fill(JSON.stringify(submission))
  await button('Submit').click()
  await page.getByText(/^Accepted H-/).waitFor()
  await nav('Hypothesis Queue').click()
  await search.fill(testName)
  await page.getByRole('button', { name: new RegExp(testName) }).click()
  await button('Cancel work').click()
  await button('Confirm cancellation').click()
  await button('Retry failed trials').waitFor()
  await button('Retry failed trials').click()
  await button('Cancel work').waitFor()
  await page.keyboard.press('Escape')
  await button('Resume dispatch').click()
  await button('Pause dispatch').waitFor()

  // Close the submitting tab as soon as work is eligible. The backend continues without a browser.
  await page.close()
  await wait(async () => (await snapshot()).hypotheses.find(item => item.name === submission.name && item.state === 'completed'), 'Browser trial did not complete')
  const reopened = await context.newPage()
  await reopened.goto(`${origin}/#inbox`)
  await reopened.getByRole('button', { name: new RegExp(testName) }).click()
  await reopened.getByRole('button', { name: 'Acknowledge', exact: true }).click()
  await reopened.getByRole('button', { name: 'Acknowledge', exact: true }).waitFor({ state: 'hidden' })
  assert.equal((await snapshot()).hypotheses.find(item => item.name === submission.name).unreviewed, false)
  assert.deepEqual(errors, [])
  console.log(JSON.stringify({ origin, output, checks: ['queue search', 'drawer and filter persistence', 'native comparison', 'budgets modal', 'library search', 'capability request', 'pause/resume', 'UI submission', 'cancel/retry', 'execution after tab closure', 'inbox acknowledgment'] }))
} finally {
  await browser.close()
}
