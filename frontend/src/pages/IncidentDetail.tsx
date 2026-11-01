import { useCallback } from 'react'
import { api } from '../api'
import { ErrorState, Loading, Tag } from '../components/States'
import { when, words } from '../format'
import type { Fact, Incident } from '../types'
import { useAction, useApi } from '../useApi'

function Reference({ value }: { value: string }) {
  // Only web links become links: a reference can also be a path or a log search.
  return /^https?:\/\//.test(value) ? (
    <a href={value}>Open the evidence</a>
  ) : (
    <code>{value}</code>
  )
}

function Facts({ facts }: { facts: Fact[] }) {
  return (
    <ul className="evidence">
      {facts.map((fact) => (
        <li key={fact.statement}>
          <Tag>{fact.source}</Tag> {fact.statement}
          {fact.reference && (
            <>
              {' '}
              <Reference value={fact.reference} />
            </>
          )}
        </li>
      ))}
    </ul>
  )
}

export default function IncidentDetail({ id }: { id: string }) {
  const incident = useApi<Incident>(`/incidents/${id}`)
  const { reload } = incident
  const resolve = useAction(
    useCallback(
      () => api(`/incidents/${id}/resolve`, { method: 'POST' }),
      [id],
    ),
  )
  if (incident.error) {
    return <ErrorState error={incident.error} onRetry={reload} />
  }
  if (incident.data === null) return <Loading label="Loading the incident…" />
  const { data } = incident
  const report = data.report
  return (
    <>
      <p className="meta">
        <a href="#/incidents">Incidents</a>
      </p>
      <h1>
        {words(data.type)}{' '}
        {data.source === 'simulated' && <Tag>Simulated</Tag>}
      </h1>
      <p className="meta">
        {words(data.severity)} · {words(data.status)} · last seen{' '}
        {when(data.last_seen)}
      </p>
      <p className="notice">
        Diagnosis is rule-based. No model is involved, and nothing here has been
        run.
      </p>
      {report && (
        <>
          <p className="symptom">{report.symptom}</p>

          <section aria-labelledby="facts-title">
            <h2 id="facts-title">Observed facts</h2>
            <p className="meta">Read directly from metrics, logs and alerts.</p>
            <Facts facts={report.facts} />
          </section>

          <section className="inferred" aria-labelledby="hypotheses-title">
            <h2 id="hypotheses-title">Hypotheses</h2>
            <p className="meta">Inferred from the facts, not confirmed.</p>
            {report.hypotheses.length === 0 && <p>None.</p>}
            <ul className="evidence">
              {report.hypotheses.map((hypothesis) => (
                <li key={hypothesis.cause}>
                  <Tag>{hypothesis.confidence} confidence</Tag> {hypothesis.cause}
                  <p className="meta">{hypothesis.basis}</p>
                </li>
              ))}
            </ul>
          </section>

          <section aria-labelledby="uncertainty-title">
            <h2 id="uncertainty-title">Uncertainty</h2>
            <p>{report.uncertainty}</p>
          </section>

          <section aria-labelledby="actions-title">
            <h2 id="actions-title">Suggested actions</h2>
            <ol>
              {report.suggested_actions.map((action) => (
                <li key={action}>{action}</li>
              ))}
            </ol>
            <p className="meta">
              Runbook: <code>{report.runbook}</code>
            </p>
          </section>
        </>
      )}
      <p className="links">
        <a href={`/api/v1/incidents/${id}/report`}>Markdown report</a>
      </p>
      {data.status === 'open' && (
        <button
          type="button"
          disabled={resolve.loading}
          onClick={async () => {
            if ((await resolve.run()) !== null) reload()
          }}
        >
          Resolve incident
        </button>
      )}
      {resolve.error && <ErrorState error={resolve.error} />}
    </>
  )
}
