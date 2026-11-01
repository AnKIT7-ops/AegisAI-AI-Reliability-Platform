import { expect, test } from '@playwright/test'
import { heading } from './stack.ts'

test('a live run fails its gate and detection reports it', async ({ page }) => {
  test.setTimeout(120_000)
  await page.goto('/#/evaluations')
  await page.getByLabel('Run label').fill('e2e-live')
  await page.getByRole('button', { name: 'Start evaluation' }).click()

  // The list polls while a run is active, so no reload is needed here.
  const run = page.getByRole('row').filter({ hasText: 'e2e-live' })
  await expect(run.getByRole('cell').nth(1)).toHaveText('Done', {
    timeout: 90_000,
  })
  await expect(run.getByText('FAIL')).toBeVisible()

  await page.goto('/#/incidents')
  await page.getByRole('button', { name: 'Check for incidents' }).click()
  await expect(page.getByRole('status')).toContainText('Found 1.')

  await page.getByRole('link', { name: /retrieval quality regression/i }).click()
  await page.getByRole('button', { name: 'Resolve incident' }).click()
  await expect(
    page.getByRole('button', { name: 'Resolve incident' }),
  ).toHaveCount(0)
})

test('telemetry reports a rate once traffic has been scraped', async ({
  page,
}) => {
  test.setTimeout(120_000)
  await page.goto('/#/telemetry')
  const rate = page.getByText('Error rate').locator('..')
  await expect(async () => {
    await page.getByRole('button', { name: 'Refresh' }).click()
    await expect(rate).toContainText('%', { timeout: 2_000 })
  }).toPass({ timeout: 100_000, intervals: [5_000] })
  await expect(rate).not.toContainText('No traffic')
})

test('a failed request shows its message and request ID', async ({ page }) => {
  await page.goto('/#/evaluations/00000000-0000-4000-8000-000000000000')
  const alert = page.getByRole('alert')
  await expect(alert).toContainText('not found')
  await expect(alert).toContainText('Request ID')
})

test('a crafted id in the URL never reaches the API', async ({ page }) => {
  const calls: string[] = []
  page.on('request', (request) => calls.push(request.url()))
  await page.goto('/#/incidents/..%2Freleases')
  await heading(page, 'That page does not exist')
  expect(calls.filter((url) => url.includes('..'))).toHaveLength(0)
  expect(calls.filter((url) => url.includes('/incidents/'))).toHaveLength(0)
})

test('the keyboard reaches the content', async ({ page }) => {
  await page.goto('/')
  await heading(page, 'Overview')
  await page.keyboard.press('Tab')
  await expect(
    page.getByRole('link', { name: 'Skip to content' }),
  ).toBeFocused()
  await page.keyboard.press('Enter')
  await expect(page.getByRole('main')).toBeFocused()

  await page.getByRole('link', { name: 'Releases' }).focus()
  await page.keyboard.press('Enter')
  await heading(page, 'Releases')
  await expect(page.getByRole('main')).toBeFocused()
})

test.describe('at 360 px', () => {
  test.use({ viewport: { width: 360, height: 740 } })

  for (const route of [
    '',
    'chat',
    'documents',
    'evaluations',
    'telemetry',
    'incidents',
    'releases',
  ]) {
    test(`#/${route} does not scroll sideways`, async ({ page }) => {
      await page.goto(`/#/${route}`)
      await expect(page.getByRole('heading', { level: 1 })).toBeVisible()
      await expect(page.getByRole('status')).toHaveCount(0)
      const width = await page.evaluate(() => ({
        page: document.documentElement.scrollWidth,
        view: document.documentElement.clientWidth,
      }))
      expect(width.page).toBeLessThanOrEqual(width.view)
    })
  }
})
