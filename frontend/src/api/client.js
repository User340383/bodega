// Global API client (M3 4.2). Every call goes through here: base URL, Bearer access code,
// timeouts, and one plain-English error shape for every backend error format.

export const DEFAULT_TIMEOUT_MS = 30_000

export class ApiError extends Error {
  constructor(message, { status = 0, code = 'REQUEST_FAILED', fields = {} } = {}) {
    super(message)
    this.name = 'ApiError'
    this.status = status
    this.code = code
    this.fields = fields
  }
}

// Production writes window.__BODEGA_CONFIG__ in /config.js when the container starts (M3 8.3 API_BASE_URL).
// Locally it is empty and Vite proxies /api to the backend.
export function apiBaseUrl(config = globalThis.window?.__BODEGA_CONFIG__) {
  return String(config?.apiBaseUrl || '').trim().replace(/\/+$/, '')
}

const STATUS_MESSAGES = {
  401: 'Your access code was not accepted. Please sign in again.',
  404: 'We could not find that. It may have been deleted. Go back and try again.',
  409: 'This cannot be changed right now. Refresh and try again.',
  429: 'Too many wrong access codes. Wait 15 minutes and try again.',
  500: 'Something went wrong on our side. Please try again.',
  502: 'The server is starting up or unavailable. Please try again in a minute.',
  503: 'The service is not working right now. Please try again in a minute.',
  504: 'The server took too long to answer. Please try again.',
}

// The backend sends {error: {code, message, fields}} on every error (M3 4.2); every validation
// error, including /confirm and /manual-entries, is 400 VALIDATION_ERROR (D-30).
const CODES = { 400: 'VALIDATION_ERROR', 401: 'UNAUTHORIZED', 404: 'NOT_FOUND', 409: 'INVALID_STATE', 429: 'RATE_LIMITED' }

function fieldName(loc) {
  // FastAPI locations look like ["body", "lines", 0, "quantity"]; drop the request part.
  const path = (Array.isArray(loc) ? loc : []).filter(part => !['body', 'query', 'path', 'header', 'form'].includes(part))
  return path.length ? path.join('.') : ''
}

// Accepts the M3 shape {error: {code, message, fields}} and FastAPI's {detail: string | [{loc, msg}]}.
export function errorFromResponse(status, body) {
  const fallback = STATUS_MESSAGES[status] || (status >= 500 ? STATUS_MESSAGES[500] : 'Request failed. Please try again.')
  const code = CODES[status] || (status >= 500 ? 'SERVER_ERROR' : 'REQUEST_FAILED')
  if (body?.error && typeof body.error === 'object') {
    return new ApiError(body.error.message || fallback, { status, code: body.error.code || code, fields: body.error.fields || {} })
  }
  const detail = body?.detail
  if (typeof detail === 'string' && detail && !(status === 401 && detail === 'Unauthorized')) {
    return new ApiError(detail, { status, code })
  }
  if (Array.isArray(detail) && detail.length) {
    const fields = {}
    const messages = detail.map(item => {
      const name = fieldName(item?.loc)
      const message = String(item?.msg || 'Invalid value').replace(/^Value error, /, '')
      if (name && !fields[name]) fields[name] = message
      return name ? `${name}: ${message}` : message
    })
    return new ApiError(messages.join('; '), { status, code, fields })
  }
  return new ApiError(fallback, { status, code })
}

async function readBody(response) {
  if (response.status === 204) return null
  const text = await response.text()
  if (!text) return null
  try { return JSON.parse(text) } catch { return undefined }
}

/**
 * Returns `request(path, options)`: fetch with the base URL, Authorization header and JSON handling.
 * - `options.body` that is a plain object is sent as JSON; FormData is sent as multipart.
 * - Pass `options.signal` to control cancellation yourself (capture uses a 90 s limit);
 *   otherwise the request is aborted after `timeoutMs` with a plain-English ApiError.
 * - A 401 calls `onUnauthorized` (sign out, back to /login) before the error is thrown.
 */
export function createApiClient({
  getAccessCode = () => '', onUnauthorized = () => {}, baseUrl = apiBaseUrl(),
  fetchImpl = (...args) => globalThis.fetch(...args), timeoutMs = DEFAULT_TIMEOUT_MS,
} = {}) {
  return async function request(path, options = {}) {
    const { accessCode = getAccessCode(), timeoutMs: requestTimeout = timeoutMs, headers = {}, signal, ...init } = options
    let body = init.body
    const isRaw = body === undefined || body === null || typeof body === 'string' || body instanceof FormData ||
      body instanceof Blob || body instanceof URLSearchParams
    if (!isRaw) body = JSON.stringify(body)
    const finalHeaders = {
      Accept: 'application/json',
      ...(accessCode && { Authorization: `Bearer ${accessCode}` }),
      // FormData must let the browser set its own multipart boundary.
      ...(body !== undefined && body !== null && !(body instanceof FormData) && { 'Content-Type': 'application/json' }),
      ...headers,
    }

    const controller = signal ? null : new AbortController()
    const timer = controller ? setTimeout(() => controller.abort(), requestTimeout) : null
    let response
    try {
      response = await fetchImpl(`${baseUrl}${path}`, { ...init, body, headers: finalHeaders, signal: signal || controller.signal })
    } catch (err) {
      if (err?.name === 'AbortError') {
        if (signal) throw err // The caller's own timeout or cancellation; it decides the message.
        throw new ApiError('The server took too long to answer. Check your connection and try again.', { code: 'TIMEOUT' })
      }
      throw new ApiError('Could not reach the server. Check your connection and try again.', { code: 'NETWORK_ERROR' })
    } finally {
      if (timer) clearTimeout(timer)
    }

    const data = await readBody(response)
    if (!response.ok) {
      const error = errorFromResponse(response.status, data)
      if (response.status === 401) onUnauthorized(error)
      throw error
    }
    if (data === undefined) throw new ApiError('The server sent an unreadable answer. Please try again.', { status: response.status, code: 'BAD_RESPONSE' })
    return data
  }
}
