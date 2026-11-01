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

export type DataSource = 'live' | 'demo'
export type Metrics = Record<string, number | null>

export interface Gate {
  passed: boolean
  failures: string[]
}

export interface EvaluationRun {
  id: string
  label: string
  status: string
  source: DataSource
  dataset_version: string
  dataset_checksum: string
  prompt_version: string
  chat_model: string
  embed_model: string
  top_k: number
  min_score: number
  metrics: Metrics | null
  error: string | null
  created_at: string | null
  gate?: Gate | null
}

export interface EvaluationResult {
  example_id: string
  category: string
  question: string
  answer: string | null
  sources: string[]
  scores: Record<string, unknown>
  latency_ms: number | null
  error: string | null
}

export interface EvaluationDetail extends EvaluationRun {
  limits: Record<string, number>
  results: EvaluationResult[]
}

export interface MetricRow {
  name: string
  candidate: number
  baseline: number
  delta: number
  direction: 'improved' | 'regressed' | 'same'
}

export interface RunComparison {
  status: 'comparable' | 'dataset_changed'
  run: EvaluationRun
  baseline: EvaluationRun
  metrics: MetricRow[]
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

export interface Fact {
  statement: string
  source: string
  reference: string | null
}

export interface Hypothesis {
  cause: string
  confidence: string
  basis: string
}

export interface IncidentReport {
  symptom: string
  facts: Fact[]
  hypotheses: Hypothesis[]
  uncertainty: string
  runbook: string
  suggested_actions: string[]
}

export interface Incident {
  id: string
  type: string
  severity: string
  status: string
  source: 'live' | 'simulated'
  occurrences?: number
  last_seen?: string
  report?: IncidentReport
}

export interface Comparison {
  status: 'comparable' | 'no_baseline' | 'dataset_changed'
  baseline_release_id: string | null
  metrics: MetricRow[]
}

export interface ReleaseEvent {
  event: string
  from_status: string | null
  to_status: string | null
  actor: string
  reason: string | null
  request_id: string | null
  created_at: string
}

export interface Release {
  id: string
  version: string
  source: DataSource
  status: string
  simulated: boolean
  evaluation_run_id: string
  prompt_version: string
  chat_model: string
  top_k: number
  min_score: number
  gate_passed: boolean | null
  gate_failures: string[]
  comparison: Comparison | null
  events?: ReleaseEvent[]
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
