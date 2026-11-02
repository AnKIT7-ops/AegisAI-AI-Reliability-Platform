import { useCallback, useState } from 'react'
import type { FormEvent } from 'react'
import { api } from '../api'
import { ErrorState } from '../components/States'
import type { Answer } from '../types'
import { useAction } from '../useApi'

/** Plain text only: answers are model output, so nothing renders as HTML. */
function Cited({ text }: { text: string }) {
  return (
    <p className="answer">
      {text.split(/(\[\d+\])/).map((part, index) =>
        /^\[\d+\]$/.test(part) ? (
          <span className="citation" key={index}>
            {part}
          </span>
        ) : (
          part
        ),
      )}
    </p>
  )
}

function LiveAnswer({ answer }: { answer: Answer }) {
  return (
    <section className="result" aria-labelledby="answer-title">
      <h2 id="answer-title">Answer</h2>
      <Cited text={answer.answer} />
      <p className="meta">
        {answer.latency_ms} ms
        {answer.model ? ` · ${answer.model}` : ' · no model called'}
      </p>
      {answer.sources.length > 0 && (
        <>
          <h3>Sources</h3>
          <ol className="sources">
            {answer.sources.map((source) => (
              <li key={source.n} value={source.n}>
                <strong>{source.filename}</strong>{' '}
                <span className="meta">score {source.score.toFixed(2)}</span>
                <p>{source.snippet}</p>
              </li>
            ))}
          </ol>
        </>
      )}
    </section>
  )
}

export default function Chat() {
  const [question, setQuestion] = useState('')
  const ask = useAction(
    useCallback(
      (value: string) =>
        api<Answer>('/chat', { method: 'POST', json: { question: value } }),
      [],
    ),
  )

  const submit = (event: FormEvent) => {
    event.preventDefault()
    void ask.run(question)
  }

  const modelDown = ask.error?.code === 'model_unavailable'

  return (
    <>
      <h1>Chat</h1>
      <form className="ask" onSubmit={submit}>
        <label htmlFor="question">Ask about the ingested documents</label>
        <textarea
          id="question"
          rows={3}
          maxLength={2000}
          required
          value={question}
          onChange={(e) => setQuestion(e.target.value)}
        />
        <button type="submit" disabled={ask.loading}>
          {ask.loading ? 'Asking…' : 'Ask'}
        </button>
      </form>

      {ask.loading && <p role="status">Waiting for the model…</p>}
      {ask.error && <ErrorState error={ask.error} />}
      {modelDown && (
        <p className="hint">
          Live answers need Ollama. Start it, or pull the models with{' '}
          <code>ollama pull qwen3.5:4b</code> and{' '}
          <code>ollama pull nomic-embed-text</code>.
        </p>
      )}
      {ask.data && <LiveAnswer answer={ask.data} />}
    </>
  )
}
