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

  it('lets the owner type and confirm a rejected submission directly (M3 Diagram 4)', async () => {
    const request = vi.fn().mockResolvedValue({ recordIds: ['r1'], created: true })
    const { user } = review({ status: 'REJECTED', lines: [] }, request)
    expect(screen.queryByRole('button', { name: 'Review manually' })).not.toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Add row' }))
    await user.type(screen.getByRole('textbox', { name: 'Row 1 Item' }), 'Rice (lb)')
    await user.type(screen.getByRole('textbox', { name: 'Row 1 Quantity' }), '2.25')
    await user.type(screen.getByRole('textbox', { name: 'Row 1 Unit price' }), '0.80')
    await user.click(screen.getByRole('button', { name: 'Confirm sales' }))
    expect(request).toHaveBeenCalledTimes(1)
    const body = JSON.parse(request.mock.calls[0][1].body)
    expect(body.lines[0]).toMatchObject({ source: 'MANUAL', draftLineId: null, quantity: '2.25', unitPrice: '0.80' })
  })

  it('accepts integer line IDs from extraction (M3 7.3) and shows plain-language states', async () => {
    const request = vi.fn().mockResolvedValue({ recordIds: ['r1'], created: true })
    const { user } = review({ lines: [{ ...draft.lines[0], lineId: 1 }] }, request)
    expect(screen.getByText(/Needs checking/)).toBeInTheDocument()
    expect(screen.queryByText(/NEEDS_VERIFICATION/)).not.toBeInTheDocument()
    await user.click(screen.getByRole('checkbox', { name: 'Please verify' }))
    await user.click(screen.getByRole('button', { name: 'Confirm sales' }))
    expect(JSON.parse(request.mock.calls[0][1].body).lines[0].draftLineId).toBe(1)
  })

  it('shows a confirmed submission as saved and read-only', () => {
    review({ status: 'CONFIRMED', lines: [] })
    expect(screen.getByText(/already saved/)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Add row' })).toBeDisabled()
  })
})

const record = (overrides = {}) => ({
  recordId: crypto.randomUUID(), submissionId: 'sub-1', saleDate: '2026-10-01', lineNo: 1, itemName: 'Bread',
  quantity: '1', unitPrice: '0', lineTotal: '0', source: 'MANUAL', ...overrides,
})
const ledger = records => ({ start: '2026-09-04', end: '2026-10-01', records })

