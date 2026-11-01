import { MetricDeltas } from '../components/Metrics'
import { Empty, ErrorState, Loading, Tag } from '../components/States'
import type { EvaluationRun, RunComparison } from '../types'
import { useApi } from '../useApi'

const ROWS: [string, (run: EvaluationRun) => string][] = [
  ['Chat model', (run) => run.chat_model],
  ['Embedding model', (run) => run.embed_model],
  ['Prompt version', (run) => run.prompt_version],
  ['top_k', (run) => String(run.top_k)],
  ['min_score', (run) => String(run.min_score)],
  ['Dataset', (run) => `${run.dataset_version} (${run.dataset_checksum})`],
]

function Heading({ run }: { run: EvaluationRun }) {
  return (
    <>
      <a href={`#/evaluations/${run.id}`}>{run.label}</a>{' '}
      {run.source === 'demo' && <Tag>Demo</Tag>}
    </>
  )
}

export default function Compare({ query }: { query: URLSearchParams }) {
  const a = query.get('a')
  const b = query.get('b')
  const comparison = useApi<RunComparison>(
    a && b ? `/evaluations/${b}/comparison?baseline=${a}` : null,
  )
  if (!a || !b) {
    return (
      <>
        <h1>Compare runs</h1>
        <Empty title="Choose two runs to compare">
          <p>
            <a href="#/evaluations">Go to Evaluations</a>
          </p>
        </Empty>
      </>
    )
  }
  if (comparison.error) {
    return <ErrorState error={comparison.error} onRetry={comparison.reload} />
  }
  if (comparison.data === null) return <Loading label="Comparing the runs…" />
  const { run, baseline, metrics, status } = comparison.data
  const changed = ROWS.filter(([, read]) => read(run) !== read(baseline))
  return (
    <>
      <p className="meta">
        <a href="#/evaluations">Evaluations</a>
      </p>
      <h1>Compare runs</h1>
      {status === 'dataset_changed' && (
        <p className="hint">
          These runs used different datasets, so their metrics are not compared.
        </p>
      )}
      <div className="table-scroll">
        <table>
          <caption>Configuration</caption>
          <thead>
            <tr>
              <th scope="col">Setting</th>
              <th scope="col">
                Baseline: <Heading run={baseline} />
              </th>
              <th scope="col">
                Run: <Heading run={run} />
              </th>
            </tr>
          </thead>
          <tbody>
            {ROWS.map(([label, read]) => (
              <tr key={label}>
                <td>{label}</td>
                <td>{read(baseline)}</td>
                <td>{read(run)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <p className="meta">
        {changed.length === 0
          ? 'Every setting matches.'
          : `Changed: ${changed.map(([label]) => label).join(', ')}.`}
      </p>
      {metrics.length > 0 && (
        <section aria-labelledby="deltas-title">
          <h2 id="deltas-title">Metrics</h2>
          <MetricDeltas rows={metrics} baselineLabel="Baseline" candidateLabel="Run" />
        </section>
      )}
    </>
  )
}
