import { describe, expect, it, vi } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import DraftReview from './DraftReview'
import SalesHistory from './SalesHistory'

const draft = {
  submissionId: '10000000-0000-4000-8000-000000000003', saleDate: '2026-10-01', status: 'NEEDS_VERIFICATION',
  lines: [{ lineId: 'a', itemName: 'Bread', quantity: 3, unitPrice: '1.50', bands: { itemName: 'HIGH', quantity: 'HIGH', unitPrice: 'MEDIUM' } }],
}

function review(overrides = {}, request = vi.fn(), onConfirmed = vi.fn()) {
  render(<DraftReview draft={{ ...draft, ...overrides }} request={request} onConfirmed={onConfirmed} />)
  return { user: userEvent.setup(), request, onConfirmed }
}

describe('Draft review', () => {
  it('requires verification, submits exact decimal strings and reports success', async () => {
    const request = vi.fn().mockResolvedValue({ recordIds: ['saved'], created: true })
    const { user, onConfirmed } = review({}, request)
    expect(screen.getByRole('button', { name: 'Confirm sales' })).toBeDisabled()
    await user.click(screen.getByRole('checkbox', { name: 'Please verify' }))
    await user.click(screen.getByRole('button', { name: 'Confirm sales' }))
    expect(request).toHaveBeenCalledWith('/api/v1/confirm', expect.objectContaining({ method: 'POST' }))
    const body = JSON.parse(request.mock.calls[0][1].body)
    expect(body.lines[0]).toMatchObject({ draftLineId: 'a', quantity: '3', unitPrice: '1.50', verifiedFields: ['unitPrice'] })
    expect(await screen.findByText(/Sales saved/)).toBeInTheDocument()
    expect(onConfirmed).toHaveBeenCalledOnce()
    expect(screen.getByRole('button', { name: 'Confirm sales' })).toBeDisabled()
  })

  it('blanks low-confidence fields and permits correction without a separate verification click', async () => {
    const { user } = review({ lines: [{ ...draft.lines[0], bands: { itemName: 'HIGH', quantity: 'LOW', unitPrice: 'HIGH' } }] })
    const quantity = screen.getByRole('textbox', { name: 'Row 1 Quantity' })
    expect(quantity).toHaveValue('')
    expect(screen.getByRole('button', { name: 'Confirm sales' })).toBeDisabled()
    await user.type(quantity, '2.5')
    expect(screen.getByRole('button', { name: 'Confirm sales' })).toBeEnabled()
  })

  it('blocks empty or invalid drafts, supports adding and removing rows', async () => {
    const { user } = review({ lines: [], status: 'AI_UNAVAILABLE' })
    const confirm = screen.getByRole('button', { name: 'Confirm sales' })
    expect(confirm).toBeDisabled()
    await user.click(screen.getByRole('button', { name: 'Add row' }))
    await user.type(screen.getByRole('textbox', { name: 'Row 1 Item' }), 'Milk')
    const quantity = screen.getByRole('textbox', { name: 'Row 1 Quantity' })
    await user.type(quantity, '0')
    await user.type(screen.getByRole('textbox', { name: 'Row 1 Unit price' }), '0')
    expect(confirm).toBeDisabled()
    await user.clear(quantity)
    await user.type(quantity, '1')
    expect(confirm).toBeEnabled()
    await user.click(screen.getByRole('button', { name: 'Remove row 1' }))
    expect(confirm).toBeDisabled()
  })

  it('retains corrected values after a failure and allows retry', async () => {
    const request = vi.fn().mockRejectedValueOnce(new Error('Database unavailable')).mockResolvedValueOnce({ recordIds: ['saved'], created: false })
    const { user } = review({}, request)
    const price = screen.getByRole('textbox', { name: 'Row 1 Unit price' })
    await user.clear(price)
    await user.type(price, '2.25')
    await user.click(screen.getByRole('button', { name: 'Confirm sales' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('Database unavailable')
    expect(price).toHaveValue('2.25')
    await user.click(screen.getByRole('button', { name: 'Confirm sales' }))
    expect(await screen.findByText(/already confirmed/)).toBeInTheDocument()
  })

  it('explicitly recovers a rejected submission into manual review', async () => {
    const request = vi.fn().mockResolvedValue({ status: 'NEEDS_VERIFICATION', lines: [] })
    const { user } = review({ status: 'REJECTED', lines: [] }, request)
    expect(screen.getByRole('button', { name: 'Add row' })).toBeDisabled()
    await user.click(screen.getByRole('button', { name: 'Review manually' }))
    expect(request).toHaveBeenCalledWith(`/api/v1/submissions/${draft.submissionId}/manual-review`, { method: 'POST' })
    expect(screen.getByRole('button', { name: 'Add row' })).toBeEnabled()
  })
})

describe('Confirmed sales ledger', () => {
  it('shows loading then an empty state', async () => {
    let finish
    const request = vi.fn(() => new Promise(resolve => { finish = resolve }))
    render(<SalesHistory request={request} revision={0} />)
    expect(screen.getByRole('status')).toHaveTextContent('Loading sales')
    finish({ records: [] })
    expect(await screen.findByText('No confirmed sales on this page.')).toBeInTheDocument()
  })

  it('shows failures and refreshes successfully', async () => {
    const request = vi.fn().mockRejectedValueOnce(new Error('Unauthorized')).mockResolvedValueOnce({ records: [{ id: 'one', saleDate: '2026-10-01', itemName: 'Bread', quantity: '2.5', unitPrice: '1.20', source: 'MANUAL' }] })
    const user = userEvent.setup()
    render(<SalesHistory request={request} revision={0} />)
    expect(await screen.findByRole('alert')).toHaveTextContent('Unauthorized')
    await user.click(screen.getByRole('button', { name: 'Refresh history' }))
    expect(await screen.findByText('Bread')).toBeInTheDocument()
    expect(screen.getByText('2.5')).toBeInTheDocument()
    expect(screen.getByText('1.20')).toBeInTheDocument()
  })

  it('reloads records after confirmation changes the revision', async () => {
    const request = vi.fn().mockResolvedValueOnce({ records: [] }).mockResolvedValueOnce({ records: [{ id: 'one', itemName: 'Milk', saleDate: '2026-10-01', quantity: '1', unitPrice: '0', source: 'MANUAL' }] })
    const { rerender } = render(<SalesHistory request={request} revision={0} />)
    await screen.findByText('No confirmed sales on this page.')
    rerender(<SalesHistory request={request} revision={1} />)
    expect(await screen.findByText('Milk')).toBeInTheDocument()
    await waitFor(() => expect(request).toHaveBeenCalledTimes(2))
  })
})
