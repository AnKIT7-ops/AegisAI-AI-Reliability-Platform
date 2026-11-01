import { expect, test } from '@playwright/test'
import { csrfHeaders, heading } from './stack.ts'

test('first visit to a diagnosed incident, with no model', async ({ page }) => {
  await page.goto('/')
  await heading(page, 'Nothing is being measured yet')
  await page.getByRole('button', { name: 'Load demo data' }).click()

  const guide = page.getByRole('region', {
    name: 'Follow one answer from question to diagnosis',
  })
  const step = (name: string) =>
    guide.getByRole('link', { name: new RegExp(`^${name}`) }).click()
  const overview = () => page.getByRole('link', { name: 'AegisAI' }).click()

  await step('Ask')
  await heading(page, 'Chat')
  await page
    .getByRole('region', { name: 'Recorded answers' })
    .getByRole('button')
    .first()
    .click()
  await expect(
    page.getByRole('heading', { name: /^Recorded answer\b(?!s)/ }),
  ).toBeVisible()
  await heading(page, 'Sources')

  await overview()
  await step('Evaluate')
  await heading(page, 'Quality gate')
  await expect(
    page.getByRole('region', { name: 'Quality gate' }).getByText('PASS'),
  ).toBeVisible()

  await overview()
  await step('Inspect')
  await heading(page, 'Telemetry')

  await overview()
  await step('Diagnose')
  await heading(page, 'Observed facts')
  await heading(page, 'Hypotheses')
  await expect(page.getByText('rule-based')).toBeVisible()
})

test('loading the demo twice creates no duplicates', async ({ request }) => {
  const count = async (path: string, key: string) =>
    (await (await request.get(`/api/v1/${path}`)).json())[key].length
  const before = [
    await count('evaluations?limit=200', 'runs'),
    await count('releases?limit=200', 'releases'),
    await count('incidents?limit=200', 'incidents'),
  ]
  const again = await request.post('/api/v1/demo/load', {
    headers: await csrfHeaders(request),
  })
  expect(again.ok()).toBe(true)
  expect(before).toEqual([
    await count('evaluations?limit=200', 'runs'),
    await count('releases?limit=200', 'releases'),
    await count('incidents?limit=200', 'incidents'),
  ])
  expect(before).toEqual([3, 3, 1])
})
