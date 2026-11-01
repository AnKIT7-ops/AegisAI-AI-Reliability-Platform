import { useState } from 'react'

const KEY = 'aegis.guide'

interface Progress {
  visited: string[]
  hidden: boolean
}

export interface Step {
  name: string
  detail: string
  href: string
}

// Storage can be missing or blocked. The guide then works for this visit only.
function read(): Progress {
  try {
    const saved = JSON.parse(localStorage.getItem(KEY) ?? 'null') as Progress | null
    if (saved && Array.isArray(saved.visited)) return saved
  } catch {
    // Fall through to a fresh start.
  }
  return { visited: [], hidden: false }
}

function save(progress: Progress): void {
  try {
    localStorage.setItem(KEY, JSON.stringify(progress))
  } catch {
    // Nothing to do: progress lasts until the page is reloaded.
  }
}

export function GuidedPath({ steps }: { steps: Step[] }) {
  const [progress, setProgress] = useState(read)
  const update = (next: Progress) => {
    save(next)
    setProgress(next)
  }

  if (progress.hidden) return null
  const next = steps.find((step) => !progress.visited.includes(step.name))

  return (
    <section className="guide" aria-labelledby="guide-title">
      <div className="guide-head">
        <h2 id="guide-title">Follow one answer from question to diagnosis</h2>
        <button
          type="button"
          className="quiet"
          onClick={() => update({ ...progress, hidden: true })}
        >
          Hide guide
        </button>
      </div>
      <ol>
        {steps.map((step, index) => {
          const visited = progress.visited.includes(step.name)
          return (
            <li key={step.name} className={visited ? 'visited' : undefined}>
              <a
                href={step.href}
                aria-current={step === next ? 'step' : undefined}
                onClick={() =>
                  !visited &&
                  update({ ...progress, visited: [...progress.visited, step.name] })
                }
              >
                <span className="guide-mark" aria-hidden="true">
                  {visited ? '✓' : index + 1}
                </span>
                <span className="guide-name">{step.name}</span>
                <span className="guide-detail">{step.detail}</span>
                {visited && <span className="sr-only"> (visited)</span>}
              </a>
            </li>
          )
        })}
      </ol>
    </section>
  )
}
