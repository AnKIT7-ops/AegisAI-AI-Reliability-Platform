import type { Telemetry } from './types'

export function percent(ratio: number): string {
  return `${(ratio * 100).toFixed(1)}%`
}

export function seconds(value: number): string {
  return value < 1 ? `${Math.round(value * 1000)} ms` : `${value.toFixed(1)} s`
}

export function fileSize(bytes: number): string {
  return bytes < 1024 ? `${bytes} B` : `${(bytes / 1024).toFixed(1)} KB`
}

export function when(iso: string | null | undefined): string {
  if (!iso) return '-'
  const date = new Date(iso)
  return Number.isNaN(date.getTime()) ? iso : date.toLocaleString()
}

/** "rolled_back" as "Rolled back". */
export function words(value: string): string {
  const text = value.replaceAll('_', ' ')
  return text.charAt(0).toUpperCase() + text.slice(1)
}

const METRIC_LABELS: Record<string, string> = {
  failure_rate: 'Failure rate',
  recall_at_k: 'Recall@k',
  mrr: 'MRR',
  answer_correctness: 'Answer correctness',
  citation_correctness: 'Citation correctness',
  refusal_accuracy: 'Refusal accuracy',
  false_refusal_rate: 'False refusal rate',
  correct_refusal_rate: 'Correct refusal rate',
  injection_resistance: 'Injection resistance',
  latency_ms_p50: 'Latency p50',
  latency_ms_p95: 'Latency p95',
}

/** Quality measures, as opposed to counts such as examples or tokens. */
export function isQualityMetric(name: string): boolean {
  return name in METRIC_LABELS
}

export function metricLabel(name: string): string {
  return METRIC_LABELS[name] ?? words(name)
}

export function metricValue(name: string, value: number): string {
  return name.startsWith('latency') ? `${value} ms` : value.toFixed(2)
}

/** Null means no traffic, which is not the same as zero. */
export function measured(
  telemetry: Telemetry | null,
  value: number | null | undefined,
  format: (value: number) => string,
): string {
  if (telemetry === null || telemetry.not_checked.length > 0) return 'Not checked'
  return value === null || value === undefined ? 'No traffic' : format(value)
}
