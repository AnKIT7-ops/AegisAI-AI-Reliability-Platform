import { useCallback, useState } from 'react'
import { api } from '../api'
import { GuidedPath } from '../components/GuidedPath'
import type { Step } from '../components/GuidedPath'
import {
  DemoBanner,
  ErrorState,
  Loading,
  Tag,
  Verdict,
} from '../components/States'
import { percent, seconds } from '../format'
import type {
  DemoStatus,
  DocumentRecord,
  EvaluationDetail,
  EvaluationRun,
  Incident,
  Release,
  Telemetry,
} from '../types'
import { useAction, useApi } from '../useApi'

function guideSteps(demo: DemoStatus): Step[] {
  return [
    { name: 'Ask', detail: 'Read a cited answer', href: '#/chat' },
    {
      name: 'Evaluate',
      detail: 'See how it scored',
      href: `#/evaluations/${demo.run_ids.current}`,
    },
    { name: 'Inspect', detail: 'Check live telemetry', href: '#/telemetry' },
    {
      name: 'Diagnose',
      detail: 'Open a simulated failure',
      href: `#/incidents/${demo.incident_id}`,
    },
  ]
}

function Start({ onLoaded }: { onLoaded: () => void }) {
  const load = useAction(
    useCallback(() => api('/demo/load', { method: 'POST' }), []),
  )
  const explore = async () => {
    if ((await load.run()) !== null) onLoaded()
  }

  return (
    <section className="start" aria-labelledby="start-title">
      <h2 id="start-title">Nothing is being measured yet</h2>
      <p>
        AegisAI evaluates an AI application, watches it run and diagnoses its
        failures. Start with sample data, or bring your own documents.
      </p>
      <div className="start-options">
        <div>
          <h3>Explore the demo</h3>
          <p>
            Real evaluation runs, a release history and a simulated incident.
            Works with no model installed.
          </p>
          <button type="button" onClick={explore} disabled={load.loading}>
            {load.loading ? 'Loading demo data…' : 'Load demo data'}
          </button>
        </div>
        <div>
          <h3>Use your own documents</h3>
          <p>Upload Markdown or text files. This needs Ollama running.</p>
          <a className="button-secondary" href="#/documents">
            Go to Documents
          </a>
        </div>
      </div>
      {load.error && <ErrorState error={load.error} />}
    </section>
  )
}

function Readout({
  label,
  value,
  note,
}: {
  label: string
  value: React.ReactNode
  note?: React.ReactNode
}) {
  return (
    <div className="readout">
      <dt>{label}</dt>
      <dd>
        <span className="readout-value">{value}</span>
        {note && <span className="readout-note">{note}</span>}
      </dd>
    </div>
  )
}

function measured(
  telemetry: Telemetry | null,
  value: number | null | undefined,
  format: (value: number) => string,
): string {
  if (telemetry === null || telemetry.not_checked.length > 0) return 'Not checked'
  return value === null || value === undefined ? 'No traffic' : format(value)
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
  detail,
  open,
  release,
}: {
  telemetry: Telemetry | null
  run: EvaluationRun | undefined
  detail: EvaluationDetail | null
  open: Incident[] | null
  release: Release | undefined
}) {
  const recall = run?.metrics?.recall_at_k
  const demoTag = run?.source === 'demo' ? <Tag>Demo</Tag> : undefined
  return (
    <dl className="readouts">
      <Readout label="Health" value={health(telemetry)} />
      <Readout
        label="Recall@k"
        value={typeof recall === 'number' ? recall.toFixed(2) : 'No evaluation'}
        note={demoTag}
      />
      <Readout
        label="Quality gate"
        value={
          detail?.gate ? <Verdict passed={detail.gate.passed} /> : 'No result'
        }
        note={demoTag}
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
        note={release?.source === 'demo' ? <Tag>Demo</Tag> : undefined}
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

function Content({ onChanged }: { onChanged: () => void }) {
  const demo = useApi<DemoStatus>('/demo')
  const runs = useApi<{ runs: EvaluationRun[] }>('/evaluations?limit=1')
  const documents = useApi<{ documents: DocumentRecord[] }>('/documents')
  const telemetry = useApi<Telemetry>('/telemetry')
  const incidents = useApi<{ incidents: Incident[] }>(
    '/incidents?status=open&limit=5',
  )
  const releases = useApi<{ releases: Release[] }>('/releases?status=active')
  const run = runs.data?.runs[0]
  const detail = useApi<EvaluationDetail>(run ? `/evaluations/${run.id}` : null)

  const blocking = demo.error ?? runs.error
  if (blocking) return <ErrorState error={blocking} onRetry={onChanged} />
  if (demo.data === null || runs.data === null || documents.loading) {
    return <Loading label="Loading the overview…" />
  }

  const nothingYet =
    !demo.data.loaded &&
    runs.data.runs.length === 0 &&
    (documents.data?.documents.length ?? 0) === 0
  if (nothingYet) return <Start onLoaded={onChanged} />

  const active = releases.data?.releases ?? []
  const release = active.find((r) => r.source === 'live') ?? active[0]
  return (
    <>
      {demo.data.loaded && <DemoBanner />}
      {demo.data.loaded && <GuidedPath steps={guideSteps(demo.data)} />}
      <Readouts
        telemetry={telemetry.data}
        run={run}
        detail={detail.data}
        open={incidents.data?.incidents ?? null}
        release={release}
      />
      <Incidents incidents={incidents.data?.incidents ?? []} />
    </>
  )
}

export default function Overview() {
  // A new key reads everything again after demo data is loaded.
  const [version, setVersion] = useState(0)
  return (
    <>
      <h1>Overview</h1>
      <Content key={version} onChanged={() => setVersion((v) => v + 1)} />
    </>
  )
}
