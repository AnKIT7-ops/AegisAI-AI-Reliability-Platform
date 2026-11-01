import { Readout } from '../components/Metrics'
import { ErrorState, Loading } from '../components/States'
import { measured, percent, seconds, when, words } from '../format'
import type { Telemetry as Summary } from '../types'
import { useApi } from '../useApi'

export default function Telemetry() {
  const summary = useApi<Summary>('/telemetry')
  if (summary.error) {
    return (
      <>
        <h1>Telemetry</h1>
        <ErrorState error={summary.error} onRetry={summary.reload} />
      </>
    )
  }
  if (summary.data === null) return <Loading label="Reading telemetry…" />
  const { data } = summary
  const alerts = data.firing_alerts
  const failures = Object.entries(data.model_failures ?? {})
  return (
    <>
      <h1>Telemetry</h1>
      <p className="meta">
        Last 5 minutes, read at {when(data.observed_at)}.{' '}
        <button type="button" className="quiet" onClick={summary.reload}>
          Refresh
        </button>
      </p>
      {data.not_checked.length > 0 && (
        <p className="hint">
          Prometheus could not be read, so these values are unknown:{' '}
          {data.not_checked.join(', ')}. Check it with{' '}
          <code>docker compose ps</code>.
        </p>
      )}
      <dl className="readouts">
        <Readout
          label="Database"
          value={data.database === 'ok' ? 'Reachable' : 'Unavailable'}
        />
        <Readout
          label="Requests"
          value={measured(data, data.request_rate_per_s, (v) => `${v.toFixed(2)}/s`)}
        />
        <Readout
          label="Error rate"
          value={measured(data, data.error_ratio, percent)}
        />
        <Readout label="Chat p95" value={measured(data, data.chat_p95_s, seconds)} />
        <Readout
          label="Model p95"
          value={measured(data, data.model_p95_s, seconds)}
        />
        <Readout
          label="Retrieval p95"
          value={measured(data, data.retrieval_p95_s, seconds)}
        />
      </dl>

      <section aria-labelledby="alerts-title">
        <h2 id="alerts-title">Firing alerts</h2>
        {alerts === null && <p>Not checked.</p>}
        {alerts?.length === 0 && <p>No alerts are firing.</p>}
        {alerts && alerts.length > 0 && (
          <ul className="rows">
            {alerts.map((alert) => (
              <li key={alert}>
                <code>{alert}</code>
              </li>
            ))}
          </ul>
        )}
      </section>

      {failures.length > 0 && (
        <section aria-labelledby="failures-title">
          <h2 id="failures-title">Failed model calls</h2>
          <ul className="rows">
            {failures.map(([outcome, count]) => (
              <li key={outcome}>
                {words(outcome)}: {Math.round(count)}
              </li>
            ))}
          </ul>
        </section>
      )}

      <section aria-labelledby="graphs-title">
        <h2 id="graphs-title">Graphs and queries</h2>
        <p className="links">
          <a href={data.grafana_url}>Open Grafana</a>
          <a href={data.prometheus_url}>Open Prometheus</a>
        </p>
      </section>
    </>
  )
}
