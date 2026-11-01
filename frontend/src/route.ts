import { useSyncExternalStore } from 'react'

export interface Route {
  path: string
  query: URLSearchParams
}

export function parseRoute(hash: string): Route {
  const [path = '', query = ''] = hash.replace(/^#/, '').split('?')
  return { path: path || '/', query: new URLSearchParams(query) }
}

const UUID =
  /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i

// Ids from the URL go into API paths, so a crafted link must not change the path.
export function isId(value: string | null | undefined): value is string {
  return typeof value === 'string' && UUID.test(value)
}

function subscribe(onChange: () => void): () => void {
  window.addEventListener('hashchange', onChange)
  return () => window.removeEventListener('hashchange', onChange)
}

export function useRoute(): Route {
  const hash = useSyncExternalStore(subscribe, () => window.location.hash)
  return parseRoute(hash)
}
