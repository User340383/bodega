import { beforeEach, describe, expect, it, vi } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Route, Routes } from 'react-router'
import DraftsPage from './DraftsPage'
import ReviewPage from './ReviewPage'
import DraftReview from '../components/DraftReview'

const auth = { request: vi.fn() }
vi.mock('../auth/useAuth', () => ({ useAuth: () => auth }))

const SID = '10000000-0000-4000-8000-000000000003'
const unavailable = { submissionId: SID, status: 'AI_UNAVAILABLE', inputType: 'TEXT', saleDate: '2026-04-30',
  createdAt: '2026-04-30T10:00:00Z', lineCount: 0 }
const detail = (overrides = {}) => ({ ...unavailable, lines: [], manualEntry: true, maskedText: 'Bread 3 1.50',
  imageUrl: null, message: 'We could not read this right now. You can type the sales below.', ...overrides })
const readable = detail({ status: 'NEEDS_VERIFICATION', manualEntry: false, message: null,
  lines: [{ lineId: 1, itemName: 'Bread', quantity: '3', unitPrice: '1.50', bands: { itemName: 'HIGH', quantity: 'HIGH', unitPrice: 'HIGH' } }] })

function at(path) {
  return render(<MemoryRouter initialEntries={[path]}><Routes>
    <Route path="/drafts" element={<DraftsPage />} />
    <Route path="/review/:submissionId" element={<ReviewPage />} />
  </Routes></MemoryRouter>)
}

beforeEach(() => { auth.request.mockReset() })  // Block body: a returned function would run as teardown.

describe('Pending drafts (FR-216)', () => {
  it('loads drafts from the server and opens one for manual entry', async () => {
    auth.request.mockImplementation(path => {
      if (path === '/api/v1/submissions') return Promise.resolve([unavailable])
      if (path === `/api/v1/submissions/${SID}`) return Promise.resolve(detail())
      return Promise.reject(new Error(`unexpected ${path}`))
    })
    const user = userEvent.setup()
    at('/drafts')
    await user.click(await screen.findByRole('link', { name: 'Open' }))
    expect(await screen.findByText(/We could not read this right now/)).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /Try reading/ })).not.toBeInTheDocument()
  })

  it('discards a draft and reloads the list', async () => {
    let drafts = [unavailable]
    auth.request.mockImplementation((path, options = {}) => {
      if (path === '/api/v1/submissions') return Promise.resolve(drafts)
      if (options.method === 'DELETE') { drafts = []; return Promise.resolve(null) }
      return Promise.reject(new Error(`unexpected ${path} ${JSON.stringify(options)}`))
    })
    const user = userEvent.setup()
    at('/drafts')
    await user.click(await screen.findByRole('button', { name: 'Discard draft from 2026-04-30' }))
    expect(await screen.findByText('No pending drafts.')).toBeInTheDocument()
  })
})

describe('Review page discard', () => {
  it('discards from the review page and returns to pending drafts', async () => {
    auth.request.mockImplementation((path, options = {}) => {
      if (path === `/api/v1/submissions/${SID}` && !options.method) return Promise.resolve(detail())
      if (options.method === 'DELETE') return Promise.resolve(null)
      if (path === '/api/v1/submissions') return Promise.resolve([])
      return Promise.reject(new Error(`unexpected ${path} ${JSON.stringify(options)}`))
    })
    const user = userEvent.setup()
    at(`/review/${SID}`)
    await user.click(await screen.findByRole('button', { name: 'Discard this draft' }))
    expect(await screen.findByText('No pending drafts.')).toBeInTheDocument()
  })
})

describe('Confirmation errors (422, D-30)', () => {
  it('shows error.fields on the matching row and field, and clears them on edit', async () => {
    const error = Object.assign(new Error('Row 1: tick or correct this value.'), {
      status: 422, code: 'VALIDATION_ERROR', fields: { 'lines.0.quantity': 'Row 1: tick or correct this value.' } })
    const request = vi.fn().mockRejectedValue(error)
    const user = userEvent.setup()
    render(<DraftReview draft={readable} request={request} />)
    await user.click(screen.getByRole('button', { name: 'Confirm sales' }))
    const quantity = screen.getByLabelText('Row 1 Quantity')
    await waitFor(() => expect(quantity).toHaveAttribute('aria-invalid', 'true'))
    expect(screen.getAllByText('Row 1: tick or correct this value.').length).toBeGreaterThan(0)
    await user.type(quantity, '0')
    expect(screen.getByLabelText('Row 1 Quantity')).toHaveAttribute('aria-invalid', 'false')
  })
})
