import { useCallback, useState } from 'react'
import type { FormEvent } from 'react'
import { api, setCsrfToken } from '../api'
import type { ApiError } from '../api'
import type { SessionState } from '../types'
import { useAction } from '../useApi'

function message(error: ApiError): string {
  if (error.code === 'invalid_credentials') return 'That password is wrong.'
  if (error.retryAfter) {
    const minutes = Math.ceil(error.retryAfter / 60)
    return `Too many failed attempts. Try again in ${minutes} min.`
  }
  return error.message
}

export default function Login({ onSignedIn }: { onSignedIn: () => void }) {
  const [password, setPassword] = useState('')
  const signIn = useAction(
    useCallback(
      (value: string) =>
        api<SessionState>('/auth/login', {
          method: 'POST',
          json: { password: value },
        }),
      [],
    ),
  )

  const submit = async (event: FormEvent) => {
    event.preventDefault()
    const session = await signIn.run(password)
    if (session) {
      setCsrfToken(session.csrf_token)
      onSignedIn()
    }
  }

  return (
    <main className="login">
      <h1>AegisAI</h1>
      <p>Sign in to see this environment.</p>
      <form onSubmit={submit}>
        <label htmlFor="password">Password</label>
        <input
          id="password"
          type="password"
          autoComplete="current-password"
          required
          value={password}
          onChange={(e) => setPassword(e.target.value)}
        />
        {signIn.error && (
          <p className="field-error" role="alert">
            {message(signIn.error)}
          </p>
        )}
        <button type="submit" disabled={signIn.loading}>
          {signIn.loading ? 'Signing in…' : 'Sign in'}
        </button>
      </form>
    </main>
  )
}
