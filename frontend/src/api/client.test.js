import { describe, expect, it, vi } from 'vitest'
import { ApiError, apiBaseUrl, createApiClient, errorFromResponse } from './client'

function reply(status, body, { raw = false } = {}) {
  const text = body === undefined ? '' : raw ? body : JSON.stringify(body)
  return Promise.resolve(new Response(status === 204 ? null : text, { status }))
}

describe('apiBaseUrl', () => {
  it('is empty without config and trims trailing slashes', () => {
    expect(apiBaseUrl(undefined)).toBe('')
    expect(apiBaseUrl({ apiBaseUrl: ' https://api.example.com/// ' })).toBe('https://api.example.com')
  })
})

describe('createApiClient', () => {
  it('prefixes the base URL, sends the Bearer code and JSON-encodes plain objects', async () => {
    const fetchImpl = vi.fn(() => reply(200, { ok: true }))
    const request = createApiClient({ baseUrl: 'https://api.example.com', getAccessCode: () => 'secret', fetchImpl })
    await expect(request('/api/v1/guidance', { method: 'POST', body: { asOf: '2026-04-30' } })).resolves.toEqual({ ok: true })
    const [url, init] = fetchImpl.mock.calls[0]
    expect(url).toBe('https://api.example.com/api/v1/guidance')
    expect(init.headers.Authorization).toBe('Bearer secret')
    expect(init.headers['Content-Type']).toBe('application/json')
    expect(init.body).toBe('{"asOf":"2026-04-30"}')
  })

  it('leaves FormData to the browser and passes pre-encoded strings through', async () => {
    const fetchImpl = vi.fn(() => reply(200, {}))
    const request = createApiClient({ getAccessCode: () => 'c', fetchImpl })
    const form = new FormData()
    await request('/api/v1/extract', { method: 'POST', body: form })
    expect(fetchImpl.mock.calls[0][1].body).toBe(form)
    expect(fetchImpl.mock.calls[0][1].headers['Content-Type']).toBeUndefined()
    await request('/api/v1/confirm', { method: 'POST', body: '{"a":1}' })
    expect(fetchImpl.mock.calls[1][1].body).toBe('{"a":1}')
    expect(fetchImpl.mock.calls[1][1].headers['Content-Type']).toBe('application/json')
  })

  it('a per-request accessCode overrides the session code; no code sends no header', async () => {
    const fetchImpl = vi.fn(() => reply(200, {}))
    await createApiClient({ getAccessCode: () => 'session', fetchImpl })('/x', { accessCode: 'candidate' })
    expect(fetchImpl.mock.calls[0][1].headers.Authorization).toBe('Bearer candidate')
    await createApiClient({ fetchImpl })('/x')
    expect(fetchImpl.mock.calls[1][1].headers.Authorization).toBeUndefined()
  })

  it('returns null for 204 No Content', async () => {
    const request = createApiClient({ fetchImpl: () => reply(204) })
    await expect(request('/api/v1/submissions/1', { method: 'DELETE' })).resolves.toBeNull()
  })

  it('calls onUnauthorized on 401 and throws a plain-English ApiError', async () => {
    const onUnauthorized = vi.fn()
    const request = createApiClient({ fetchImpl: () => reply(401, { detail: 'Unauthorized' }), onUnauthorized })
    const error = await request('/api/v1/sales').catch(err => err)
    expect(error).toBeInstanceOf(ApiError)
    expect(error.status).toBe(401)
    expect(error.message).toMatch(/sign in again/)
    expect(onUnauthorized).toHaveBeenCalledOnce()
  })

  it('turns a non-JSON gateway page into a friendly error', async () => {
    const request = createApiClient({ fetchImpl: () => reply(502, '<html>Bad gateway</html>', { raw: true }) })
    await expect(request('/x')).rejects.toMatchObject({ status: 502, message: expect.stringMatching(/starting up/) })
  })

  it('reports network failures without leaking the browser message', async () => {
    const request = createApiClient({ fetchImpl: () => Promise.reject(new TypeError('Failed to fetch')) })
    await expect(request('/x')).rejects.toMatchObject({ code: 'NETWORK_ERROR', message: expect.stringMatching(/Could not reach/) })
  })

  it('times out on its own when the caller gives no signal', async () => {
    vi.useFakeTimers()
    try {
      const fetchImpl = (url, { signal }) => new Promise((resolve, reject) => {
        signal.addEventListener('abort', () => reject(new DOMException('aborted', 'AbortError')))
      })
      const pending = createApiClient({ fetchImpl, timeoutMs: 1000 })('/slow').catch(err => err)
      await vi.advanceTimersByTimeAsync(1000)
      expect(await pending).toMatchObject({ code: 'TIMEOUT' })
    } finally { vi.useRealTimers() }
  })

  it("rethrows the caller's own AbortError so the caller chooses the message", async () => {
    const controller = new AbortController()
    const fetchImpl = (url, { signal }) => new Promise((resolve, reject) => {
      signal.addEventListener('abort', () => reject(new DOMException('aborted', 'AbortError')))
    })
    const pending = createApiClient({ fetchImpl })('/api/v1/extract', { signal: controller.signal }).catch(err => err)
    controller.abort()
    expect((await pending).name).toBe('AbortError')
  })
})

describe('errorFromResponse', () => {
  it('reads the M3 error body', () => {
    const error = errorFromResponse(400, { error: { code: 'VALIDATION_ERROR', message: 'Sale date cannot be in the future.', fields: { saleDate: 'future' } } })
    expect(error).toMatchObject({ code: 'VALIDATION_ERROR', message: 'Sale date cannot be in the future.', fields: { saleDate: 'future' } })
  })

  it('reads FastAPI string and list details', () => {
    expect(errorFromResponse(503, { detail: 'We could not save your photo. Please try again.' }).message).toBe('We could not save your photo. Please try again.')
    const error = errorFromResponse(400, { detail: [
      { loc: ['body', 'lines', 0, 'quantity'], msg: 'Input should be greater than 0' },
      { loc: ['body'], msg: 'Value error, Provide exactly one image file or text input.' },
    ] })
    expect(error.message).toBe('lines.0.quantity: Input should be greater than 0; Provide exactly one image file or text input.')
    expect(error.fields).toEqual({ 'lines.0.quantity': 'Input should be greater than 0' })
  })

  it('falls back to a status message', () => {
    expect(errorFromResponse(429, null)).toMatchObject({ code: 'RATE_LIMITED', message: expect.stringMatching(/15 minutes/) })
    expect(errorFromResponse(500, undefined).message).toMatch(/Something went wrong/)
  })
})
