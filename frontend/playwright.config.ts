import { defineConfig } from '@playwright/test'
import { SESSION_FILE } from './e2e/stack.ts'

const signedIn = { storageState: SESSION_FILE }

// One stack, shared and written to in order, so the projects run as a chain.
// The first visit needs an empty database: see docs/development.md.
export default defineConfig({
  testDir: 'e2e',
  workers: 1,
  forbidOnly: !!process.env.CI,
  reporter: 'list',
  use: {
    baseURL: 'http://localhost:8090',
    trace: 'retain-on-failure',
    // A local Chromium-based browser, in place of Playwright's download.
    launchOptions: { executablePath: process.env.AEGIS_E2E_BROWSER || undefined },
  },
  projects: [
    { name: 'session', testMatch: 'session.setup.ts' },
    {
      name: 'journey',
      testMatch: 'journey.spec.ts',
      dependencies: ['session'],
      use: signedIn,
    },
    {
      name: 'releases',
      testMatch: 'releases.spec.ts',
      dependencies: ['journey'],
      use: signedIn,
    },
    {
      name: 'operations',
      testMatch: 'operations.spec.ts',
      dependencies: ['releases'],
      use: signedIn,
    },
    { name: 'auth', testMatch: 'auth.spec.ts', dependencies: ['operations'] },
  ],
})
