import { useCallback, useState } from 'react'
import type { FormEvent } from 'react'
import { api } from '../api'
import { DemoBanner, ErrorState, Tag } from '../components/States'
import type { Answer, DemoStatus, RecordedAnswer } from '../types'
import { useAction, useApi } from '../useApi'

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

function Recorded({ answer }: { answer: RecordedAnswer }) {
  return (
    <section className="result" aria-labelledby="recorded-title">
      <h2 id="recorded-title">
        Recorded answer <Tag>Demo</Tag>
      </h2>
      <p className="question">{answer.question}</p>
      <Cited text={answer.answer ?? 'This run recorded no answer.'} />
      {answer.latency_ms !== null && (
        <p className="meta">{answer.latency_ms} ms when it was recorded</p>
      )}
      {answer.sources.length > 0 && (
        <>
          <h3>Sources</h3>
          <ol className="sources">
            {answer.sources.map((source, index) => (
              <li key={index}>
                <strong>{source}</strong>
              </li>
            ))}
          </ol>
        </>
      )}
    </section>
  )
}

export default function Chat() {
  const demo = useApi<DemoStatus>('/demo')
  const [question, setQuestion] = useState('')
  const [recorded, setRecorded] = useState<RecordedAnswer | null>(null)
  const ask = useAction(
    useCallback(
      (value: string) =>
        api<Answer>('/chat', { method: 'POST', json: { question: value } }),
      [],
    ),
  )

  const submit = (event: FormEvent) => {
    event.preventDefault()
    setRecorded(null)
    void ask.run(question)
  }

  const answers = demo.data?.recorded_answers ?? []
  const modelDown = ask.error?.code === 'model_unavailable'

  return (
    <>
      <h1>Chat</h1>
      {demo.data?.loaded && <DemoBanner />}
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
          {answers.length > 0 && ' Recorded answers below need no model.'}
        </p>
      )}
      {ask.data && !recorded && <LiveAnswer answer={ask.data} />}

      {answers.length > 0 && (
        <section aria-labelledby="recorded-list">
          <h2 id="recorded-list">Recorded answers</h2>
          <p className="meta">
            Real answers from an evaluation run. No model is called.
          </p>
          <ul className="choices">
            {answers.map((answer) => (
              <li key={answer.example_id}>
                <button
                  type="button"
                  className="choice"
                  aria-pressed={recorded?.example_id === answer.example_id}
                  onClick={() => setRecorded(answer)}
                >
                  {answer.question}
                </button>
              </li>
            ))}
          </ul>
        </section>
      )}
      {recorded && <Recorded answer={recorded} />}
    </>
  )
}
