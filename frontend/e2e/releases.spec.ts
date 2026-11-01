import type { Page } from '@playwright/test'
import { expect, test } from '@playwright/test'
import { csrfHeaders } from './stack.ts'

const row = (page: Page, version: string) =>
  page.getByRole('row').filter({
    has: page.getByRole('link', { name: version, exact: true }),
  })

const open = async (page: Page, version: string) => {
  await page.goto('/#/releases')
  await row(page, version).getByRole('link', { name: version }).click()
  await expect(page.getByRole('heading', { name: version })).toBeVisible()
}

test('a passing candidate is promoted', async ({ page, request }) => {
  const demo = await (await request.get('/api/v1/demo')).json()
  await page.goto('/#/releases')
  await page.getByLabel('Version').fill('e2e-1')
  await page.getByLabel('Completed evaluation').selectOption(demo.run_ids.current)
  await page.getByRole('button', { name: 'Create candidate' }).click()

  await page.getByRole('button', { name: 'Run canary' }).click()
  await page.getByRole('button', { name: 'Promote' }).click()
  await page.getByRole('button', { name: 'Yes, promote' }).click()

  await expect(row(page, 'e2e-1').getByRole('cell').last()).toHaveText('Active')
  await expect(row(page, 'demo-2').getByRole('cell').last()).toHaveText(
    'Superseded',
  )
})

test('a rejected release cannot be promoted', async ({ page, request }) => {
  await open(page, 'demo-3')
  await expect(
    page.getByText('This release failed its gate, so it cannot be promoted.'),
  ).toBeVisible()
  await expect(page.getByRole('button', { name: 'Promote' })).toHaveCount(0)

  const { releases } = await (
    await request.get('/api/v1/releases?status=rejected')
  ).json()
  const refused = await request.post(
    `/api/v1/releases/${releases[0].id}/promote`,
    { headers: await csrfHeaders(request), data: {} },
  )
  expect(refused.status()).toBe(409)
  expect((await refused.json()).error.code).toBe('invalid_transition')

  await page.reload()
  await expect(row(page, 'demo-3').getByRole('cell').last()).toHaveText(
    'Rejected',
  )
})

test('rollback names its target and shows in history', async ({ page }) => {
  await open(page, 'e2e-1')
  await page.getByLabel('Roll back to').selectOption({ label: 'demo-2' })
  await page.getByLabel('Reason').fill('e2e rollback drill')
  await page.getByRole('button', { name: 'Roll back to demo-2' }).click()
  await page.getByRole('button', { name: 'Yes, roll back to demo-2' }).click()

  await expect(row(page, 'demo-2').getByRole('cell').last()).toHaveText('Active')
  await expect(row(page, 'e2e-1').getByRole('cell').last()).toHaveText(
    'Rolled back',
  )
  await expect(
    page.getByRole('listitem').filter({ hasText: 'e2e rollback drill' }),
  ).toBeVisible()
})
