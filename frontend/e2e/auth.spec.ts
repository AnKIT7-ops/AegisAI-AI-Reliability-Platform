import { execFileSync } from 'node:child_process'
import { expect, test } from '@playwright/test'
import { heading, PASSWORD, signIn } from './stack.ts'

test('no route answers without a session', async ({ request }) => {
  for (const path of [
    '/api/v1/evaluations',
    '/api/v1/releases',
    '/api/v1/incidents',
    '/api/v1/telemetry',
    '/api/v1/demo',
  ]) {
    expect((await request.get(path)).status(), path).toBe(401)
  }
  for (const path of ['/prometheus/api/v1/query?query=up', '/grafana/api/health']) {
    const refused = await request.get(path, { maxRedirects: 0 })
    expect(refused.status(), path).toBe(302)
    expect(refused.headers().location, path).toBe('/#/login')
  }
})

test('sign-in, wrong password, sign-out and the end of the session', async ({
  page,
  playwright,
}) => {
  await page.goto('/')
  await signIn(page, 'not-the-password')
  await expect(page.getByRole('alert')).toHaveText('That password is wrong.')

  await signIn(page, PASSWORD)
  await heading(page, 'Overview')
  const session = await page.context().storageState()

  await page.getByRole('button', { name: 'Sign out' }).click()
  await expect(page.getByLabel('Password')).toBeVisible()

  // The old cookie is refused: logout revokes on the server.
  const stale = await playwright.request.newContext({
    baseURL: 'http://localhost:8090',
    storageState: session,
  })
  expect((await stale.get('/api/v1/evaluations')).status()).toBe(401)
  await stale.dispose()
})

test('nginx hides the backend', async ({ request }) => {
  for (const path of ['/metrics', '/health/ready', '/docs']) {
    expect((await request.get(path)).status(), path).toBe(404)
  }
})

test('only nginx is published, on loopback', () => {
  const lines = execFileSync(
    'docker',
    [
      'ps',
      '--filter',
      'label=com.docker.compose.project=aegis-e2e',
      '--format',
      '{{.Label "com.docker.compose.service"}} {{.Ports}}',
    ],
    { encoding: 'utf8' },
  )
    .trim()
    .split('\n')
  expect(lines.length).toBe(5)
  const published = lines.filter((line) => line.includes('->'))
  expect(published).toEqual(['frontend 127.0.0.1:8090->8080/tcp'])
})
