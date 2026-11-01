import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it } from 'vitest'
import { apiError, stubApi } from '../test/stub'
import type { Release } from '../types'
import Releases from './Releases'

const BASE: Release = {
  id: 'r1',
  version: 'demo-1',
  source: 'demo',
  status: 'superseded',
  simulated: true,
  evaluation_run_id: 'run-1',
  prompt_version: '2',
  chat_model: 'qwen3.5:4b',
  top_k: 4,
  min_score: 0.7,
  gate_passed: true,
  gate_failures: [],
  comparison: null,
  events: [],
}
const ACTIVE: Release = { ...BASE, id: 'r2', version: 'demo-2', status: 'active' }
const REJECTED: Release = {
  ...BASE,
  id: 'r3',
  version: 'demo-3',
  status: 'rejected',
  gate_passed: false,
  gate_failures: ['recall_at_k: 0.9412 < 0.95'],
}
const CANDIDATE: Release = {
  ...BASE,
  id: 'r4',
  version: 'next',
  status: 'candidate',
  gate_passed: null,
}
const PASSED: Release = { ...BASE, id: 'r5', version: 'ready', status: 'canary_passed' }
const LIVE_OLD: Release = { ...BASE, id: 'r6', version: 'live-1', source: 'live' }
const RUNS = 'GET /evaluations?limit=50'
const LIST = 'GET /releases?limit=50'
const ALL = [REJECTED, ACTIVE, BASE, CANDIDATE, PASSED, LIVE_OLD]

function open(release: Release, extra: Record<string, unknown> = {}) {
  window.location.hash = `#/releases?id=${release.id}`
  return stubApi({
    [LIST]: { releases: ALL },
    [RUNS]: { runs: [] },
    [`GET /releases/${release.id}`]: release,
    ...extra,
  })
}

