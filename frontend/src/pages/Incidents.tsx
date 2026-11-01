import { useCallback, useState } from 'react'
import { api } from '../api'
import {
  DemoBanner,
  Empty,
  ErrorState,
  Loading,
  Tag,
} from '../components/States'
import { when, words } from '../format'
import type { DemoStatus, Incident } from '../types'
import { useAction, useApi } from '../useApi'

// Files in data/incidents. The API has no listing, and these rarely change.
const SCENARIOS = [
  'model_timeout',
  'model_unavailable',
  'increased_latency',
  'elevated_api_error_rate',
  'retrieval_quality_regression',
  'unhealthy_dependency',
  'unexplained_error_rate',
]

interface Detected {
  incidents: Incident[]
  not_checked: string[]
}

export default function Incidents() {
  const incidents = useApi<{ incidents: Incident[] }>('/incidents?limit=50')
  const demo = useApi<DemoStatus>('/demo')
  const [scenario, setScenario] = useState(SCENARIOS[0] as string)
  const { reload } = incidents

  const detect = useAction(
    useCallback(() => api<Detected>('/incidents/detect', { method: 'POST' }), []),
  )
  const simulate = useAction(
    useCallback(
      (name: string) =>
        api('/incidents/simulate', { method: 'POST', json: { scenario: name } }),
      [],
    ),
  )
  const list = incidents.data?.incidents ?? []
  const actionError = detect.error ?? simulate.error

  return (
    <>
      <h1>Incidents</h1>
      {demo.data?.loaded && <DemoBanner />}
      <div className="row-form">
        <button
          type="button"
          disabled={detect.loading}
          onClick={async () => {
            if (await detect.run()) reload()
          }}
        >
          {detect.loading ? 'Checking…' : 'Check for incidents'}
        </button>
        <div>
          <label htmlFor="scenario">Scenario</label>
          <select
            id="scenario"
            value={scenario}
            onChange={(e) => setScenario(e.target.value)}
          >
            {SCENARIOS.map((name) => (
              <option key={name} value={name}>
                {words(name)}
              </option>
            ))}
          </select>
        </div>
        <button
          type="button"
          className="button-outline"
          disabled={simulate.loading}
          onClick={async () => {
            if ((await simulate.run(scenario)) !== null) reload()
          }}
        >
          Simulate
        </button>
      </div>
      {actionError && <ErrorState error={actionError} />}
      {detect.data && (
        <p role="status">
          Found {detect.data.incidents.length}.
          {detect.data.not_checked.length > 0 &&
            ` Not checked: ${detect.data.not_checked.join(', ')}.`}
        </p>
      )}

      {incidents.loading && incidents.data === null && (
        <Loading label="Loading incidents…" />
      )}
      {incidents.error && <ErrorState error={incidents.error} onRetry={reload} />}
      {incidents.data && list.length === 0 && (
        <Empty title="No incidents recorded">
          <p>Check for incidents, or simulate a scenario to see a diagnosis.</p>
        </Empty>
      )}
      {list.length > 0 && (
        <div className="table-scroll">
          <table>
            <caption>Most recently seen first</caption>
            <thead>
              <tr>
                <th scope="col">Incident</th>
                <th scope="col">Severity</th>
                <th scope="col">Status</th>
                <th scope="col">Last seen</th>
              </tr>
            </thead>
            <tbody>
              {list.map((incident) => (
                <tr key={incident.id}>
                  <td>
                    <a href={`#/incidents/${incident.id}`}>{words(incident.type)}</a>{' '}
                    {incident.source === 'simulated' && <Tag>Simulated</Tag>}
                  </td>
                  <td>{words(incident.severity)}</td>
                  <td>{words(incident.status)}</td>
                  <td>{when(incident.last_seen)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </>
  )
}
