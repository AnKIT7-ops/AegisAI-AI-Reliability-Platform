import { useCallback, useEffect } from 'react'
import { api, setCsrfToken, setUnauthorizedHandler } from './api'
import { Layout } from './components/Layout'
import { Empty, ErrorState, Loading } from './components/States'
import Chat from './pages/Chat'
import Documents from './pages/Documents'
import Login from './pages/Login'
import Overview from './pages/Overview'
import { useRoute } from './route'
import type { SessionState } from './types'
import { useApi } from './useApi'

function NotBuilt() {
  return (
    <Empty title="This page is not available yet">
      <p>
        <a href="#/">Back to Overview</a>
      </p>
    </Empty>
  )
}

function Page({ path }: { path: string }) {
  if (path === '/' || path === '/login') return <Overview />
  if (path === '/chat') return <Chat />
  if (path === '/documents') return <Documents />
  return <NotBuilt />
}

export default function App() {
  const { path } = useRoute()
  const session = useApi<SessionState>('/auth/session')
  const { reload } = session

  useEffect(() => {
    setUnauthorizedHandler(reload)
    return () => setUnauthorizedHandler(null)
  }, [reload])

  const token = session.data?.csrf_token ?? null
  useEffect(() => setCsrfToken(token), [token])

  const signOut = useCallback(async () => {
    await api('/auth/logout', { method: 'POST' }).catch(() => undefined)
    setCsrfToken(null)
    reload()
  }, [reload])

  if (session.error) {
    return <ErrorState error={session.error} onRetry={session.reload} />
  }
  if (session.data === null) return <Loading label="Loading AegisAI…" />
  if (session.data.enabled && !session.data.authenticated) {
    return <Login onSignedIn={reload} />
  }
  return (
    <Layout path={path} onSignOut={session.data.enabled ? signOut : undefined}>
      <Page key={path} path={path} />
    </Layout>
  )
}
