import { MetricBar } from '../components/Metrics'
import {
  ErrorState,
  Loading,
  Tag,
  Verdict,
} from '../components/States'
import { measured, percent, seconds, when } from '../format'
import type {
  DocumentRecord,
  EvaluationDetail,
  EvaluationRun,
  Incident,
  Release,
  Telemetry,
} from '../types'
import { useApi } from '../useApi'

function Start() {
  return (
    <section className="start" aria-labelledby="start-title">
      <h2 id="start-title">Nothing is being measured yet</h2>
      <p>
        Add your documents, ask a question, then run an evaluation to see how
        the answers score. Adding documents needs Ollama running.
      </p>
      <a className="button-primary" href="#/documents">
        Add documents
      </a>
    </section>
  )
}

// Only what was read: Ollama is not probed, so this never claims the model works.
function health(telemetry: Telemetry | null): string {
  if (telemetry === null) return 'Not checked'
  if (telemetry.database !== 'ok') return 'Database unavailable'
  if (telemetry.firing_alerts === null) return 'Not checked'
  return telemetry.firing_alerts.length > 0 ? 'Alerts firing' : 'No alerts'
}

function Signal({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <dt>{label}</dt>
      <dd>{value}</dd>
    </div>
  )
}

// The headline scores. The evaluation's own page has the full set.
const HEADLINE = [
  'recall_at_k',
  'answer_correctness',
  'citation_correctness',
  'injection_resistance',
]

function LatestEvaluation({ run }: { run: EvaluationRun }) {
  // Only the detail carries the limits, and the bars draw without them.
  const detail = useApi<EvaluationDetail>(`/evaluations/${run.id}`)
  const failures = run.gate?.failures ?? []
  return (
    <>
      <p className="latest">
        {run.gate ? <Verdict passed={run.gate.passed} /> : 'No gate result'}{' '}
        <a href={`#/evaluations/${run.id}`}>{run.label}</a>{' '}
        <span className="meta">{when(run.created_at)}</span>
      </p>
      <ul className="metrics">
        {HEADLINE.map((name) => {
          const value = run.metrics?.[name]
          return typeof value === 'number' ? (
            <MetricBar
              key={name}
              name={name}
              value={value}
              limit={detail.data?.limits[name]}
              missed={failures.some((failure) => failure.startsWith(`${name}:`))}
            />
          ) : null
        })}
      </ul>
    </>
  )
}

function Incidents({ incidents }: { incidents: Incident[] }) {
  if (incidents.length === 0) return null
  return (
    <section aria-labelledby="open-incidents">
      <h2 id="open-incidents">Open incidents</h2>
      <ul className="rows">
        {incidents.map((incident) => (
          <li key={incident.id}>
            <a href={`#/incidents/${incident.id}`}>
              {incident.type.replaceAll('_', ' ')}
            </a>{' '}
            <span className="meta">{incident.severity}</span>{' '}
            {incident.source === 'simulated' && <Tag>Simulated</Tag>}
          </li>
        ))}
      </ul>
    </section>
  )
}

function Content() {
  const runs = useApi<{ runs: EvaluationRun[] }>('/evaluations?limit=1')
  const documents = useApi<{ documents: DocumentRecord[] }>('/documents')
  const telemetry = useApi<Telemetry>('/telemetry')
  const incidents = useApi<{ incidents: Incident[] }>(
    '/incidents?status=open&limit=5',
  )
  const releases = useApi<{ releases: Release[] }>('/releases?status=active')
  const run = runs.data?.runs[0]

  if (runs.error) return <ErrorState error={runs.error} onRetry={runs.reload} />
  if (
    runs.data === null ||
    documents.loading ||
    telemetry.loading ||
    incidents.loading ||
    releases.loading
  ) {
    return <Loading label="Loading the overview…" />
  }

  const nothingYet =
    runs.data.runs.length === 0 &&
    (documents.data?.documents.length ?? 0) === 0
  if (nothingYet) return <Start />

  const active = releases.data?.releases ?? []
  const release = active.find((r) => r.source === 'live') ?? active[0]
  const open = incidents.data?.incidents ?? null
  return (
    <div className="overview">
      <div>
        <section aria-labelledby="latest-title">
          <h2 id="latest-title">Latest evaluation</h2>
          {run ? (
            <LatestEvaluation run={run} />
          ) : (
            <p>
              No evaluation yet. <a href="#/evaluations">Run one</a> to score the
              answers.
            </p>
          )}
        </section>
        <Incidents incidents={open ?? []} />
      </div>
      <div>
        <section aria-labelledby="traffic-title">
          <h2 id="traffic-title">Live traffic</h2>
          <dl className="signals">
            <Signal
              label="Error rate"
              value={measured(telemetry.data, telemetry.data?.error_ratio, percent)}
            />
            <Signal
              label="Chat p95"
              value={measured(telemetry.data, telemetry.data?.chat_p95_s, seconds)}
            />
          </dl>
        </section>
        <section aria-labelledby="operations-title">
          <h2 id="operations-title">Operations</h2>
          <dl className="signals">
            <Signal label="Health" value={health(telemetry.data)} />
            <Signal
              label="Open incidents"
              value={open === null ? 'Not checked' : String(open.length)}
            />
            <Signal label="Active release" value={release?.version ?? 'None'} />
          </dl>
        </section>
      </div>
    </div>
  )
}

export default function Overview() {
  return (
    <>
      <h1>Overview</h1>
      <Content />
    </>
  )
}
