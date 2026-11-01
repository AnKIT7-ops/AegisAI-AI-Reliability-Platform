import { expect, test as setup } from '@playwright/test'
import { SESSION_FILE, signIn } from './stack.ts'

setup('sign in', async ({ page }) => {
  await page.goto('/')
  await signIn(page)
  await expect(page.getByRole('button', { name: 'Sign out' })).toBeVisible()
  await page.context().storageState({ path: SESSION_FILE })
})
