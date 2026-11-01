import { describe, expect, it, vi } from 'vitest'
import { api, ApiError, setCsrfToken, setUnauthorizedHandler } from './api'
import { apiError, stubApi } from './test/stub'

async function failure(promise: Promise<unknown>): Promise<ApiError> {
  const error = await promise.catch((e: unknown) => e)
  expect(error).toBeInstanceOf(ApiError)
  return error as ApiError
}

describe('api', () => {
  it('returns the parsed body', async () => {
    stubApi({ 'GET /documents': { documents: [] } })
    await expect(api('/documents')).resolves.toEqual({ documents: [] })
  })

  it('returns nothing for 204', async () => {
    stubApi({ 'POST /auth/logout': () => new Response(null, { status: 204 }) })
    await expect(api('/auth/logout', { method: 'POST' })).resolves.toBeUndefined()
  })

  it('reads the error shape and the request ID', async () => {
    stubApi({
      'GET /x': () =>
        apiError(503, 'model_unavailable', 'Ollama is down', {
          'X-Request-ID': 'req-9',
        }),
    })
    const error = await failure(api('/x'))
    expect(error.status).toBe(503)
    expect(error.code).toBe('model_unavailable')
    expect(error.message).toBe('Ollama is down')
    expect(error.requestId).toBe('req-9')
  })

  it('explains nginx errors that are not JSON', async () => {
    stubApi({
      'POST /documents/ingest': () =>
        new Response('<html>too large</html>', { status: 413 }),
    })
    const error = await failure(api('/documents/ingest', { method: 'POST' }))
    expect(error.message).toBe('That file is larger than the 2 MB limit.')
  })

  it('names an unexpected status', async () => {
    stubApi({ 'GET /x': () => new Response('nope', { status: 418 }) })
    expect((await failure(api('/x'))).message).toBe('The request failed (418).')
  })

  it('explains an unreachable server', async () => {
    vi.stubGlobal('fetch', () => Promise.reject(new TypeError('failed')))
    const error = await failure(api('/x'))
    expect(error.code).toBe('network_error')
    expect(error.message).toContain('cannot be reached')
  })

  it('passes an abort through untouched', async () => {
    const controller = new AbortController()
    controller.abort()
    vi.stubGlobal('fetch', () => Promise.reject(new DOMException('', 'AbortError')))
    const error = await api('/x', { signal: controller.signal }).catch(
      (e: unknown) => e,
    )
    expect(error).toBeInstanceOf(DOMException)
  })

  it('reads Retry-After', async () => {
    stubApi({
      'POST /auth/login': () =>
        apiError(429, 'too_many_attempts', 'later', { 'Retry-After': '120' }),
    })
    const error = await failure(api('/auth/login', { method: 'POST' }))
    expect(error.retryAfter).toBe(120)
  })

  it('sends the CSRF token on unsafe requests only', async () => {
    const calls = stubApi({ 'GET /a': {}, 'POST /b': {} })
    setCsrfToken('secret-token')
    await api('/a')
    await api('/b', { method: 'POST', json: {} })
    const header = (i: number) =>
      new Headers(calls[i]?.init.headers).get('X-CSRF-Token')
    expect(header(0)).toBeNull()
    expect(header(1)).toBe('secret-token')
  })

  it('sends JSON with its content type', async () => {
    const calls = stubApi({ 'POST /b': {} })
    await api('/b', { method: 'POST', json: { a: 1 } })
    expect(calls[0]?.init.body).toBe('{"a":1}')
    expect(new Headers(calls[0]?.init.headers).get('Content-Type')).toBe(
      'application/json',
    )
  })

  it('reports a lost session', async () => {
    const lost = vi.fn()
    setUnauthorizedHandler(lost)
    stubApi({ 'GET /x': () => apiError(401, 'not_authenticated', 'sign in') })
    await failure(api('/x'))
    expect(lost).toHaveBeenCalledOnce()
  })

  it('does not treat a wrong password as a lost session', async () => {
    const lost = vi.fn()
    setUnauthorizedHandler(lost)
    stubApi({
      'POST /auth/login': () => apiError(401, 'invalid_credentials', 'wrong'),
    })
    await failure(api('/auth/login', { method: 'POST' }))
    expect(lost).not.toHaveBeenCalled()
  })
})
