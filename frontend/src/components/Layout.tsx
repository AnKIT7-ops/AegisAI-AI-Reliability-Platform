import { useEffect, useRef } from 'react'
import type { ReactNode } from 'react'

const PAGES = [
  ['/', 'Overview'],
  ['/chat', 'Chat'],
  ['/documents', 'Documents'],
  ['/evaluations', 'Evaluations'],
  ['/telemetry', 'Telemetry'],
  ['/incidents', 'Incidents'],
  ['/releases', 'Releases'],
] as const

function isCurrent(page: string, current: string): boolean {
  return page === '/' ? current === '/' : current.startsWith(page)
}

export function Layout({
  path,
  onSignOut,
  children,
}: {
  path: string
  onSignOut?: () => void
  children: ReactNode
}) {
  const main = useRef<HTMLElement>(null)
  const first = useRef(true)

  // A route change replaces the page without a browser navigation, so focus
  // moves here for keyboard and screen reader users. Not on the first load.
  useEffect(() => {
    if (first.current) {
      first.current = false
      return
    }
    main.current?.focus()
  }, [path])

  return (
    <div className="shell">
      <a className="skip-link" href="#main" onClick={(e) => {
        e.preventDefault()
        main.current?.focus()
      }}>
        Skip to content
      </a>
      <header className="rail">
        <a className="brand" href="#/">
          AegisAI
        </a>
        <nav aria-label="Main">
          <ul>
            {PAGES.map(([page, label]) => (
              <li key={page}>
                <a
                  href={`#${page}`}
                  aria-current={isCurrent(page, path) ? 'page' : undefined}
                >
                  {label}
                </a>
              </li>
            ))}
          </ul>
        </nav>
        {onSignOut && (
          <button type="button" className="quiet" onClick={onSignOut}>
            Sign out
          </button>
        )}
      </header>
      <main id="main" ref={main} tabIndex={-1}>
        {children}
      </main>
    </div>
  )
}
