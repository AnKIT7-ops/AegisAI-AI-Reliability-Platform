import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { apiError, stubApi, TELEMETRY } from '../test/stub'
import Overview from './Overview'

const EMPTY = {
  'GET /evaluations?limit=1': { runs: [] },
  'GET /documents': { documents: [] },
  'GET /telemetry': TELEMETRY,
  'GET /incidents?status=open&limit=5': { incidents: [] },
  'GET /releases?status=active': { releases: [] },
}
const HAS_DOCS = {
  ...EMPTY,
  'GET /documents': {
    documents: [
      { id: 'd1', filename: 'a.md', size_bytes: 1, chunks: 1, ingested_at: '' },
    ],
  },
}
const RUN = {
  id: 'run-1',
  label: 'baseline',
  status: 'done',
  source: 'live',
  metrics: { recall_at_k: 0.9804 },
  gate: { passed: true, failures: [] },
}
const LOADED = {
  ...EMPTY,
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
    releases: [{ id: 'r2', version: '1.2', source: 'live', status: 'active' }],
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

  it('points an empty console at its documents', async () => {
    stubApi(EMPTY)
    render(<Overview />)
    const link = await screen.findByRole('link', { name: 'Add documents' })
    expect(link.getAttribute('href')).toBe('#/documents')
  })

  it('states the gate in words', async () => {
    stubApi(LOADED)
    render(<Overview />)
    await screen.findByText('PASS')
    expect(readout('Recall@k')).toContain('0.98')
    expect(readout('Quality gate')).toContain('PASS')
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
      ...HAS_DOCS,
      'GET /evaluations?limit=1': () =>
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
