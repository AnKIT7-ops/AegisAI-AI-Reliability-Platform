import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it } from 'vitest'
import { apiError, DEMO, NO_DEMO, stubApi, TELEMETRY } from '../test/stub'
import type { Incident } from '../types'
import IncidentDetail from './IncidentDetail'
import Incidents from './Incidents'
import Telemetry from './Telemetry'

const INCIDENT: Incident = {
  id: 'i1',
  type: 'model_timeout',
  severity: 'warning',
  status: 'open',
  source: 'simulated',
  last_seen: '2026-11-01T00:00:00Z',
  report: {
    symptom: 'Model calls are timing out.',
    facts: [
      {
        statement: 'AegisModelFailures is firing.',
        source: 'alert',
        reference: 'http://localhost:8080/prometheus/alerts',
      },
      {
        statement: '3 requests failed: req-1, req-2.',
        source: 'log',
        reference: 'event=model_error',
      },
    ],
    hypotheses: [
      {
        cause: 'The model is slower than the timeout.',
        confidence: 'high',
        basis: 'Timeouts match the failed requests.',
      },
    ],
    uncertainty: 'The cause of the slowdown is unknown.',
    runbook: 'docs/runbooks/model-timeout.md',
    suggested_actions: ['Check Ollama.', 'Raise the timeout.'],
  },
}

describe('Telemetry', () => {
  it('shows current values', async () => {
    stubApi({ 'GET /telemetry': TELEMETRY })
    render(<Telemetry />)
    expect(await screen.findByText('1.8%')).toBeTruthy()
    expect(screen.getByText('2.4 s')).toBeTruthy()
    expect(screen.getByText('1.50/s')).toBeTruthy()
    expect(screen.getByText('Reachable')).toBeTruthy()
    expect(screen.getByText('No alerts are firing.')).toBeTruthy()
  })

  it('names firing alerts and failed model calls', async () => {
    stubApi({
      'GET /telemetry': {
        ...TELEMETRY,
        firing_alerts: ['AegisHighErrorRate'],
        model_failures: { timeout: 3.2 },
      },
    })
    render(<Telemetry />)
    expect(await screen.findByText('AegisHighErrorRate')).toBeTruthy()
    expect(screen.getByText('Timeout: 3')).toBeTruthy()
  })

  it('says no traffic rather than zero', async () => {
    stubApi({
      'GET /telemetry': { ...TELEMETRY, error_ratio: null, chat_p95_s: null },
    })
    render(<Telemetry />)
    expect((await screen.findAllByText('No traffic')).length).toBe(2)
  })

  it('explains an unreadable Prometheus', async () => {
    stubApi({
      'GET /telemetry': {
        ...TELEMETRY,
        not_checked: ['alerts', 'metrics'],
        firing_alerts: null,
        model_failures: null,
      },
    })
    render(<Telemetry />)
    expect(await screen.findByText(/Prometheus could not be read/)).toBeTruthy()
    expect(screen.getByText('Not checked.')).toBeTruthy()
    expect(screen.queryByText('No alerts are firing.')).toBeNull()
  })

  it('shows an unreachable database', async () => {
    stubApi({ 'GET /telemetry': { ...TELEMETRY, database: 'unavailable' } })
    render(<Telemetry />)
    expect(await screen.findByText('Unavailable')).toBeTruthy()
  })

  it('links to the graphs', async () => {
    stubApi({ 'GET /telemetry': TELEMETRY })
    render(<Telemetry />)
    const link = await screen.findByRole('link', { name: 'Open Grafana' })
    expect(link.getAttribute('href')).toBe(TELEMETRY.grafana_url)
  })

  it('shows a failure with a retry', async () => {
    stubApi({ 'GET /telemetry': () => apiError(500, 'internal_error', 'broken') })
    render(<Telemetry />)
    expect((await screen.findByRole('alert')).textContent).toContain('broken')
    expect(screen.getByRole('button', { name: 'Try again' })).toBeTruthy()
  })
})

