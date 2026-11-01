import { useCallback, useEffect, useState } from 'react'
import type { FormEvent } from 'react'
import { api } from '../api'
import {
  DemoBanner,
  Empty,
  ErrorState,
  Loading,
  Tag,
  Verdict,
} from '../components/States'
import { metricValue, when, words } from '../format'
import type { EvaluationRun } from '../types'
import { useAction, useApi } from '../useApi'

const POLL_MS = 5000
const RUNNING = ['pending', 'running']

function CompareForm({ runs }: { runs: EvaluationRun[] }) {
  const done = runs.filter((run) => run.status === 'done')
  const [a, setA] = useState(done[1]?.id ?? '')
  const [b, setB] = useState(done[0]?.id ?? '')
  if (done.length < 2) return null
  const options = done.map((run) => (
    <option key={run.id} value={run.id}>
      {run.label} · {run.id.slice(0, 8)}
      {run.source === 'demo' ? ' · demo' : ''}
    </option>
  ))
  return (
    <section aria-labelledby="compare-title">
      <h2 id="compare-title">Compare two runs</h2>
      <div className="row-form">
        <div>
          <label htmlFor="compare-a">Baseline</label>
          <select id="compare-a" value={a} onChange={(e) => setA(e.target.value)}>
            {options}
          </select>
        </div>
        <div>
          <label htmlFor="compare-b">Run</label>
          <select id="compare-b" value={b} onChange={(e) => setB(e.target.value)}>
            {options}
          </select>
        </div>
        <a className="button-secondary" href={`#/evaluations/compare?a=${a}&b=${b}`}>
          Compare
        </a>
      </div>
    </section>
  )
}

export default function Evaluations() {
  const runs = useApi<{ runs: EvaluationRun[] }>('/evaluations?limit=50')
  const [label, setLabel] = useState('baseline')
  const start = useAction(
    useCallback(
      (value: string) =>
        api<{ id: string }>('/evaluations/run', {
          method: 'POST',
          json: { label: value },
        }),
      [],
    ),
  )
  const { reload } = runs
  const list = runs.data?.runs ?? []
  const active = list.some((run) => RUNNING.includes(run.status))

  useEffect(() => {
    if (!active) return
    const timer = setInterval(reload, POLL_MS)
    return () => clearInterval(timer)
  }, [active, reload])

  const submit = async (event: FormEvent) => {
    event.preventDefault()
    if (await start.run(label)) reload()
  }

  return (
    <>
      <h1>Evaluations</h1>
      {list.some((run) => run.source === 'demo') && <DemoBanner />}
      <form className="row-form" onSubmit={submit}>
        <div>
          <label htmlFor="run-label">Run label</label>
          <input
            id="run-label"
            maxLength={64}
            required
            value={label}
            onChange={(e) => setLabel(e.target.value)}
          />
        </div>
        <button type="submit" disabled={start.loading || active}>
          {active ? 'A run is in progress' : 'Start evaluation'}
        </button>
      </form>
      <p className="meta">A run asks every dataset question, so it needs Ollama.</p>
      {start.error && <ErrorState error={start.error} />}

      {runs.loading && runs.data === null && (
        <Loading label="Loading evaluations…" />
      )}
      {runs.error && <ErrorState error={runs.error} onRetry={reload} />}
      {runs.data && list.length === 0 && (
        <Empty title="No evaluations yet">
          <p>Start one above, or load the demo from Overview.</p>
        </Empty>
      )}
      {list.length > 0 && (
        <div className="table-scroll">
          <table>
            <caption>History, newest first</caption>
            <thead>
              <tr>
                <th scope="col">Run</th>
                <th scope="col">Status</th>
                <th scope="col">Quality gate</th>
                <th scope="col" className="number">
                  Recall@k
                </th>
                <th scope="col">Started</th>
              </tr>
            </thead>
            <tbody>
              {list.map((run) => {
                const recall = run.metrics?.recall_at_k
                return (
                  <tr key={run.id}>
                    <td>
                      <a href={`#/evaluations/${run.id}`}>{run.label}</a>{' '}
                      {run.source === 'demo' && <Tag>Demo</Tag>}
                    </td>
                    <td>{words(run.status)}</td>
                    <td>
                      {run.gate ? <Verdict passed={run.gate.passed} /> : 'No result'}
                    </td>
                    <td className="number">
                      {typeof recall === 'number'
                        ? metricValue('recall_at_k', recall)
                        : '-'}
                    </td>
                    <td>{when(run.created_at)}</td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        </div>
      )}
      <CompareForm key={list.length} runs={list} />
    </>
  )
}
