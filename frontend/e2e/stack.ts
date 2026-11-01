import type { APIRequestContext, Page } from '@playwright/test'
import { expect } from '@playwright/test'

export const PASSWORD = 'e2e-only-password'
export const SESSION_FILE = 'e2e/.auth/session.json'

export async function csrfHeaders(request: APIRequestContext) {
  const session = await (await request.get('/api/v1/auth/session')).json()
  return { 'X-CSRF-Token': session.csrf_token as string }
}

export async function signIn(page: Page, password = PASSWORD) {
  await page.getByLabel('Password').fill(password)
  await page.getByRole('button', { name: 'Sign in' }).click()
}

export const heading = (page: Page, name: string) =>
  expect(page.getByRole('heading', { name, exact: true })).toBeVisible()
