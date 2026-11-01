import { useCallback, useState } from 'react'
import type { FormEvent } from 'react'
import { api } from '../api'
import { MetricDeltas } from '../components/Metrics'
import {
  DemoBanner,
  Empty,
  ErrorState,
  Loading,
  Tag,
  Verdict,
} from '../components/States'
import { when, words } from '../format'
import { isId, useRoute } from '../route'
import type { EvaluationRun, Release } from '../types'
import { useAction, useApi } from '../useApi'

const STAGES = ['Candidate', 'Canary', 'Promoted', 'Rolled back']

function Lifecycle() {
  return (
    <ol className="lifecycle" aria-label="Release lifecycle">
      {STAGES.map((stage) => (
        <li key={stage}>{stage}</li>
      ))}
    </ol>
  )
}

function GateCell({ passed }: { passed: boolean | null }) {
  return passed === null ? <>Not run</> : <Verdict passed={passed} />
}

function CreateForm({ onCreated }: { onCreated: (id: string) => void }) {
  const runs = useApi<{ runs: EvaluationRun[] }>('/evaluations?limit=50')
  const [version, setVersion] = useState('')
  const [runId, setRunId] = useState('')
  const create = useAction(
    useCallback(
      (body: { version: string; evaluation_run_id: string }) =>
        api<Release>('/releases', { method: 'POST', json: body }),
      [],
    ),
  )
  const done = (runs.data?.runs ?? []).filter((run) => run.status === 'done')
  if (done.length === 0) return null
  const selected = runId || (done[0] as EvaluationRun).id

  const submit = async (event: FormEvent) => {
    event.preventDefault()
    const release = await create.run({ version, evaluation_run_id: selected })
    if (release) {
      setVersion('')
      onCreated(release.id)
    }
  }

  return (
    <section aria-labelledby="create-title">
      <h2 id="create-title">New candidate</h2>
      <form className="row-form" onSubmit={submit}>
        <div>
          <label htmlFor="version">Version</label>
          <input
            id="version"
            required
            maxLength={64}
            pattern="[A-Za-z0-9._\-]+"
            title="Letters, digits, dots, dashes and underscores"
            value={version}
            onChange={(e) => setVersion(e.target.value)}
          />
        </div>
        <div>
          <label htmlFor="run">Completed evaluation</label>
          <select
            id="run"
            value={selected}
            onChange={(e) => setRunId(e.target.value)}
          >
            {done.map((run) => (
              <option key={run.id} value={run.id}>
                {run.label} · {run.id.slice(0, 8)}
                {run.source === 'demo' ? ' · demo' : ''}
              </option>
            ))}
          </select>
        </div>
        <button type="submit" disabled={create.loading}>
          Create candidate
        </button>
      </form>
      {create.error && <ErrorState error={create.error} />}
    </section>
  )
}

function Confirm({
  action,
  question,
  busy,
  onConfirm,
}: {
  action: string
  question: string
  busy: boolean
  onConfirm: () => void
}) {
  const [asking, setAsking] = useState(false)
  if (!asking) {
    return (
      <button type="button" onClick={() => setAsking(true)}>
        {action}
      </button>
    )
  }
  return (
    <div className="confirm" role="group" aria-label={`Confirm: ${action}`}>
      <p>{question}</p>
      <button type="button" disabled={busy} onClick={onConfirm}>
        Yes, {action.toLowerCase()}
      </button>{' '}
      <button
        type="button"
        className="button-outline"
        onClick={() => setAsking(false)}
      >
        Cancel
      </button>
    </div>
  )
}

function RollbackForm({
  release,
  targets,
  onChanged,
}: {
  release: Release
  targets: Release[]
  onChanged: () => void
}) {
  const [target, setTarget] = useState(targets[0]?.id ?? '')
  const [reason, setReason] = useState('')
  const rollback = useAction(
    useCallback(
      () =>
        api(`/releases/${release.id}/rollback`, {
          method: 'POST',
          json: { target_release_id: target, reason },
        }),
      [release.id, target, reason],
    ),
  )
  if (targets.length === 0) {
    return <p>No earlier release is available to roll back to.</p>
  }
  const name = targets.find((candidate) => candidate.id === target)?.version
  return (
    <>
      <label htmlFor="target">Roll back to</label>
      <select
        id="target"
        value={target}
        onChange={(e) => setTarget(e.target.value)}
      >
        {targets.map((candidate) => (
          <option key={candidate.id} value={candidate.id}>
            {candidate.version}
          </option>
        ))}
      </select>
      <label htmlFor="reason">Reason</label>
      <input
        id="reason"
        maxLength={500}
        value={reason}
        onChange={(e) => setReason(e.target.value)}
      />
      {reason.trim() === '' ? (
        <p className="meta">A rollback needs a reason.</p>
      ) : (
        <Confirm
          action={`Roll back to ${name}`}
          question={`Roll back ${release.version} to ${name}? This records the decision. Live settings do not change.`}
          busy={rollback.loading}
          onConfirm={async () => {
            if ((await rollback.run()) !== null) onChanged()
          }}
        />
      )}
      {rollback.error && <ErrorState error={rollback.error} />}
    </>
  )
}

