import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it } from 'vitest'
import App from './App'
import { apiError, NO_DEMO, stubApi, TELEMETRY } from './test/stub'

const OFF = {
  enabled: false,
  authenticated: false,
  expires_at: null,
  csrf_token: null,
}
const SIGNED_OUT = { ...OFF, enabled: true }
const SIGNED_IN = {
  ...OFF,
  enabled: true,
  authenticated: true,
  csrf_token: 'token-1',
}
const OVERVIEW = {
  'GET /demo': NO_DEMO,
  'GET /evaluations?limit=1': { runs: [] },
  'GET /documents': { documents: [] },
  'GET /telemetry': TELEMETRY,
  'GET /incidents?status=open&limit=5': { incidents: [] },
  'GET /releases?status=active': { releases: [] },
}

describe('App', () => {
  it('opens the console when sign-in is off', async () => {
    stubApi({ 'GET /auth/session': OFF, ...OVERVIEW })
    render(<App />)
    expect(await screen.findByRole('heading', { name: 'Overview' })).toBeTruthy()
    expect(screen.queryByRole('button', { name: 'Sign out' })).toBeNull()
  })

  it('asks for the password when sign-in is on', async () => {
    stubApi({ 'GET /auth/session': SIGNED_OUT })
    render(<App />)
    expect(await screen.findByLabelText('Password')).toBeTruthy()
    expect(screen.queryByRole('navigation')).toBeNull()
  })

  it('signs in and opens the console', async () => {
    let signedIn = false
    stubApi({
      'GET /auth/session': () =>
        new Response(JSON.stringify(signedIn ? SIGNED_IN : SIGNED_OUT)),
      'POST /auth/login': () => {
        signedIn = true
        return new Response(JSON.stringify(SIGNED_IN))
      },
      ...OVERVIEW,
    })
    render(<App />)
    await userEvent.type(await screen.findByLabelText('Password'), 'pass')
    await userEvent.click(screen.getByRole('button', { name: 'Sign in' }))
    expect(await screen.findByRole('heading', { name: 'Overview' })).toBeTruthy()
    expect(screen.getByRole('button', { name: 'Sign out' })).toBeTruthy()
  })

  it('explains a wrong password', async () => {
    stubApi({
      'GET /auth/session': SIGNED_OUT,
      'POST /auth/login': () => apiError(401, 'invalid_credentials', 'wrong'),
    })
    render(<App />)
    await userEvent.type(await screen.findByLabelText('Password'), 'nope')
    await userEvent.click(screen.getByRole('button', { name: 'Sign in' }))
    expect((await screen.findByRole('alert')).textContent).toBe(
      'That password is wrong.',
    )
  })

  it('explains the login limit in minutes', async () => {
    stubApi({
      'GET /auth/session': SIGNED_OUT,
      'POST /auth/login': () =>
        apiError(429, 'too_many_attempts', 'later', { 'Retry-After': '600' }),
    })
    render(<App />)
    await userEvent.type(await screen.findByLabelText('Password'), 'nope')
    await userEvent.click(screen.getByRole('button', { name: 'Sign in' }))
    expect((await screen.findByRole('alert')).textContent).toContain('10 min')
  })

  it('returns to sign-in when the session ends', async () => {
    let expired = false
    stubApi({
      'GET /auth/session': () =>
        new Response(JSON.stringify(expired ? SIGNED_OUT : SIGNED_IN)),
      ...OVERVIEW,
      'GET /documents': () => {
        expired = true
        return apiError(401, 'not_authenticated', 'sign in')
      },
    })
    render(<App />)
    expect(await screen.findByLabelText('Password')).toBeTruthy()
  })

  it('signs out', async () => {
    let signedIn = true
    const calls = stubApi({
      'GET /auth/session': () =>
        new Response(JSON.stringify(signedIn ? SIGNED_IN : SIGNED_OUT)),
      'POST /auth/logout': () => {
        signedIn = false
        return new Response(null, { status: 204 })
      },
      ...OVERVIEW,
    })
    render(<App />)
    await userEvent.click(await screen.findByRole('button', { name: 'Sign out' }))
    expect(await screen.findByLabelText('Password')).toBeTruthy()
    const logout = calls.find((c) => c.key === 'POST /auth/logout')
    expect(new Headers(logout?.init.headers).get('X-CSRF-Token')).toBe('token-1')
  })

  it('shows why the session could not be read', async () => {
    stubApi({
      'GET /auth/session': () =>
        apiError(503, 'database_unavailable', 'database is unavailable', {
          'X-Request-ID': 'req-7',
        }),
    })
    render(<App />)
    const alert = await screen.findByRole('alert')
    expect(alert.textContent).toContain('database is unavailable')
    expect(alert.textContent).toContain('req-7')
  })

  it('marks the current page and moves focus when the route changes', async () => {
    stubApi({
      'GET /auth/session': OFF,
      ...OVERVIEW,
      'GET /demo': NO_DEMO,
    })
    render(<App />)
    await screen.findByRole('heading', { name: 'Overview' })
    window.location.hash = '#/chat'
    await screen.findByRole('heading', { name: 'Chat' })
    const link = screen.getByRole('link', { name: 'Chat' })
    expect(link.getAttribute('aria-current')).toBe('page')
    await waitFor(() => expect(document.activeElement?.id).toBe('main'))
  })

  it('does not open a detail page for an id that is not a uuid', async () => {
    window.location.hash = '#/incidents/..%2Fdemo'
    const calls = stubApi({ 'GET /auth/session': OFF })
    render(<App />)
    expect(await screen.findByText('That page does not exist')).toBeTruthy()
    expect(calls.some((c) => c.key.includes('incidents'))).toBe(false)
    window.location.hash = '#/evaluations/nope'
    expect(await screen.findByText('That page does not exist')).toBeTruthy()
  })
})
