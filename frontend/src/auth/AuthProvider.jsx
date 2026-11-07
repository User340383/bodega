import { useEffect, useMemo, useState } from 'react'
import { AuthContext } from './authContext'
import { createAuthSession } from './authSession'

// Provides { accessCode, isSignedIn, notice, request, login, logout } to the whole app.
export default function AuthProvider({ children, clientOptions }) {
  const [session] = useState(() => createAuthSession(clientOptions))
  const [accessCode, setAccessCode] = useState(session.currentCode)
  const [notice, setNotice] = useState('')

  useEffect(() => session.subscribe((code, message) => {
    setAccessCode(code)
    setNotice(message)
  }), [session])

  const value = useMemo(() => ({
    accessCode, isSignedIn: Boolean(accessCode), notice,
    request: session.request, login: session.login, logout: session.logout,
  }), [accessCode, notice, session])
  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>
}
