import { fireEvent, render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it } from 'vitest'
import { apiError, DEMO, NO_DEMO, stubApi } from '../test/stub'
import Documents from './Documents'

const DOCUMENT = {
  id: 'd1',
  filename: 'runbook.md',
  size_bytes: 2048,
  chunks: 3,
  ingested_at: '2026-11-01T00:00:00Z',
}
const FILE = new File(['# Hi'], 'runbook.md', { type: 'text/markdown' })

async function upload(): Promise<void> {
  await userEvent.upload(screen.getByLabelText(/Add a Markdown/), FILE)
  // jsdom's required check cannot see an uploaded file, so submit directly.
  const form = screen.getByRole('button', { name: 'Add document' }).closest('form')
  fireEvent.submit(form as HTMLFormElement)
}

describe('Documents', () => {
  it('invites the first upload', async () => {
    stubApi({ 'GET /documents': { documents: [] }, 'GET /demo': NO_DEMO })
    render(<Documents />)
    expect(await screen.findByText('No documents added yet')).toBeTruthy()
  })

  it('lists documents', async () => {
    stubApi({ 'GET /documents': { documents: [DOCUMENT] }, 'GET /demo': NO_DEMO })
    render(<Documents />)
    expect(await screen.findByText('runbook.md')).toBeTruthy()
    expect(screen.getByText('2.0 KB')).toBeTruthy()
  })

  it('uploads and refreshes the list', async () => {
    let uploaded = false
    stubApi({
      'GET /documents': () =>
        new Response(JSON.stringify({ documents: uploaded ? [DOCUMENT] : [] })),
      'GET /demo': NO_DEMO,
      'POST /documents/ingest': () => {
        uploaded = true
        return new Response(JSON.stringify({ document: DOCUMENT, created: true }))
      },
    })
    render(<Documents />)
    await screen.findByText('No documents added yet')
    await upload()
    expect(await screen.findByText('Added runbook.md.')).toBeTruthy()
    expect(await screen.findByRole('cell', { name: 'runbook.md' })).toBeTruthy()
  })

  it('says when a document was already added', async () => {
    stubApi({
      'GET /documents': { documents: [DOCUMENT] },
      'GET /demo': NO_DEMO,
      'POST /documents/ingest': { document: DOCUMENT, created: false },
    })
    render(<Documents />)
    await screen.findByText('runbook.md')
    await upload()
    expect(await screen.findByText('runbook.md was already added.')).toBeTruthy()
  })

  it('explains a rejected upload', async () => {
    stubApi({
      'GET /documents': { documents: [] },
      'GET /demo': NO_DEMO,
      'POST /documents/ingest': () =>
        apiError(503, 'model_unavailable', 'Ollama is not reachable'),
    })
    render(<Documents />)
    await screen.findByText('No documents added yet')
    await upload()
    expect((await screen.findByRole('alert')).textContent).toContain(
      'Ollama is not reachable',
    )
  })

  it('keeps demo documents apart and labelled', async () => {
    stubApi({ 'GET /documents': { documents: [] }, 'GET /demo': DEMO })
    render(<Documents />)
    expect(await screen.findByText('k8s-probes.md')).toBeTruthy()
    expect(screen.getByText('Demo data.')).toBeTruthy()
    expect(screen.getByRole('heading', { name: /Demo documents/ })).toBeTruthy()
    expect(screen.getByText(/not in your document index/)).toBeTruthy()
  })

  it('hides demo documents until the demo is loaded', async () => {
    stubApi({ 'GET /documents': { documents: [] }, 'GET /demo': NO_DEMO })
    render(<Documents />)
    await screen.findByText('No documents added yet')
    expect(screen.queryByText('k8s-probes.md')).toBeNull()
  })

  it('shows a loading failure with a retry', async () => {
    stubApi({
      'GET /documents': () => apiError(500, 'index_mismatch', 'index mismatch'),
      'GET /demo': NO_DEMO,
    })
    render(<Documents />)
    expect((await screen.findByRole('alert')).textContent).toContain(
      'index mismatch',
    )
    expect(screen.getByRole('button', { name: 'Try again' })).toBeTruthy()
  })
})
