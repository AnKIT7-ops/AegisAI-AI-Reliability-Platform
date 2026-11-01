import { useCallback, useEffect, useState } from 'react'
import { api, ApiError, toApiError } from './api'

interface Result<T> {
  path: string
  attempt: number
  data: T | null
  error: ApiError | null
}

/** Load a path. A null path waits, for reads that depend on an earlier one. */
export function useApi<T>(path: string | null) {
  const [result, setResult] = useState<Result<T> | null>(null)
  const [attempt, setAttempt] = useState(0)

  useEffect(() => {
    if (path === null) return
    const controller = new AbortController()
    api<T>(path, { signal: controller.signal })
      .then((data) => setResult({ path, attempt, data, error: null }))
      .catch((error: unknown) => {
        if (controller.signal.aborted) return
        setResult({ path, attempt, data: null, error: toApiError(error) })
      })
    return () => controller.abort()
  }, [path, attempt])

  const reload = useCallback(() => setAttempt((n) => n + 1), [])
  // Derived, so a reload keeps showing the last data instead of flashing empty.
  const shown = result?.path === path ? result : null
  return {
    data: shown?.data ?? null,
    error: shown?.error ?? null,
    loading: path !== null && (shown === null || shown.attempt !== attempt),
    reload,
  }
}

interface ActionState<T> {
  data: T | null
  error: ApiError | null
  loading: boolean
}

/** Run something the user asked for, tracking progress and failure. */
export function useAction<Args extends unknown[], T>(
  action: (...args: Args) => Promise<T>,
) {
  const [state, setState] = useState<ActionState<T>>({
    data: null,
    error: null,
    loading: false,
  })

  const run = useCallback(
    async (...args: Args): Promise<T | null> => {
      setState({ data: null, error: null, loading: true })
      try {
        const data = await action(...args)
        setState({ data, error: null, loading: false })
        return data
      } catch (error) {
        setState({ data: null, error: toApiError(error), loading: false })
        return null
      }
    },
    [action],
  )
  return { ...state, run }
}
