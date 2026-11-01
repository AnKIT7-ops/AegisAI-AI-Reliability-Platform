export interface SessionState {
  enabled: boolean
  authenticated: boolean
  expires_at: string | null
  csrf_token: string | null
}

export interface Telemetry {
  observed_at: string
  database: 'ok' | 'unavailable'
  not_checked: string[]
  firing_alerts: string[] | null
  request_rate_per_s: number | null
  error_ratio: number | null
  chat_p95_s: number | null
  model_p95_s: number | null
  retrieval_p95_s: number | null
  model_failures: Record<string, number> | null
  grafana_url: string
  prometheus_url: string
}

export interface EvaluationRun {
  id: string
  label: string
  status: string
  source: 'live' | 'demo'
  metrics: Record<string, number | null> | null
}

export interface EvaluationDetail extends EvaluationRun {
  gate: { passed: boolean; failures: string[] } | null
}

export interface DocumentRecord {
  id: string
  filename: string
  size_bytes: number
  chunks: number
  ingested_at: string
}

export interface DemoDocument {
  filename: string
  size_bytes: number
  chunks: number
}

export interface RecordedAnswer {
  example_id: string
  category: string
  question: string
  answer: string | null
  sources: string[]
  latency_ms: number | null
}

export interface DemoStatus {
  loaded: boolean
  documents: DemoDocument[]
  recorded_answers: RecordedAnswer[]
  run_ids: Record<string, string>
  incident_id: string | null
}

export interface Incident {
  id: string
  type: string
  severity: string
  status: string
  source: 'live' | 'simulated'
}

export interface Release {
  id: string
  version: string
  source: 'live' | 'demo'
  status: string
}

export interface Source {
  n: number
  filename: string
  score: number
  snippet: string
}

export interface Answer {
  answer: string
  sources: Source[]
  model: string | null
  latency_ms: number
}
