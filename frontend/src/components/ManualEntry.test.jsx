import { describe, expect, it, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import ManualEntry from './ManualEntry'
import SalesCapture from './SalesCapture'

const today = () => '2026-04-30'

async function fillRow(user, index, item, quantity, price) {
  await user.type(screen.getByRole('textbox', { name: `Row ${index} Item` }), item)
  await user.type(screen.getByRole('textbox', { name: `Row ${index} Quantity` }), quantity)
  await user.type(screen.getByRole('textbox', { name: `Row ${index} Unit price` }), price)
}

describe('Standalone manual entry (FR-104)', () => {
  it('saves typed rows with a UUID generated once per form, with no AI call', async () => {
    const request = vi.fn().mockResolvedValue({ recordIds: ['a', 'b'], created: true })
    const onSaved = vi.fn()
    const user = userEvent.setup()
    render(<ManualEntry request={request} onSaved={onSaved} today={today} />)
    const save = screen.getByRole('button', { name: 'Save sales' })
    expect(save).toBeDisabled()
    await fillRow(user, 1, 'Rice (lb)', '2.25', '0.80')
    await user.click(screen.getByRole('button', { name: 'Add row' }))
    await fillRow(user, 2, 'Milk', '1', '1.20')
    await user.click(save)
    expect(request).toHaveBeenCalledTimes(1)
    const [path, options] = request.mock.calls[0]
    expect(path).toBe('/api/v1/manual-entries')
    expect(options.method).toBe('POST')
    expect(options.body.submissionId).toMatch(/^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/)
    expect(options.body).toMatchObject({ saleDate: '2026-04-30', lines: [
      { itemName: 'Rice (lb)', quantity: 2.25, unitPrice: 0.8 }, { itemName: 'Milk', quantity: 1, unitPrice: 1.2 }] })
    expect(await screen.findByText('Sales saved. 2 records created.')).toBeInTheDocument()
    expect(onSaved).toHaveBeenCalledOnce()
  })

  it('retries with the same submission ID so a network error cannot duplicate sales', async () => {
    const request = vi.fn().mockRejectedValueOnce(new Error('Could not reach the server.'))
      .mockResolvedValueOnce({ recordIds: ['a'], created: false })
    const user = userEvent.setup()
    render(<ManualEntry request={request} today={today} />)
    await fillRow(user, 1, 'Bread', '3', '1.50')
    await user.click(screen.getByRole('button', { name: 'Save sales' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('Nothing was lost')
    await user.click(screen.getByRole('button', { name: 'Save sales' }))
    expect(await screen.findByText('Sales saved. 1 record already saved.')).toBeInTheDocument()
    expect(request.mock.calls[0][1].body.submissionId).toBe(request.mock.calls[1][1].body.submissionId)
  })

  it('starts a new form with a new ID after saving', async () => {
    const request = vi.fn().mockResolvedValue({ recordIds: ['a'], created: true })
    const user = userEvent.setup()
    render(<ManualEntry request={request} today={today} />)
    await fillRow(user, 1, 'Bread', '3', '1.50')
    await user.click(screen.getByRole('button', { name: 'Save sales' }))
    await user.click(await screen.findByRole('button', { name: 'Enter more sales' }))
    await fillRow(user, 1, 'Eggs', '12', '0.25')
    await user.click(screen.getByRole('button', { name: 'Save sales' }))
    expect(request.mock.calls[0][1].body.submissionId).not.toBe(request.mock.calls[1][1].body.submissionId)
  })

  it.each([['quantity', '0', 'Quantity must be greater than zero.'], ['quantity', '1.234', 'Use at most 2 decimal places.'],
    ['unitPrice', '-1', 'Price cannot be negative.']])('blocks invalid %s %s', async (field, value, message) => {
    const user = userEvent.setup()
    render(<ManualEntry request={vi.fn()} today={today} />)
    await fillRow(user, 1, 'Bread', field === 'quantity' ? value : '1', field === 'unitPrice' ? value : '1')
    expect(screen.getByText(message)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Save sales' })).toBeDisabled()
  })

  it('rejects a future sale date', async () => {
    const user = userEvent.setup()
    render(<ManualEntry request={vi.fn()} today={today} />)
    const date = screen.getByLabelText('Sale date')
    await user.clear(date)
    await user.type(date, '2026-05-01')
    expect(screen.getByText('Sale date cannot be in the future.')).toBeInTheDocument()
  })

  it('is the third capture tab and needs no extraction request', async () => {
    const request = vi.fn()
    const user = userEvent.setup()
    render(<SalesCapture request={request} onDraft={vi.fn()} today={today} />)
    await user.click(screen.getByRole('tab', { name: 'Enter manually' }))
    expect(screen.getByRole('heading', { name: 'Enter sales manually' })).toBeInTheDocument()
    expect(screen.getByText('Type each item you sold. Nothing is sent to the AI service.')).toBeInTheDocument()
    expect(request).not.toHaveBeenCalled()
  })
})
