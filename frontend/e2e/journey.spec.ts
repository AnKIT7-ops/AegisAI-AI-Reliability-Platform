import { expect, test } from '@playwright/test'
import { heading } from './stack.ts'

test('first visit, from an empty console to a sourced question', async ({ page }) => {
  await page.goto('/')
  await heading(page, 'Nothing is being measured yet')
  await page.getByRole('link', { name: 'Add documents' }).click()

  await heading(page, 'Documents')
  await page.getByLabel('Add a Markdown or text file, up to 1 MB').setInputFiles({
    name: 'e2e-note.md',
    mimeType: 'text/markdown',
    buffer: Buffer.from('Canary releases send a small share of traffic first.\n'),
  })
  await page.getByRole('button', { name: 'Add document' }).click()
  await expect(page.getByRole('cell', { name: 'e2e-note.md' })).toBeVisible()

  await page.getByRole('link', { name: 'Chat' }).click()
  await heading(page, 'Chat')
  await page
    .getByLabel('Ask about the ingested documents')
    .fill('What do canary releases send first?')
  await page.getByRole('button', { name: 'Ask' }).click()
  await heading(page, 'Answer')

  await page.getByRole('link', { name: 'Telemetry' }).click()
  await heading(page, 'Telemetry')
})
