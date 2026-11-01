import { useState } from 'react'
import { MetricBar } from '../components/Metrics'
import {
  DemoBanner,
  ErrorState,
  Loading,
  Tag,
  Verdict,
} from '../components/States'
import { isQualityMetric, when, words } from '../format'
import type { EvaluationDetail as Detail, EvaluationResult } from '../types'
import { useApi } from '../useApi'

function outcome({ category, scores }: EvaluationResult): string {
  if (scores.error) return 'Error'
  if (category === 'injection') {
    return scores.resisted === false ? 'Followed the injection' : 'Resisted'
  }
  if (category === 'unanswerable') return scores.refused ? 'Refused' : 'Answered'
  if (scores.refused) return 'Refused'
  return scores.correct ? 'Correct' : 'Incorrect'
}

function Examples({ results }: { results: EvaluationResult[] }) {
  const [category, setCategory] = useState('all')
  const categories = [...new Set(results.map((result) => result.category))]
  const shown = results.filter(
    (result) => category === 'all' || result.category === category,
  )
  return (
    <section aria-labelledby="examples-title">
      <h2 id="examples-title">Examples</h2>
      <label htmlFor="category">Category</label>
      <select
        id="category"
        value={category}
        onChange={(e) => setCategory(e.target.value)}
      >
        <option value="all">All ({results.length})</option>
        {categories.map((name) => (
          <option key={name} value={name}>
            {words(name)}
          </option>
        ))}
      </select>
      <ul className="examples">
        {shown.map((result) => (
          <li key={result.example_id}>
            <details>
              <summary>
                <span className="example-outcome">{outcome(result)}</span>
                <span className="example-question">{result.question}</span>
              </summary>
              <p className="answer">{result.answer ?? 'No answer was recorded.'}</p>
              <p className="meta">
                {result.example_id}
                {result.latency_ms !== null && ` · ${result.latency_ms} ms`}
                {result.sources.length > 0 &&
                  ` · ${[...new Set(result.sources)].join(', ')}`}
              </p>
            </details>
          </li>
        ))}
      </ul>
    </section>
  )
}

export default function EvaluationDetail({ id }: { id: string }) {
  const run = useApi<Detail>(`/evaluations/${id}`)
  if (run.error) return <ErrorState error={run.error} onRetry={run.reload} />
  if (run.data === null) return <Loading label="Loading the evaluation…" />
  const { data } = run
  const failures = data.gate?.failures ?? []
  const missed = (name: string) =>
    failures.some((failure) => failure.startsWith(`${name}:`))
  const metrics = Object.entries(data.metrics ?? {}).filter(
    (entry): entry is [string, number] =>
      typeof entry[1] === 'number' && isQualityMetric(entry[0]),
  )
  return (
    <>
      <p className="meta">
        <a href="#/evaluations">Evaluations</a>
      </p>
      <h1>
        {data.label} {data.source === 'demo' && <Tag>Demo</Tag>}
      </h1>
      {data.source === 'demo' && <DemoBanner />}
      <p className="meta">
        {data.chat_model} · prompt {data.prompt_version} · top_k {data.top_k} ·
        min_score {data.min_score} · dataset {data.dataset_version} ·{' '}
        {when(data.created_at)}
      </p>
      {data.error && <p role="alert">{data.error}</p>}

      <section aria-labelledby="gate-title">
        <h2 id="gate-title">Quality gate</h2>
        {data.gate ? (
          <Verdict passed={data.gate.passed} />
        ) : (
          <p>No gate result: this dataset has no limits, or the run did not finish.</p>
        )}
        {failures.length > 0 && (
          <ul className="failures">
            {failures.map((failure) => (
              <li key={failure}>{failure}</li>
            ))}
          </ul>
        )}
      </section>

      {metrics.length > 0 && (
        <section aria-labelledby="metrics-title">
          <h2 id="metrics-title">Metrics</h2>
          <p className="meta">The line on each bar marks its limit.</p>
          <ul className="metrics">
            {metrics.map(([name, value]) => (
              <MetricBar
                key={name}
                name={name}
                value={value}
                limit={data.limits[name]}
                missed={missed(name)}
              />
            ))}
          </ul>
        </section>
      )}

      <p className="links">
        <a href={`/api/v1/evaluations/${id}/report`}>Markdown report</a>
        <a href={`/api/v1/evaluations/${id}/report?format=csv`}>Results as CSV</a>
      </p>

      {data.results.length > 0 && <Examples results={data.results} />}
    </>
  )
}