function Actions({
  release,
  all,
  onChanged,
}: {
  release: Release
  all: Release[]
  onChanged: () => void
}) {
  const act = useAction(
    useCallback(
      (step: string) =>
        api(`/releases/${release.id}/${step}`, { method: 'POST', json: {} }),
      [release.id],
    ),
  )
  const run = async (step: string) => {
    if ((await act.run(step)) !== null) onChanged()
  }

  let controls = null
  if (release.status === 'candidate') {
    controls = (
      <button type="button" disabled={act.loading} onClick={() => run('canary')}>
        Run canary
      </button>
    )
  } else if (release.status === 'canary_passed') {
    controls = (
      <Confirm
        action="Promote"
        question={`Promote ${release.version}? The gate is checked again first. This records the decision. Live settings do not change.`}
        busy={act.loading}
        onConfirm={() => run('promote')}
      />
    )
  } else if (release.status === 'active') {
    controls = (
      <RollbackForm
        release={release}
        targets={all.filter(
          (other) =>
            other.status === 'superseded' && other.source === release.source,
        )}
        onChanged={onChanged}
      />
    )
  } else if (release.status === 'rejected') {
    controls = <p>This release failed its gate, so it cannot be promoted.</p>
  }
  if (controls === null) return null
  return (
    <section aria-labelledby="actions-title">
      <h3 id="actions-title">Actions</h3>
      {controls}
      {act.error && <ErrorState error={act.error} />}
    </section>
  )
}

function ReleaseDetail({
  id,
  all,
  onChanged,
}: {
  id: string
  all: Release[]
  onChanged: () => void
}) {
  const release = useApi<Release>(`/releases/${id}`)
  if (release.error) {
    return <ErrorState error={release.error} onRetry={release.reload} />
  }
  if (release.data === null) return <Loading label="Loading the release…" />
  const { data } = release
  const comparison = data.comparison
  // Only while the decision is open: a rejected release is over its limits.
  const regressed =
    data.status === 'canary_passed' &&
    comparison?.metrics.some((m) => m.direction === 'regressed')
  return (
    <section className="result" aria-labelledby="release-title">
      <h2 id="release-title">
        {data.version} {data.source === 'demo' && <Tag>Demo</Tag>}
      </h2>
      <p className="meta">
        {words(data.status)} ·{' '}
        <a href={`#/evaluations/${data.evaluation_run_id}`}>Evaluation run</a>
      </p>

      {data.gate_failures.length > 0 && (
        <>
          <h3>Gate failures</h3>
          <ul className="failures">
            {data.gate_failures.map((failure) => (
              <li key={failure}>{failure}</li>
            ))}
          </ul>
        </>
      )}

      {comparison && (
        <>
          <h3>Compared with the baseline</h3>
          {comparison.status === 'no_baseline' && (
            <p>There was no earlier release to compare with.</p>
          )}
          {comparison.status === 'dataset_changed' && (
            <p className="hint">
              The dataset changed, so the metrics are not compared.
            </p>
          )}
          {regressed && (
            <p className="hint">
              Some metrics regressed inside the gate limits. This does not block
              promotion, so decide before promoting.
            </p>
          )}
          {comparison.metrics.length > 0 && (
            <MetricDeltas
              rows={comparison.metrics}
              baselineLabel="Baseline"
              candidateLabel={data.version}
            />
          )}
        </>
      )}

      <Actions
        key={data.status}
        release={data}
        all={all}
        onChanged={() => {
          release.reload()
          onChanged()
        }}
      />

      <h3>History</h3>
      <ol className="history">
        {(data.events ?? []).map((event, index) => (
          <li key={index}>
            <strong>{words(event.event)}</strong>
            {event.to_status && ` to ${words(event.to_status)}`}
            <span className="meta">
              {' '}
              {when(event.created_at)} · {event.actor}
              {event.request_id && ` · request ${event.request_id}`}
            </span>
            {event.reason && <p className="meta">{event.reason}</p>}
          </li>
        ))}
      </ol>
    </section>
  )
}

export default function Releases() {
  const { query } = useRoute()
  const releases = useApi<{ releases: Release[] }>('/releases?limit=50')
  const requested = query.get('id')
  const selected = isId(requested) ? requested : null
  const list = releases.data?.releases ?? []

  return (
    <>
      <h1>Releases</h1>
      <p className="notice">
        <strong>Simulated release.</strong> Promoting or rolling back records the
        decision. It does not change live settings.
      </p>
      {list.some((release) => release.source === 'demo') && <DemoBanner />}
      <Lifecycle />

      {releases.loading && releases.data === null && (
        <Loading label="Loading releases…" />
      )}
      {releases.error && (
        <ErrorState error={releases.error} onRetry={releases.reload} />
      )}
      {releases.data && list.length === 0 && (
        <Empty title="No releases yet">
          <p>Create a candidate from a completed evaluation below.</p>
        </Empty>
      )}
      {list.length > 0 && (
        <div className="table-scroll">
          <table>
            <caption>Newest first</caption>
            <thead>
              <tr>
                <th scope="col">Release</th>
                <th scope="col">Model and prompt</th>
                <th scope="col">Config</th>
                <th scope="col">Gate</th>
                <th scope="col">Status</th>
              </tr>
            </thead>
            <tbody>
              {list.map((release) => (
                <tr key={release.id}>
                  <td>
                    <a
                      href={`#/releases?id=${release.id}`}
                      aria-current={release.id === selected ? 'true' : undefined}
                    >
                      {release.version}
                    </a>{' '}
                    {release.source === 'demo' && <Tag>Demo</Tag>}
                  </td>
                  <td>
                    {release.chat_model} / {release.prompt_version}
                  </td>
                  <td>
                    top_k {release.top_k}, min_score {release.min_score}
                  </td>
                  <td>
                    <GateCell passed={release.gate_passed} />
                  </td>
                  <td>{words(release.status)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {selected && (
        <ReleaseDetail
          key={selected}
          id={selected}
          all={list}
          onChanged={releases.reload}
        />
      )}

      <CreateForm
        onCreated={(id) => {
          releases.reload()
          window.location.hash = `#/releases?id=${id}`
        }}
      />
    </>
  )
}
