import { describe, expect, it, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import DeleteAllData from './DeleteAllData'

describe('Delete all stored sales data (FR-304, SRS AC-21)', () => {
  it('deletes everything only after DELETE is typed and reports the count', async () => {
    const request = vi.fn().mockResolvedValue({ recordsDeleted: 5, submissionsDeleted: 5, message: '5 records deleted' })
    const onDeleted = vi.fn()
    const user = userEvent.setup()
    render(<DeleteAllData request={request} onDeleted={onDeleted} />)
    await user.click(screen.getByRole('button', { name: 'Delete all data' }))
    const confirm = screen.getByRole('button', { name: 'Delete all data' })
    expect(confirm).toBeDisabled()
    await user.type(screen.getByLabelText('Type DELETE to confirm'), 'DELETE')
    await user.click(confirm)
    expect(request).toHaveBeenCalledWith('/api/v1/data/delete-all', { method: 'POST', body: { confirmation: 'DELETE' } })
    expect(await screen.findByText('5 records deleted')).toBeInTheDocument()
    expect(onDeleted).toHaveBeenCalledOnce()
  })

  it.each(['delete', 'DELETE ALL', 'yes'])('typing %s deletes nothing', async (text) => {
    const request = vi.fn()
    const user = userEvent.setup()
    render(<DeleteAllData request={request} />)
    await user.click(screen.getByRole('button', { name: 'Delete all data' }))
    await user.type(screen.getByLabelText('Type DELETE to confirm'), text)
    expect(screen.getByRole('button', { name: 'Delete all data' })).toBeDisabled()
    expect(request).not.toHaveBeenCalled()
  })

  it('canceling deletes nothing', async () => {
    const request = vi.fn()
    const user = userEvent.setup()
    render(<DeleteAllData request={request} />)
    await user.click(screen.getByRole('button', { name: 'Delete all data' }))
    await user.type(screen.getByLabelText('Type DELETE to confirm'), 'DELETE')
    await user.click(screen.getByRole('button', { name: 'Cancel' }))
    expect(screen.getByRole('status')).toHaveTextContent('Nothing was deleted.')
    expect(request).not.toHaveBeenCalled()
  })
})
