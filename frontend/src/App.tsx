import { useCallback, useEffect } from 'react'
import { api, setCsrfToken, setUnauthorizedHandler } from './api'
import { Layout } from './components/Layout'
import { Empty, ErrorState, Loading } from './components/States'
import Compare from './pages/Compare'
import Chat from './pages/Chat'
import Documents from './pages/Documents'
import EvaluationDetail from './pages/EvaluationDetail'
import Evaluations from './pages/Evaluations'
import IncidentDetail from './pages/IncidentDetail'
import Incidents from './pages/Incidents'
import Login from './pages/Login'
import Overview from './pages/Overview'
import Releases from './pages/Releases'
import Telemetry from './pages/Telemetry'
import { useRoute } from './route'
import type { SessionState } from './types'
import { useApi } from './useApi'

function NotFound() {
  return (
    <Empty title="That page does not exist">
      <p>
        <a href="#/">Back to Overview</a>
      </p>
    </Empty>
  )
}

function Page({ path, query }: { path: string; query: URLSearchParams }) {
  const [, section = '', id] = path.split('/')
  if (section === '' || section === 'login') return <Overview />
  if (section === 'chat') return <Chat />
  if (section === 'documents') return <Documents />
  if (section === 'telemetry') return <Telemetry />
  if (section === 'releases') return <Releases />
  if (section === 'evaluations') {
    if (id === 'compare') return <Compare query={query} />
    return id ? <EvaluationDetail id={id} /> : <Evaluations />
  }
  if (section === 'incidents') {
    return id ? <IncidentDetail id={id} /> : <Incidents />
  }
  return <NotFound />
}

export default function App() {
  const { path, query } = useRoute()
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
      <Page key={path} path={path} query={query} />
    </Layout>
  )
}
