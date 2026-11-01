import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it } from 'vitest'
import { apiError, DEMO, json, NO_DEMO, stubApi } from '../test/stub'
import Chat from './Chat'

const ANSWER = {
  answer: 'Retention is 15 days [1].',
  sources: [
    {
      n: 1,
      filename: 'prometheus-storage.md',
      score: 0.82,
      snippet: 'defaults to 15d',
    },
  ],
  model: 'qwen3.5:4b',
  latency_ms: 1200,
}

async function ask(question: string): Promise<void> {
  await userEvent.type(screen.getByLabelText(/Ask about/), question)
  await userEvent.click(screen.getByRole('button', { name: 'Ask' }))
}

describe('Chat', () => {
  it('shows an answer with sources and latency', async () => {
    stubApi({ 'GET /demo': NO_DEMO, 'POST /chat': ANSWER })
    render(<Chat />)
    await ask('retention?')
    expect(await screen.findByText('prometheus-storage.md')).toBeTruthy()
    expect(screen.getByText('[1]').className).toBe('citation')
    expect(screen.getByText(/1200 ms/)).toBeTruthy()
    expect(screen.getByText('defaults to 15d')).toBeTruthy()
  })

  it('sends the question', async () => {
    const calls = stubApi({ 'GET /demo': NO_DEMO, 'POST /chat': ANSWER })
    render(<Chat />)
    await ask('retention?')
    await screen.findByText('prometheus-storage.md')
    const call = calls.find((c) => c.key === 'POST /chat')
    expect(call?.init.body).toBe('{"question":"retention?"}')
  })

  it('renders model output as plain text', async () => {
    const hostile = '<img src=x onerror="alert(1)"> **bold** [1]'
    stubApi({
      'GET /demo': NO_DEMO,
      'POST /chat': { ...ANSWER, answer: hostile },
    })
    const { container } = render(<Chat />)
    await ask('q')
    await screen.findByText('prometheus-storage.md')
    expect(container.querySelector('img')).toBeNull()
    expect(container.textContent).toContain('<img src=x onerror="alert(1)">')
  })

  it('renders document snippets as plain text', async () => {
    stubApi({
      'GET /demo': NO_DEMO,
      'POST /chat': {
        ...ANSWER,
        sources: [{ ...ANSWER.sources[0], snippet: '<script>x</script>' }],
      },
    })
    const { container } = render(<Chat />)
    await ask('q')
    await screen.findByText('<script>x</script>')
    expect(container.querySelector('script')).toBeNull()
  })

  it('says when no model was called', async () => {
    stubApi({
      'GET /demo': NO_DEMO,
      'POST /chat': { ...ANSWER, sources: [], model: null },
    })
    render(<Chat />)
    await ask('q')
    expect(await screen.findByText(/no model called/)).toBeTruthy()
  })

  it('explains a missing model and offers recorded answers', async () => {
    stubApi({
      'GET /demo': DEMO,
      'POST /chat': () =>
        apiError(503, 'model_unavailable', 'Ollama is not reachable', {
          'X-Request-ID': 'req-5',
        }),
    })
    render(<Chat />)
    await screen.findByText('Recorded answers')
    await ask('q')
    const alert = await screen.findByRole('alert')
    expect(alert.textContent).toContain('Ollama is not reachable')
    expect(alert.textContent).toContain('req-5')
    expect(screen.getByText(/Recorded answers below need no model/)).toBeTruthy()
  })

  it('shows other failures without the Ollama help', async () => {
    stubApi({
      'GET /demo': NO_DEMO,
      'POST /chat': () => json({ error: { code: 'x', message: 'bad' } }, 500),
    })
    render(<Chat />)
    await ask('q')
    await screen.findByRole('alert')
    expect(screen.queryByText(/Live answers need Ollama/)).toBeNull()
  })

  it('labels a recorded answer', async () => {
    stubApi({ 'GET /demo': DEMO })
    render(<Chat />)
    await userEvent.click(
      await screen.findByRole('button', {
        name: 'What is the default retention?',
      }),
    )
    const heading = screen.getByRole('heading', { name: /Recorded answer Demo/ })
    expect(heading).toBeTruthy()
    expect(screen.getByText('prometheus-storage.md')).toBeTruthy()
    expect(screen.getByText(/1120 ms when it was recorded/)).toBeTruthy()
  })

  it('shows the demo banner only with demo data', async () => {
    stubApi({ 'GET /demo': NO_DEMO })
    render(<Chat />)
    await screen.findByRole('heading', { name: 'Chat' })
    expect(screen.queryByText('Demo data.')).toBeNull()
  })

  it('keeps working when the demo status fails', async () => {
    stubApi({
      'GET /demo': () => apiError(503, 'database_unavailable', 'down'),
      'POST /chat': ANSWER,
    })
    render(<Chat />)
    await ask('q')
    expect(await screen.findByText('prometheus-storage.md')).toBeTruthy()
  })
})