describe('Confirmed sales ledger', () => {
  it('filters by a date range', async () => {
    const request = vi.fn().mockResolvedValue(ledger([]))
    const user = userEvent.setup()
    render(<SalesHistory request={request} revision={0} />)
    await screen.findByText(/No confirmed sales in these dates/)
    await user.type(screen.getByLabelText('From'), '2026-01-01')
    await waitFor(() => expect(request).toHaveBeenLastCalledWith('/api/v1/sales?start=2026-01-01'))
  })

  it('deletes a submission only after DELETE is typed, and reports the count (FR-304)', async () => {
    const rows = [record({ itemName: 'Bread' }), record({ itemName: 'Milk', lineNo: 2 }), record({ submissionId: 'sub-2', itemName: 'Eggs' })]
    const request = vi.fn(path => (path.startsWith('/api/v1/sales') ? Promise.resolve(ledger(rows)) : Promise.resolve(null)))
    const user = userEvent.setup()
    render(<SalesHistory request={request} revision={0} />)
    const buttons = await screen.findAllByRole('button', { name: /Delete submission from 2026-10-01/ })
    expect(buttons).toHaveLength(2) // One per submission, not per row.
    await user.click(buttons[0])
    expect(screen.getByText(/This deletes 2 records/)).toBeInTheDocument()
    const confirm = screen.getByRole('button', { name: 'Delete' })
    expect(confirm).toBeDisabled()
    await user.type(screen.getByLabelText('Type DELETE'), 'delete')
    expect(confirm).toBeDisabled()  // Exact word only.
    await user.click(screen.getByRole('button', { name: 'Cancel' }))
    expect(screen.getByRole('status')).toHaveTextContent('Nothing was deleted.')
    expect(request.mock.calls.some(([, options]) => options?.method === 'DELETE')).toBe(false)

    await user.click(screen.getAllByRole('button', { name: /Delete submission from 2026-10-01/ })[0])
    await user.type(screen.getByLabelText('Type DELETE'), 'DELETE')
    await user.click(screen.getByRole('button', { name: 'Delete' }))
    expect(request).toHaveBeenCalledWith('/api/v1/submissions/sub-1', { method: 'DELETE' })
    expect(await screen.findByText('Deleted 2 records from 2026-10-01.')).toBeInTheDocument()
  })

  it('keeps everything when a delete fails', async () => {
    const request = vi.fn(path => (path.startsWith('/api/v1/sales') ? Promise.resolve(ledger([record()])) : Promise.reject(new Error('Server busy.'))))
    const user = userEvent.setup()
    render(<SalesHistory request={request} revision={0} />)
    await user.click(await screen.findByRole('button', { name: /Delete submission/ }))
    await user.type(screen.getByLabelText('Type DELETE'), 'DELETE')
    await user.click(screen.getByRole('button', { name: 'Delete' }))
    expect(await screen.findByText('Server busy. Nothing was deleted.')).toBeInTheDocument()
  })

  it('shows loading then an empty state', async () => {
    let finish
    const request = vi.fn(() => new Promise(resolve => { finish = resolve }))
    render(<SalesHistory request={request} revision={0} />)
    expect(screen.getByRole('status')).toHaveTextContent('Loading sales')
    finish({ start: '2026-04-03', end: '2026-04-30', records: [] })
    expect(await screen.findByText(/No confirmed sales in these dates/)).toBeInTheDocument()
    expect(screen.getByText('Showing 2026-04-03 to 2026-04-30.')).toBeInTheDocument()
    expect(request).toHaveBeenCalledWith('/api/v1/sales')
  })

  it('shows failures and refreshes successfully', async () => {
    const request = vi.fn().mockRejectedValueOnce(new Error('Unauthorized')).mockResolvedValueOnce({ start: '2026-09-04', end: '2026-10-01', records: [record({ itemName: 'Bread', quantity: '2.5', unitPrice: '1.20', lineTotal: '3.000' })] })
    const user = userEvent.setup()
    render(<SalesHistory request={request} revision={0} />)
    expect(await screen.findByRole('alert')).toHaveTextContent('Unauthorized')
    await user.click(screen.getByRole('button', { name: 'Refresh history' }))
    expect(await screen.findByText('Bread')).toBeInTheDocument()
    expect(screen.getByText('2.5')).toBeInTheDocument()
    expect(screen.getByText('1.20')).toBeInTheDocument()
    expect(screen.getByText('3.000')).toBeInTheDocument()
    expect(screen.getByText('Typed by you')).toBeInTheDocument()
  })

  it('reloads records after confirmation changes the revision', async () => {
    const request = vi.fn().mockResolvedValueOnce(ledger([])).mockResolvedValueOnce(ledger([record({ itemName: 'Milk' })]))
    const { rerender } = render(<SalesHistory request={request} revision={0} />)
    await screen.findByText(/No confirmed sales in these dates/)
    rerender(<SalesHistory request={request} revision={1} />)
    expect(await screen.findByText('Milk')).toBeInTheDocument()
    await waitFor(() => expect(request).toHaveBeenCalledTimes(2))
  })
})


