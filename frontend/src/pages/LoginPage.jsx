import { useState } from 'react'
import { Navigate, useLocation, useNavigate } from 'react-router'
import { useAuth } from '../auth/useAuth'

function safeReturnPath(value) {
  // Only same-app paths; never an absolute or protocol-relative URL.
  return typeof value === 'string' && value.startsWith('/') && !value.startsWith('//') && value !== '/login'
    ? value : '/capture'
}

export default function LoginPage() {
  const { isSignedIn, login, notice } = useAuth()
  const navigate = useNavigate()
  const location = useLocation()
  const [code, setCode] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const from = safeReturnPath(location.state?.from)

  if (isSignedIn) return <Navigate to={from} replace />

  async function submit(event) {
    event.preventDefault()
    if (busy || !code.trim()) return
    setBusy(true)
    setError('')
    try {
      await login(code)
      navigate(from, { replace: true })
    } catch (err) {
      setError(err.status === 401 ? 'That access code is not correct.' : err.message)
      setBusy(false)
    }
  }

  return <section className="login-card" aria-labelledby="login-heading">
    <h1 id="login-heading">Bodega</h1>
    <p>Enter your owner access code to open your sales.</p>
    {notice && !error && <p role="status" className="status-note">{notice}</p>}
    <form onSubmit={submit}>
      <label>Access code
        <input type="password" autoComplete="current-password" required value={code}
          onChange={event => setCode(event.target.value)} disabled={busy} />
      </label>
      <button disabled={busy || !code.trim()}>{busy ? 'Checking…' : 'Sign in'}</button>
      {error && <p role="alert">{error}</p>}
    </form>
  </section>
}
