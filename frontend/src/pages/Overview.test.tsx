import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it } from 'vitest'
import { apiError, DEMO, NO_DEMO, stubApi, TELEMETRY } from '../test/stub'
import Overview from './Overview'

const EMPTY = {
  'GET /demo': NO_DEMO,
  'GET /evaluations?limit=1': { runs: [] },
  'GET /documents': { documents: [] },
  'GET /telemetry': TELEMETRY,
  'GET /incidents?status=open&limit=5': { incidents: [] },
  'GET /releases?status=active': { releases: [] },
}
const RUN = {
  id: 'run-1',
  label: 'baseline',
  status: 'done',
  source: 'demo',
  metrics: { recall_at_k: 0.9804 },
  gate: { passed: true, failures: [] },
}
const LOADED = {
  ...EMPTY,
  'GET /demo': DEMO,
  'GET /evaluations?limit=1': { runs: [RUN] },
  'GET /incidents?status=open&limit=5': {
    incidents: [
      {
        id: 'incident-1',
        type: 'model_timeout',
        severity: 'warning',
        status: 'open',
        source: 'simulated',
      },
    ],
  },
  'GET /releases?status=active': {
    releases: [{ id: 'r2', version: 'demo-2', source: 'demo', status: 'active' }],
  },
}

function readout(label: string): string {
  const term = screen
    .getAllByText(label)
    .find((element) => element.tagName === 'DT')
  return term?.parentElement?.textContent ?? ''
}