describe('Review contract regressions', () => {
  it.each([undefined, '', ' '])('keeps warnings and blocks a draft with missing/invalid ID %s', async (lineId) => {
    const { user, request } = review({ lines: [{ ...draft.lines[0], lineId }] })
    expect(screen.getByRole('alert')).toHaveTextContent('Reopen it before confirming')
    const verify = screen.getByRole('checkbox', { name: 'Please verify' })
    await user.click(verify)
    await user.click(screen.getByRole('button', { name: 'Confirm sales' }))
    expect(screen.getByRole('button', { name: 'Confirm sales' })).toBeDisabled()
    expect(request).not.toHaveBeenCalled()
  })

  it('blocks duplicate IDs without coupling edits or removing both rows', async () => {
    const { user, request } = review({ lines: [draft.lines[0], { ...draft.lines[0], itemName: 'Milk' }] })
    const price = screen.getByRole('textbox', { name: 'Row 1 Unit price' })
    await user.clear(price)
    await user.type(price, '2.25')
    expect(screen.getByRole('textbox', { name: 'Row 2 Unit price' })).toHaveValue('1.50')
    expect(screen.getByRole('button', { name: 'Confirm sales' })).toBeDisabled()
    expect(request).not.toHaveBeenCalled()
    await user.click(screen.getByRole('button', { name: 'Remove row 2' }))
    expect(screen.queryByRole('textbox', { name: 'Row 2 Item' })).not.toBeInTheDocument()
    expect(price).toHaveValue('2.25')
    expect(screen.getByRole('button', { name: 'Confirm sales' })).toBeEnabled()
  })

  it('marks added rows as manual while retaining extracted links and verification', async () => {
    const request = vi.fn().mockResolvedValue({ recordIds: ['a', 'b'], created: true })
    const { user } = review({}, request)
    await user.click(screen.getByRole('checkbox', { name: 'Please verify' }))
    await user.click(screen.getByRole('button', { name: 'Add row' }))
    await user.type(screen.getByRole('textbox', { name: 'Row 2 Item' }), 'Milk')
    await user.type(screen.getByRole('textbox', { name: 'Row 2 Quantity' }), '1')
    await user.type(screen.getByRole('textbox', { name: 'Row 2 Unit price' }), '0')
    await user.click(screen.getByRole('button', { name: 'Confirm sales' }))
    const body = JSON.parse(request.mock.calls[0][1].body)
    expect(body.lines[0]).toMatchObject({ source: 'AI_EXTRACTED', draftLineId: 'a', verifiedFields: ['unitPrice'] })
    expect(body.lines[1]).toMatchObject({ source: 'MANUAL', draftLineId: null, itemName: 'Milk' })
  })

  it.each([['0.01', '0', 'x'], ['99999.99', '9999999.99', 'x'.repeat(100)], ['1E+2', '1.5', 'Eggs'], ['2.50', '1.20', 'Rice (lb)']])(
    'sends boundary quantity %s exactly, without rounding', async (quantity, unitPrice, itemName) => {
      const request = vi.fn().mockResolvedValue({ recordIds: ['a'], created: true })
      const { user } = review({ lines: [{ ...draft.lines[0], itemName, quantity, unitPrice,
        bands: { itemName: 'HIGH', quantity: 'HIGH', unitPrice: 'HIGH' } }] }, request)
      await user.click(screen.getByRole('button', { name: 'Confirm sales' }))
      expect(JSON.parse(request.mock.calls[0][1].body).lines[0]).toMatchObject({ itemName, quantity, unitPrice })
    })

  it.each([
    ['quantity', '0.001', 'Use at most 2 decimal places.'], ['quantity', '100000', 'Quantity can be at most 99,999.99.'],
    ['quantity', '1e-400', 'Use at most 2 decimal places.'], ['unitPrice', '10000000', 'Price can be at most 9,999,999.99.'],
    ['itemName', 'x'.repeat(101), 'Use at most 100 characters.'],
  ])('blocks %s %s outside the final M3 limits', (field, value, message) => {
    review({ lines: [{ ...draft.lines[0], [field]: value, bands: { itemName: 'HIGH', quantity: 'HIGH', unitPrice: 'HIGH' } }] })
    expect(screen.getByRole('button', { name: 'Confirm sales' })).toBeDisabled()
    expect(screen.getByText(message)).toBeInTheDocument()
  })

  it.each(['NaN', 'Infinity', '-0.0000001', '0e100'])('blocks invalid quantity %s', (quantity) => {
    review({ lines: [{ ...draft.lines[0], quantity, bands: { itemName: 'HIGH', quantity: 'HIGH', unitPrice: 'HIGH' } }] })
    expect(screen.getByRole('button', { name: 'Confirm sales' })).toBeDisabled()
    expect(screen.getByRole('textbox', { name: 'Row 1 Quantity' })).toHaveAttribute('aria-invalid', 'true')
  })
})
