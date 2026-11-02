import { Readout } from '../components/Metrics'
import {
  ErrorState,
  Loading,
  Tag,
  Verdict,
} from '../components/States'
import { measured, percent, seconds } from '../format'
import type {
  DocumentRecord,
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

function Readouts({
  telemetry,
  run,
  open,
  release,
}: {
  telemetry: Telemetry | null
  run: EvaluationRun | undefined
  open: Incident[] | null
  release: Release | undefined
}) {
  const recall = run?.metrics?.recall_at_k
  return (
    <dl className="readouts">
      <Readout label="Health" value={health(telemetry)} />
      <Readout
        label="Recall@k"
        value={typeof recall === 'number' ? recall.toFixed(2) : 'No evaluation'}
      />
      <Readout
        label="Quality gate"
        value={run?.gate ? <Verdict passed={run.gate.passed} /> : 'No result'}
      />
      <Readout
        label="Error rate"
        value={measured(telemetry, telemetry?.error_ratio, percent)}
      />
      <Readout
        label="Chat p95"
        value={measured(telemetry, telemetry?.chat_p95_s, seconds)}
      />
      <Readout
        label="Active release"
        value={release?.version ?? 'None'}
      />
      <Readout
        label="Open incidents"
        value={open === null ? 'Not checked' : String(open.length)}
      />
    </dl>
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
  if (runs.data === null || documents.loading) {
    return <Loading label="Loading the overview…" />
  }

  const nothingYet =
    runs.data.runs.length === 0 &&
    (documents.data?.documents.length ?? 0) === 0
  if (nothingYet) return <Start />

  const active = releases.data?.releases ?? []
  const release = active.find((r) => r.source === 'live') ?? active[0]
  return (
    <>
      <Readouts
        telemetry={telemetry.data}
        run={run}
        open={incidents.data?.incidents ?? null}
        release={release}
      />
      <Incidents incidents={incidents.data?.incidents ?? []} />
    </>
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