describe('Incidents', () => {
  const LIST = 'GET /incidents?limit=50'

  it('invites a first check', async () => {
    stubApi({ [LIST]: { incidents: [] }, 'GET /demo': NO_DEMO })
    render(<Incidents />)
    expect(await screen.findByText('No incidents recorded')).toBeTruthy()
  })

  it('lists incidents and marks simulated ones', async () => {
    stubApi({ [LIST]: { incidents: [INCIDENT] }, 'GET /demo': DEMO })
    render(<Incidents />)
    const link = await screen.findByRole('link', { name: 'Model timeout' })
    expect(link.getAttribute('href')).toBe('#/incidents/i1')
    expect(screen.getByText('Simulated')).toBeTruthy()
    expect(await screen.findByText('Demo data.')).toBeTruthy()
  })

  it('reports a detection pass, including what it could not read', async () => {
    stubApi({
      [LIST]: { incidents: [] },
      'GET /demo': NO_DEMO,
      'POST /incidents/detect': {
        incidents: [],
        not_checked: ['alerts', 'metrics'],
      },
    })
    render(<Incidents />)
    await screen.findByText('No incidents recorded')
    await userEvent.click(screen.getByRole('button', { name: 'Check for incidents' }))
    expect(
      (await screen.findByText(/Found 0/)).textContent,
    ).toContain('Not checked: alerts, metrics.')
  })

  it('simulates the chosen scenario', async () => {
    const calls = stubApi({
      [LIST]: { incidents: [] },
      'GET /demo': NO_DEMO,
      'POST /incidents/simulate': { incidents: [] },
    })
    render(<Incidents />)
    await screen.findByText('No incidents recorded')
    await userEvent.selectOptions(
      screen.getByLabelText('Scenario'),
      'increased_latency',
    )
    await userEvent.click(screen.getByRole('button', { name: 'Simulate' }))
    await screen.findByText('No incidents recorded')
    const post = calls.find((c) => c.key === 'POST /incidents/simulate')
    expect(post?.init.body).toBe('{"scenario":"increased_latency"}')
  })

  it('explains a failed check', async () => {
    stubApi({
      [LIST]: { incidents: [] },
      'GET /demo': NO_DEMO,
      'POST /incidents/detect': () => apiError(500, 'internal_error', 'broken'),
    })
    render(<Incidents />)
    await screen.findByText('No incidents recorded')
    await userEvent.click(screen.getByRole('button', { name: 'Check for incidents' }))
    expect((await screen.findByRole('alert')).textContent).toContain('broken')
  })
})

describe('IncidentDetail', () => {
  const PATH = 'GET /incidents/i1'

  it('keeps facts and hypotheses in separate sections', async () => {
    stubApi({ [PATH]: INCIDENT })
    render(<IncidentDetail id="i1" />)
    const facts = await screen.findByRole('region', { name: 'Observed facts' })
    const hypotheses = screen.getByRole('region', { name: 'Hypotheses' })
    expect(within(facts).getByText(/AegisModelFailures is firing/)).toBeTruthy()
    expect(within(facts).queryByText(/slower than the timeout/)).toBeNull()
    expect(within(hypotheses).getByText(/slower than the timeout/)).toBeTruthy()
    expect(within(hypotheses).queryByText(/AegisModelFailures/)).toBeNull()
  })

  it('labels hypotheses as unconfirmed, with their confidence', async () => {
    stubApi({ [PATH]: INCIDENT })
    render(<IncidentDetail id="i1" />)
    expect(await screen.findByText('high confidence')).toBeTruthy()
    expect(screen.getByText('Inferred from the facts, not confirmed.')).toBeTruthy()
  })

  it('states that diagnosis is rule-based and nothing was run', async () => {
    stubApi({ [PATH]: INCIDENT })
    render(<IncidentDetail id="i1" />)
    const notice = await screen.findByText(/Diagnosis is rule-based/)
    expect(notice.textContent).toContain('nothing here has been run')
  })

  it('shows uncertainty, actions and the runbook', async () => {
    stubApi({ [PATH]: INCIDENT })
    render(<IncidentDetail id="i1" />)
    expect(await screen.findByText(/cause of the slowdown/)).toBeTruthy()
    expect(screen.getByText('Check Ollama.')).toBeTruthy()
    expect(screen.getByText('docs/runbooks/model-timeout.md')).toBeTruthy()
  })

  it('links web references and shows others as text', async () => {
    stubApi({ [PATH]: INCIDENT })
    render(<IncidentDetail id="i1" />)
    const link = await screen.findByRole('link', { name: 'Open the evidence' })
    expect(link.getAttribute('href')).toBe(
      'http://localhost:8080/prometheus/alerts',
    )
    expect(screen.getByText('event=model_error').tagName).toBe('CODE')
  })

  it('never turns another scheme into a link', async () => {
    const report = {
      ...INCIDENT.report,
      facts: [{ statement: 'x', source: 'log', reference: 'javascript:alert(1)' }],
    }
    stubApi({ [PATH]: { ...INCIDENT, report } })
    const { container } = render(<IncidentDetail id="i1" />)
    await screen.findByText('javascript:alert(1)')
    expect(container.querySelector('a[href^="javascript"]')).toBeNull()
  })

  it('resolves an open incident', async () => {
    let resolved = false
    stubApi({
      [PATH]: () =>
        new Response(
          JSON.stringify({ ...INCIDENT, status: resolved ? 'resolved' : 'open' }),
        ),
      'POST /incidents/i1/resolve': () => {
        resolved = true
        return new Response('{}')
      },
    })
    render(<IncidentDetail id="i1" />)
    await userEvent.click(
      await screen.findByRole('button', { name: 'Resolve incident' }),
    )
    expect(await screen.findByText(/Resolved · last seen/)).toBeTruthy()
    expect(screen.queryByRole('button', { name: 'Resolve incident' })).toBeNull()
  })

  it('shows a missing incident', async () => {
    stubApi({ [PATH]: () => apiError(404, 'not_found', 'incident not found') })
    render(<IncidentDetail id="i1" />)
    expect((await screen.findByRole('alert')).textContent).toContain('not found')
  })
})
