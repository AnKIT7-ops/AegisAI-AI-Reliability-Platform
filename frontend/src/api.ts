const BASE = '/api/v1'

// nginx answers these itself, so the body is not the API's error shape.
const STATUS_MESSAGES: Record<number, string> = {
  413: 'That file is larger than the 2 MB limit.',
  502: 'The backend is not responding. Check it with docker compose ps.',
  503: 'A service is unavailable. Check it with docker compose ps.',
  504: 'The backend took too long to answer. Try again.',
}

export class ApiError extends Error {
  readonly status: number
  readonly code: string
  readonly requestId: string | null
  readonly retryAfter: number | null

  constructor(
    status: number,
    code: string,
    message: string,
    requestId: string | null = null,
    retryAfter: number | null = null,
  ) {
    super(message)
    this.status = status
    this.code = code
    this.requestId = requestId
    this.retryAfter = retryAfter
  }
}

interface Options {
  method?: 'GET' | 'POST'
  json?: unknown
  form?: FormData
  signal?: AbortSignal
}

let csrfToken: string | null = null
let onUnauthorized: (() => void) | null = null

export function setCsrfToken(token: string | null): void {
  csrfToken = token
}

export function setUnauthorizedHandler(handler: (() => void) | null): void {
  onUnauthorized = handler
}

async function failure(response: Response): Promise<ApiError> {
  const requestId = response.headers.get('x-request-id')
  const retry = Number(response.headers.get('retry-after'))
  let code = 'http_error'
  let message =
    STATUS_MESSAGES[response.status] ?? `The request failed (${response.status}).`
  try {
    const body = (await response.json()) as {
      error?: { code?: string; message?: string }
    }
    code = body.error?.code ?? code
    message = body.error?.message ?? message
  } catch {
    // Not JSON: keep the message for this status.
  }
  return new ApiError(response.status, code, message, requestId, retry || null)
}

export async function api<T>(
  path: string,
  { method = 'GET', json, form, signal }: Options = {},
): Promise<T> {
  const headers = new Headers()
  if (json !== undefined) headers.set('Content-Type', 'application/json')
  if (method !== 'GET' && csrfToken) headers.set('X-CSRF-Token', csrfToken)
  let response: Response
  try {
    response = await fetch(BASE + path, {
      method,
      headers,
      body: json !== undefined ? JSON.stringify(json) : form,
      signal,
      credentials: 'same-origin',
    })
  } catch (error) {
    if (signal?.aborted) throw error
    throw new ApiError(
      0,
      'network_error',
      'The server cannot be reached. Check that the stack is running.',
    )
  }
  if (!response.ok) {
    const error = await failure(response)
    if (error.code === 'not_authenticated') onUnauthorized?.()
    throw error
  }
  return (response.status === 204 ? undefined : await response.json()) as T
}

export function toApiError(error: unknown): ApiError {
  return error instanceof ApiError
    ? error
    : new ApiError(0, 'unexpected', 'Something went wrong. Reload the page.')
}
