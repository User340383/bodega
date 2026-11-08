import { createApiClient } from '../api/client'
import { clearSession, readAccessCode, writeAccessCode } from './session'

export const SESSION_ENDED = 'Your access code was not accepted. Please sign in again.'
// Any authenticated, read-only endpoint proves the code; /health is public and cannot.
const VERIFY_PATH = '/api/v1/sales?limit=1'

/**
 * The signed-in session outside React: one stable API client that always reads the current code,
 * and listeners that are told when the code changes (sign in, sign out, or a 401 from any call).
 */
export function createAuthSession(clientOptions = {}) {
  let code = readAccessCode()
  const listeners = new Set()
  const notify = message => listeners.forEach(listener => listener(code, message))

  function logout(message = '') {
    clearSession()
    code = ''
    notify(message)
  }

  const request = createApiClient({ ...clientOptions, getAccessCode: () => code, onUnauthorized: () => logout(SESSION_ENDED) })
  // A separate client: a wrong code on the sign-in form must not trigger the global sign-out.
  const verify = createApiClient(clientOptions)

  async function login(candidate) {
    const next = candidate.trim()
    await verify(VERIFY_PATH, { accessCode: next })
    writeAccessCode(next)
    code = next
    notify('')
  }

  return {
    request, login, logout,
    currentCode: () => code,
    subscribe(listener) {
      listeners.add(listener)
      return () => listeners.delete(listener)
    },
  }
}
