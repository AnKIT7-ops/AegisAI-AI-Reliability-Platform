import type { ReactNode } from 'react'
import type { ApiError } from '../api'

export function Loading({ label }: { label: string }) {
  return (
    <p className="state" role="status">
      {label}
    </p>
  )
}

export function ErrorState({
  error,
  onRetry,
}: {
  error: ApiError
  onRetry?: () => void
}) {
  return (
    <div className="state state-error" role="alert">
      <p>{error.message}</p>
      {error.requestId && (
        <p className="meta">
          Request ID <code>{error.requestId}</code>
        </p>
      )}
      {onRetry && (
        <button type="button" onClick={onRetry}>
          Try again
        </button>
      )}
    </div>
  )
}

export function Empty({
  title,
  children,
}: {
  title: string
  children?: ReactNode
}) {
  return (
    <div className="state">
      <h2>{title}</h2>
      {children}
    </div>
  )
}

/** Status in words and a glyph, so it never depends on color. */
export function Verdict({ passed }: { passed: boolean }) {
  return (
    <span className={passed ? 'verdict verdict-pass' : 'verdict verdict-fail'}>
      <span aria-hidden="true">{passed ? '✓' : '✕'}</span>{' '}
      {passed ? 'PASS' : 'FAIL'}
    </span>
  )
}

export function Tag({ children }: { children: ReactNode }) {
  return <span className="tag">{children}</span>
}

/** Stays on every page that shows demo data. */
export function DemoBanner() {
  return (
    <p className="demo-banner">
      <strong>Demo data.</strong> Sample documents, recorded answers and a
      simulated incident.
    </p>
  )
}
