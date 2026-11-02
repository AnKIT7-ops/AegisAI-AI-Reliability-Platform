import { expect, test } from '@playwright/test'
import { csrfHeaders } from './stack.ts'

// Passing, promotion and rollback paths are covered against PostgreSQL in
// backend/tests/postgres: the fake model cannot pass the v1 quality gate.
test('a candidate that fails the gate cannot be promoted', async ({ page, request }) => {
  const started = await request.post('/api/v1/evaluations/run', {
    headers: await csrfHeaders(request),
    data: { label: 'e2e-release' },
  })
  expect(started.ok()).toBe(true)
  const { id } = await started.json()
  await expect
    .poll(
      async () => (await (await request.get(`/api/v1/evaluations/${id}`)).json()).status,
      { timeout: 60_000 },
    )
    .toBe('done')

  await page.goto('/#/releases')
  await page.getByLabel('Version').fill('e2e-1')
  await page.getByLabel('Completed evaluation').selectOption(id)
  await page.getByRole('button', { name: 'Create candidate' }).click()
  await page.getByRole('button', { name: 'Run canary' }).click()

  await expect(page.getByText('Rejected').first()).toBeVisible()
  await expect(page.getByRole('button', { name: 'Promote' })).toHaveCount(0)
})
