import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, useLocation } from 'react-router'
import App from './App'

const CODE = 'local-development-code-123'
const DRAFT_ID = '10000000-0000-4000-8000-000000000003'

function json(status, body) {
  return Promise.resolve(new Response(JSON.stringify(body), { status }))
}

// A tiny fake backend: the right code opens everything, anything else is 401.
function fakeBackend(overrides = {}) {
  return vi.fn((url, init) => {
    if (init.headers.Authorization !== `Bearer ${CODE}`) return json(401, { detail: 'Unauthorized' })
    const path = url.split('?')[0]
    if (overrides[path]) return overrides[path](url, init)
    if (path === '/api/v1/sales') return json(200, { records: [], limit: 100, offset: 0 })
    if (path === '/api/v1/analysis') return json(200, { status: 'INSUFFICIENT_HISTORY', loggedDays: 0, requiredDays: 7, estimates: [] })
    return json(404, { detail: 'Not found' })
  })
}

function Location() {
  return <div data-testid="location">{useLocation().pathname}</div>
}

function renderApp(path, fetchImpl) {
  return render(<MemoryRouter initialEntries={[path]}>
    <App clientOptions={{ fetchImpl, baseUrl: '' }} />
    <Location />
  </MemoryRouter>)
}

beforeEach(() => sessionStorage.clear())
afterEach(() => sessionStorage.clear())

describe('application shell', () => {
  it('sends a signed-out owner to /login and back to the requested page after signing in', async () => {
    const user = userEvent.setup()
    const fetchImpl = fakeBackend()
    renderApp('/history', fetchImpl)
    expect(screen.getByTestId('location')).toHaveTextContent('/login')

    await user.type(screen.getByLabelText('Access code'), 'wrong-code')
    await user.click(screen.getByRole('button', { name: 'Sign in' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('That access code is not correct.')
    expect(sessionStorage.getItem('bodega.accessCode')).toBeNull()

    await user.clear(screen.getByLabelText('Access code'))
    await user.type(screen.getByLabelText('Access code'), CODE)
    await user.click(screen.getByRole('button', { name: 'Sign in' }))
    await waitFor(() => expect(screen.getByTestId('location')).toHaveTextContent('/history'))
    expect(await screen.findByRole('heading', { name: 'Confirmed sales history' })).toBeInTheDocument()
    expect(sessionStorage.getItem('bodega.accessCode')).toBe(CODE)
  })

  it('shows the main navigation and moves between pages', async () => {
    sessionStorage.setItem('bodega.accessCode', CODE)
    const user = userEvent.setup()
    renderApp('/', fakeBackend())
    expect(screen.getByTestId('location')).toHaveTextContent('/capture')
    expect(screen.getByRole('heading', { name: 'Capture sales' })).toBeInTheDocument()
    const nav = screen.getByRole('navigation', { name: 'Main' })
    for (const name of ['Capture', 'Pending drafts', 'History', 'Analysis']) {
      expect(nav).toContainElement(screen.getByRole('link', { name }))
    }
    await user.click(screen.getByRole('link', { name: 'History' }))
    expect(screen.getByTestId('location')).toHaveTextContent('/history')
    expect(screen.getByRole('link', { name: 'History' })).toHaveClass('active')
  })

  it('signs out when the backend rejects the stored code', async () => {
    sessionStorage.setItem('bodega.accessCode', 'expired-code')
    renderApp('/history', fakeBackend())
    await waitFor(() => expect(screen.getByTestId('location')).toHaveTextContent('/login'))
    expect(screen.getByRole('status')).toHaveTextContent('Please sign in again.')
    expect(sessionStorage.getItem('bodega.accessCode')).toBeNull()
  })

  it('sign out clears the session', async () => {
    sessionStorage.setItem('bodega.accessCode', CODE)
    const user = userEvent.setup()
    renderApp('/drafts', fakeBackend())
    await user.click(screen.getByRole('button', { name: 'Sign out' }))
    expect(screen.getByTestId('location')).toHaveTextContent('/login')
    expect(sessionStorage.getItem('bodega.accessCode')).toBeNull()
  })

  it('opens a draft by ID from Pending drafts and loads it on the review page', async () => {
    sessionStorage.setItem('bodega.accessCode', CODE)
    const user = userEvent.setup()
    const fetchImpl = fakeBackend({
      [`/api/v1/submissions/${DRAFT_ID}`]: () => json(200, {
        submissionId: DRAFT_ID, status: 'NEEDS_VERIFICATION', saleDate: '2026-04-30', inputType: 'TEXT',
        maskedText: 'Bread 3 1.50', imageUrl: null,
        lines: [{ lineId: '1', itemName: 'Bread', quantity: '3', unitPrice: '1.50', bands: { itemName: 'HIGH', quantity: 'HIGH', unitPrice: 'HIGH' } }],
      }),
    })
    renderApp('/drafts', fetchImpl)
    await user.click(screen.getByRole('button', { name: 'Open draft' }))
    expect(screen.getByRole('alert')).toHaveTextContent('Enter a submission ID')
    await user.type(screen.getByLabelText('Submission ID'), DRAFT_ID)
    await user.click(screen.getByRole('button', { name: 'Open draft' }))
    expect(screen.getByTestId('location')).toHaveTextContent(`/review/${DRAFT_ID}`)
    expect(await screen.findByRole('heading', { name: 'Review draft' })).toBeInTheDocument()
    expect(screen.getByText('Bread 3 1.50')).toBeInTheDocument()
    // Now remembered for this tab.
    await user.click(screen.getByRole('link', { name: 'Pending drafts' }))
    expect(screen.getByRole('link', { name: 'Open' })).toHaveAttribute('href', `/review/${DRAFT_ID}`)
  })

  it('shows a not-found page for unknown paths', () => {
    sessionStorage.setItem('bodega.accessCode', CODE)
    renderApp('/nowhere', fakeBackend())
    expect(screen.getByRole('heading', { name: 'Page not found' })).toBeInTheDocument()
  })
})