describe('Releases', () => {
  it('states that releases are simulated, on every view', async () => {
    stubApi({ [LIST]: { releases: [] }, [RUNS]: { runs: [] } })
    render(<Releases />)
    expect(await screen.findByText('No releases yet')).toBeTruthy()
    expect(screen.getByText(/does not change live settings/)).toBeTruthy()
  })

  it('lists releases with their model, config, gate and status', async () => {
    stubApi({ [LIST]: { releases: [REJECTED] }, [RUNS]: { runs: [] } })
    render(<Releases />)
    const row = (await screen.findByRole('link', { name: 'demo-3' })).closest('tr')
    const cells = within(row as HTMLElement)
    expect(cells.getByText('qwen3.5:4b / 2')).toBeTruthy()
    expect(cells.getByText('top_k 4, min_score 0.7')).toBeTruthy()
    expect(cells.getByText('FAIL')).toBeTruthy()
    expect(cells.getByText('Rejected')).toBeTruthy()
    expect(cells.getByText('Demo')).toBeTruthy()
  })

  it('shows a gate that has not run', async () => {
    stubApi({ [LIST]: { releases: [CANDIDATE] }, [RUNS]: { runs: [] } })
    render(<Releases />)
    expect(await screen.findByText('Not run')).toBeTruthy()
  })

  it('offers no promote control on a rejected release', async () => {
    open(REJECTED)
    render(<Releases />)
    expect(await screen.findByText(/cannot be promoted/)).toBeTruthy()
    expect(screen.queryByRole('button', { name: /Promote/ })).toBeNull()
    expect(screen.getByText('recall_at_k: 0.9412 < 0.95')).toBeTruthy()
  })

  it('offers no promote control on a candidate', async () => {
    open(CANDIDATE)
    render(<Releases />)
    expect(await screen.findByRole('button', { name: 'Run canary' })).toBeTruthy()
    expect(screen.queryByRole('button', { name: /Promote/ })).toBeNull()
  })

  it('runs a canary', async () => {
    const calls = open(CANDIDATE, { 'POST /releases/r4/canary': PASSED })
    render(<Releases />)
    await userEvent.click(await screen.findByRole('button', { name: 'Run canary' }))
    await screen.findByRole('button', { name: 'Run canary' })
    expect(calls.some((c) => c.key === 'POST /releases/r4/canary')).toBe(true)
  })

  it('asks before promoting', async () => {
    const calls = open(PASSED, { 'POST /releases/r5/promote': ACTIVE })
    render(<Releases />)
    await userEvent.click(await screen.findByRole('button', { name: 'Promote' }))
    expect(calls.some((c) => c.key === 'POST /releases/r5/promote')).toBe(false)
    expect(screen.getByText(/gate is checked again/)).toBeTruthy()
    await userEvent.click(screen.getByRole('button', { name: 'Yes, promote' }))
    await screen.findByRole('link', { name: 'ready' })
    expect(calls.some((c) => c.key === 'POST /releases/r5/promote')).toBe(true)
  })

  it('cancels a promotion', async () => {
    const calls = open(PASSED)
    render(<Releases />)
    await userEvent.click(await screen.findByRole('button', { name: 'Promote' }))
    await userEvent.click(screen.getByRole('button', { name: 'Cancel' }))
    expect(screen.getByRole('button', { name: 'Promote' })).toBeTruthy()
    expect(calls.some((c) => c.key.startsWith('POST'))).toBe(false)
  })

  it('shows a refused promotion', async () => {
    open(PASSED, {
      'POST /releases/r5/promote': () =>
        apiError(409, 'gate_failed', 'recall_at_k: not measured', {
          'X-Request-ID': 'req-2',
        }),
    })
    render(<Releases />)
    await userEvent.click(await screen.findByRole('button', { name: 'Promote' }))
    await userEvent.click(screen.getByRole('button', { name: 'Yes, promote' }))
    const alert = await screen.findByRole('alert')
    expect(alert.textContent).toContain('not measured')
    expect(alert.textContent).toContain('req-2')
  })

  it('needs a reason before a rollback', async () => {
    open(ACTIVE)
    render(<Releases />)
    expect(await screen.findByText('A rollback needs a reason.')).toBeTruthy()
    expect(screen.queryByRole('button', { name: /Roll back/ })).toBeNull()
  })

  it('offers only superseded releases of the same source', async () => {
    open(ACTIVE)
    render(<Releases />)
    const select = await screen.findByLabelText('Roll back to')
    const names = within(select).getAllByRole('option').map((o) => o.textContent)
    expect(names).toEqual(['demo-1'])
  })

  it('rolls back to the named release after asking', async () => {
    const calls = open(ACTIVE, { 'POST /releases/r2/rollback': ACTIVE })
    render(<Releases />)
    await userEvent.type(await screen.findByLabelText('Reason'), 'latency')
    await userEvent.click(
      screen.getByRole('button', { name: 'Roll back to demo-1' }),
    )
    expect(calls.some((c) => c.key === 'POST /releases/r2/rollback')).toBe(false)
    await userEvent.click(
      screen.getByRole('button', { name: 'Yes, roll back to demo-1' }),
    )
    await screen.findByRole('link', { name: 'demo-2' })
    const call = calls.find((c) => c.key === 'POST /releases/r2/rollback')
    expect(call?.init.body).toBe('{"target_release_id":"r1","reason":"latency"}')
  })

  it('says when there is nothing to roll back to', async () => {
    window.location.hash = '#/releases?id=r2'
    stubApi({
      [LIST]: { releases: [ACTIVE] },
      [RUNS]: { runs: [] },
      'GET /releases/r2': ACTIVE,
    })
    render(<Releases />)
    expect(await screen.findByText(/No earlier release/)).toBeTruthy()
  })

  it('warns about regressions without blocking', async () => {
    open({
      ...PASSED,
      comparison: {
        status: 'comparable',
        baseline_release_id: 'r2',
        metrics: [
          {
            name: 'latency_ms_p95',
            candidate: 3000,
            baseline: 2000,
            delta: 1000,
            direction: 'regressed',
          },
        ],
      },
    })
    render(<Releases />)
    expect(await screen.findByText(/regressed inside the gate limits/)).toBeTruthy()
    expect(screen.getByRole('button', { name: 'Promote' })).toBeTruthy()
  })

  it('gives no promotion advice once the decision is made', async () => {
    const comparison = {
      status: 'comparable' as const,
      baseline_release_id: 'r2',
      metrics: [
        {
          name: 'recall_at_k',
          candidate: 0.9,
          baseline: 0.98,
          delta: -0.08,
          direction: 'regressed' as const,
        },
      ],
    }
    open({ ...REJECTED, comparison })
    render(<Releases />)
    expect(await screen.findByText('Regressed')).toBeTruthy()
    expect(screen.queryByText(/inside the gate limits/)).toBeNull()
  })

  it('explains a comparison that could not be made', async () => {
    open({
      ...PASSED,
      comparison: {
        status: 'dataset_changed',
        baseline_release_id: 'r2',
        metrics: [],
      },
    })
    render(<Releases />)
    expect(await screen.findByText(/dataset changed/)).toBeTruthy()
  })

  it('shows the audit history', async () => {
    open({
      ...REJECTED,
      events: [
        {
          event: 'promotion_refused',
          from_status: 'rejected',
          to_status: 'rejected',
          actor: 'local-no-auth',
          reason: 'trying it',
          request_id: 'req-8',
          created_at: '2026-11-01T00:00:00Z',
        },
      ],
    })
    render(<Releases />)
    expect(await screen.findByText('Promotion refused')).toBeTruthy()
    expect(screen.getByText(/local-no-auth/).textContent).toContain('req-8')
    expect(screen.getByText('trying it')).toBeTruthy()
  })

  it('creates a candidate from a completed run', async () => {
    const calls = stubApi({
      [LIST]: { releases: [] },
      [RUNS]: {
        runs: [
          { id: 'run-a', label: 'baseline', status: 'done', source: 'live' },
          { id: 'run-b', label: 'broken', status: 'failed', source: 'live' },
        ],
      },
      'POST /releases': { ...CANDIDATE, id: 'r9' },
      'GET /releases/r9': { ...CANDIDATE, id: 'r9' },
    })
    render(<Releases />)
    await userEvent.type(await screen.findByLabelText('Version'), '1.0.0')
    const select = screen.getByLabelText('Completed evaluation')
    expect(within(select).getAllByRole('option')).toHaveLength(1)
    await userEvent.click(screen.getByRole('button', { name: 'Create candidate' }))
    await screen.findByRole('heading', { name: 'New candidate' })
    const post = calls.find((c) => c.key === 'POST /releases')
    expect(post?.init.body).toBe('{"version":"1.0.0","evaluation_run_id":"run-a"}')
  })

  it('explains a refused candidate', async () => {
    stubApi({
      [LIST]: { releases: [] },
      [RUNS]: {
        runs: [{ id: 'run-a', label: 'baseline', status: 'done', source: 'live' }],
      },
      'POST /releases': () =>
        apiError(409, 'version_exists', 'release 1.0.0 already exists'),
    })
    render(<Releases />)
    await userEvent.type(await screen.findByLabelText('Version'), '1.0.0')
    await userEvent.click(screen.getByRole('button', { name: 'Create candidate' }))
    expect((await screen.findByRole('alert')).textContent).toContain('already exists')
  })

  it('shows a failure with a retry', async () => {
    stubApi({
      [LIST]: () => apiError(503, 'database_unavailable', 'database down'),
      [RUNS]: { runs: [] },
    })
    render(<Releases />)
    expect((await screen.findByRole('alert')).textContent).toContain('database down')
    expect(screen.getByRole('button', { name: 'Try again' })).toBeTruthy()
  })
})