describe('Overview', () => {
  it('shows a loading state first', () => {
    stubApi(EMPTY)
    render(<Overview />)
    expect(screen.getByRole('status').textContent).toContain('Loading')
  })

  it('offers two ways to start when nothing exists', async () => {
    stubApi(EMPTY)
    render(<Overview />)
    expect(
      await screen.findByRole('button', { name: 'Load demo data' }),
    ).toBeTruthy()
    expect(
      screen.getByRole('link', { name: 'Go to Documents' }).getAttribute('href'),
    ).toBe('#/documents')
  })

  it('loads the demo and shows the guide', async () => {
    let loaded = false
    stubApi({
      ...EMPTY,
      'POST /demo/load': () => {
        loaded = true
        return new Response('{}')
      },
      'GET /demo': () => new Response(JSON.stringify(loaded ? DEMO : NO_DEMO)),
      'GET /evaluations?limit=1': () =>
        new Response(JSON.stringify({ runs: loaded ? [RUN] : [] })),
    })
    render(<Overview />)
    await userEvent.click(
      await screen.findByRole('button', { name: 'Load demo data' }),
    )
    expect(
      await screen.findByRole('heading', {
        name: 'Follow one answer from question to diagnosis',
      }),
    ).toBeTruthy()
  })

  it('explains stale demo data', async () => {
    stubApi({
      ...EMPTY,
      'POST /demo/load': () =>
        apiError(409, 'demo_stale', 'the demo data is out of date'),
    })
    render(<Overview />)
    await userEvent.click(
      await screen.findByRole('button', { name: 'Load demo data' }),
    )
    expect((await screen.findByRole('alert')).textContent).toContain('out of date')
  })

  it('labels demo data', async () => {
    stubApi(LOADED)
    render(<Overview />)
    await screen.findByText('Demo data.')
    expect(readout('Recall@k')).toContain('Demo')
    expect(readout('Active release')).toContain('demo-2')
    expect(readout('Active release')).toContain('Demo')
  })

  it('shows no banner for live data', async () => {
    stubApi({
      ...EMPTY,
      'GET /documents': {
        documents: [
          {
            id: 'd1',
            filename: 'a.md',
            size_bytes: 1,
            chunks: 1,
            ingested_at: '',
          },
        ],
      },
    })
    render(<Overview />)
    await screen.findByText('Health')
    expect(screen.queryByText('Demo data.')).toBeNull()
  })

  it('states the gate in words', async () => {
    stubApi(LOADED)
    render(<Overview />)
    await screen.findByText('PASS')
    expect(readout('Recall@k')).toContain('0.98')
    expect(readout('Quality gate')).toContain('PASS')
  })

  it('links each guide step to its real page', async () => {
    stubApi(LOADED)
    render(<Overview />)
    const guide = await screen.findByRole('region', {
      name: 'Follow one answer from question to diagnosis',
    })
    const hrefs = within(guide)
      .getAllByRole('link')
      .map((link) => link.getAttribute('href'))
    expect(hrefs).toEqual([
      '#/chat',
      '#/evaluations/run-1',
      '#/telemetry',
      '#/incidents/incident-1',
    ])
  })

  it('remembers guide progress and hiding', async () => {
    stubApi(LOADED)
    const first = render(<Overview />)
    const ask = await screen.findByRole('link', { name: /Ask/ })
    expect(ask.getAttribute('aria-current')).toBe('step')
    await userEvent.click(ask)
    first.unmount()

    render(<Overview />)
    const evaluate = await screen.findByRole('link', { name: /Evaluate/ })
    expect(evaluate.getAttribute('aria-current')).toBe('step')
    await userEvent.click(screen.getByRole('button', { name: 'Hide guide' }))
    expect(screen.queryByRole('link', { name: /Evaluate/ })).toBeNull()
  })

  it('works when storage is blocked', async () => {
    const original = window.localStorage
    Object.defineProperty(window, 'localStorage', {
      configurable: true,
      get() {
        throw new Error('blocked')
      },
    })
    stubApi(LOADED)
    render(<Overview />)
    expect(await screen.findByRole('link', { name: /Ask/ })).toBeTruthy()
    Object.defineProperty(window, 'localStorage', {
      configurable: true,
      value: original,
    })
  })

  it('says no traffic rather than zero', async () => {
    stubApi({
      ...LOADED,
      'GET /telemetry': { ...TELEMETRY, error_ratio: null, chat_p95_s: null },
    })
    render(<Overview />)
    await screen.findByText('Error rate')
    expect(readout('Error rate')).toContain('No traffic')
    expect(readout('Chat p95')).toContain('No traffic')
  })

  it('says not checked when Prometheus is unreadable', async () => {
    stubApi({
      ...LOADED,
      'GET /telemetry': {
        ...TELEMETRY,
        not_checked: ['alerts', 'metrics'],
        error_ratio: null,
      },
    })
    render(<Overview />)
    await screen.findByText('Error rate')
    expect(readout('Error rate')).toContain('Not checked')
  })

  it('keeps working when telemetry fails', async () => {
    stubApi({
      ...LOADED,
      'GET /telemetry': () => apiError(503, 'database_unavailable', 'down'),
    })
    render(<Overview />)
    await screen.findByText('Health')
    expect(readout('Health')).toContain('Not checked')
    expect(readout('Open incidents')).toContain('1')
  })

  it('does not call health good when Prometheus is unreadable', async () => {
    stubApi({
      ...LOADED,
      'GET /telemetry': {
        ...TELEMETRY,
        not_checked: ['alerts', 'metrics'],
        firing_alerts: null,
      },
    })
    render(<Overview />)
    await screen.findByText('Health')
    expect(readout('Health')).toContain('Not checked')
  })

  it('claims no more than the checks show', async () => {
    stubApi(LOADED)
    render(<Overview />)
    await screen.findByText('Health')
    expect(readout('Health')).toContain('No alerts')
    expect(readout('Health')).not.toContain('Ready')
  })

  it('shows firing alerts in the health readout', async () => {
    stubApi({
      ...LOADED,
      'GET /telemetry': { ...TELEMETRY, firing_alerts: ['AegisHighErrorRate'] },
    })
    render(<Overview />)
    await screen.findByText('Health')
    expect(readout('Health')).toContain('Alerts firing')
  })

  it('shows the failure and a way to retry', async () => {
    stubApi({
      ...EMPTY,
      'GET /demo': () =>
        apiError(503, 'database_unavailable', 'database is unavailable', {
          'X-Request-ID': 'req-3',
        }),
    })
    render(<Overview />)
    const alert = await screen.findByRole('alert')
    expect(alert.textContent).toContain('database is unavailable')
    expect(alert.textContent).toContain('req-3')
    expect(screen.getByRole('button', { name: 'Try again' })).toBeTruthy()
  })

  it('lists open incidents and marks simulated ones', async () => {
    stubApi(LOADED)
    render(<Overview />)
    const link = await screen.findByRole('link', { name: 'model timeout' })
    expect(link.getAttribute('href')).toBe('#/incidents/incident-1')
    expect(screen.getByText('Simulated')).toBeTruthy()
  })
})
