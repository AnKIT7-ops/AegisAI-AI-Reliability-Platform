import { vi } from 'vitest'
import type { DemoStatus, Telemetry } from '../types'

type Reply = unknown | ((init: RequestInit) => Response)

export function json(
  body: unknown,
  status = 200,
  headers: Record<string, string> = {},
): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json', ...headers },
  })
}

export function apiError(
  status: number,
  code: string,
  message: string,
  headers: Record<string, string> = {},
): Response {
  return json({ error: { code, message } }, status, headers)
}

/** Answers fetch from a map of "METHOD /path". Anything else fails the test. */
export function stubApi(routes: Record<string, Reply>) {
  const calls: { key: string; init: RequestInit }[] = []
  const fetchStub = vi.fn(async (url: string, init: RequestInit = {}) => {
    const key = `${init.method ?? 'GET'} ${url.replace('/api/v1', '')}`
    calls.push({ key, init })
    if (!(key in routes)) throw new Error(`unexpected request: ${key}`)
    const reply = routes[key]
    return typeof reply === 'function'
      ? (reply as (init: RequestInit) => Response)(init)
      : json(reply)
  })
  vi.stubGlobal('fetch', fetchStub)
  return calls
}

export const TELEMETRY: Telemetry = {
  observed_at: '2026-11-01T00:00:00Z',
  database: 'ok',
  not_checked: [],
  firing_alerts: [],
  request_rate_per_s: 1.5,
  error_ratio: 0.018,
  chat_p95_s: 2.4,
  model_p95_s: 2.1,
  retrieval_p95_s: 0.05,
  model_failures: {},
  grafana_url: 'http://localhost:8080/grafana/',
  prometheus_url: 'http://localhost:8080/prometheus',
}

export const NO_DEMO: DemoStatus = {
  loaded: false,
  documents: [{ filename: 'k8s-probes.md', size_bytes: 2048, chunks: 12 }],
  recorded_answers: [],
  run_ids: {},
  incident_id: null,
}

export const DEMO: DemoStatus = {
  ...NO_DEMO,
  loaded: true,
  run_ids: { current: 'run-1' },
  incident_id: 'incident-1',
  recorded_answers: [
    {
      example_id: 'prom-storage-01',
      category: 'answerable',
      question: 'What is the default retention?',
      answer: 'It is 15 days [2].',
      sources: ['prometheus-storage.md', 'prometheus-overview.md'],
      latency_ms: 1120,
    },
  ],
}
