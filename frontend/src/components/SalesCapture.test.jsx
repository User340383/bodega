import { describe, expect, it, vi } from 'vitest'
import { fireEvent, render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import SalesCapture from './SalesCapture'
import { INPUT_NOTICE, LOADING_TEXT, MAX_FILE_BYTES, fileProblem, localToday, textProblem } from './captureRules'

const TODAY = '2026-04-30'
const photo = (name = 'page.jpg', type = 'image/jpeg') => new File(['jpeg-bytes'], name, { type })
const draft = (overrides = {}) => ({
  submissionId: 's-1', status: 'NEEDS_VERIFICATION', saleDate: TODAY, inputType: 'IMAGE', message: null,
  manualEntry: false, imageUrl: null, maskedText: null, lines: [], ...overrides,
})

function setup(request = vi.fn().mockResolvedValue(draft()), options) {
  const onDraft = vi.fn()
  render(<SalesCapture request={request} onDraft={onDraft} today={() => TODAY} />)
  return { user: userEvent.setup(options), request, onDraft }
}

const readPhoto = () => screen.getByRole('button', { name: 'Read photo' })

describe('capture rules', () => {
  it('formats the local date and validates files and text', () => {
    expect(localToday(new Date(2026, 0, 5))).toBe('2026-01-05')
    expect(fileProblem(photo())).toBe('')
    expect(fileProblem(photo('a.png', 'image/png'))).toBe('')
    expect(fileProblem(photo('a.gif', 'image/gif'))).toMatch(/JPEG or PNG/)
    const big = photo()
    Object.defineProperty(big, 'size', { value: MAX_FILE_BYTES + 1 })
    expect(fileProblem(big)).toMatch(/under 10 MB/)
    expect(textProblem('   ')).toMatch(/at least one/)
    expect(textProblem(`  ${'x'.repeat(5000)}  `)).toBe('')
    expect(textProblem('x'.repeat(5001))).toMatch(/at most 5,000/)
  })
})

describe('Sales capture', () => {
  it('shows the exact AI input notice and needs a photo before reading', () => {
    setup()
    expect(screen.getByText(INPUT_NOTICE)).toBeInTheDocument()
    expect(screen.getByLabelText('Sale date')).toHaveValue(TODAY)
    expect(readPhoto()).toBeDisabled()
  })

  it('uploads the photo with the sale date, shows progress and hands the draft to review', async () => {
    let finish
    const request = vi.fn(() => new Promise(resolve => { finish = resolve }))
    const { user, onDraft } = setup(request)
    await user.upload(screen.getByLabelText('Photo of your sales page'), photo())
    await user.click(readPhoto())

    expect(screen.getByRole('status')).toHaveTextContent(LOADING_TEXT)
    const [path, options] = request.mock.calls[0]
    expect(path).toBe('/api/v1/extract')
    expect(options.method).toBe('POST')
    expect(options.signal.aborted).toBe(false)  // 90 s client timeout is wired in.
    expect(options.body.get('saleDate')).toBe(TODAY)
    expect(options.body.get('file').name).toBe('page.jpg')
    expect(options.body.get('text')).toBeNull()

    const result = draft({ lines: [{ lineId: '1', itemName: 'Bread', quantity: '3', unitPrice: '1.50', bands: {} }] })
    finish(result)
    expect(await screen.findByText(/Draft ready/)).toBeInTheDocument()
    expect(onDraft).toHaveBeenCalledWith(result)
    expect(screen.queryByText(LOADING_TEXT)).not.toBeInTheDocument()
  })

  it('rejects other file types before any request', async () => {
    const { user, request } = setup(undefined, { applyAccept: false })
    await user.upload(screen.getByLabelText('Photo of your sales page'), photo('notes.pdf', 'application/pdf'))
    expect(screen.getByText('Choose a JPEG or PNG photo under 10 MB.')).toBeInTheDocument()
    expect(readPhoto()).toBeDisabled()
    expect(request).not.toHaveBeenCalled()
  })

  it('blocks future sale dates', async () => {
    const { user } = setup()
    await user.upload(screen.getByLabelText('Photo of your sales page'), photo())
    fireEvent.change(screen.getByLabelText('Sale date'), { target: { value: '2026-05-01' } })
    expect(screen.getByText('Sale date cannot be in the future.')).toBeInTheDocument()
    expect(readPhoto()).toBeDisabled()
  })

  it('sends trimmed typed text and shows the masked text that reached the AI', async () => {
    const request = vi.fn().mockResolvedValue(draft({ inputType: 'TEXT', maskedText: 'Bread 3 1.50 [removed]' }))
    const { user } = setup(request)
    await user.click(screen.getByRole('tab', { name: 'Text' }))
    const button = screen.getByRole('button', { name: 'Read text' })
    expect(button).toBeDisabled()
    fireEvent.change(screen.getByLabelText('Sales text'), { target: { value: '  Bread 3 1.50 809-555-1234  ' } })
    await user.click(button)
    const body = request.mock.calls[0][1].body
    expect(body.get('text')).toBe('Bread 3 1.50 809-555-1234')
    expect(body.get('file')).toBeNull()
    expect(await screen.findByText('Bread 3 1.50 [removed]')).toBeInTheDocument()
  })

  it('rejects text over 5,000 characters', async () => {
    const { user } = setup()
    await user.click(screen.getByRole('tab', { name: 'Text' }))
    fireEvent.change(screen.getByLabelText('Sales text'), { target: { value: 'x'.repeat(5001) } })
    expect(screen.getByText(/at most 5,000 characters/)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Read text' })).toBeDisabled()
  })

  it('falls back to manual entry when the AI is unavailable', async () => {
    const result = draft({ status: 'AI_UNAVAILABLE', manualEntry: true,
      message: 'We could not read this right now. You can type the sales below.' })
    const { user, onDraft } = setup(vi.fn().mockResolvedValue(result))
    await user.upload(screen.getByLabelText('Photo of your sales page'), photo())
    await user.click(readPhoto())
    expect(await screen.findByText(/We could not read this right now/)).toBeInTheDocument()
    expect(screen.getByText(/Add row/)).toBeInTheDocument()
    expect(onDraft).toHaveBeenCalledWith(result)  // DraftReview opens with an empty manual form.
  })

  it('offers a retake after an unreadable photo', async () => {
    const result = draft({ status: 'REJECTED', manualEntry: true, message: 'We could not read this photo. Use good light.' })
    const { user } = setup(vi.fn().mockResolvedValue(result))
    const input = screen.getByLabelText('Photo of your sales page')
    await user.upload(input, photo())
    await user.click(readPhoto())
    await user.click(await screen.findByRole('button', { name: 'Retake photo' }))
    expect(screen.queryByText(/good light/)).not.toBeInTheDocument()
    expect(readPhoto()).toBeDisabled()
  })

  it('reports a failed request and allows retrying', async () => {
    const request = vi.fn().mockRejectedValueOnce(new Error('We could not save your photo. Please try again.'))
      .mockResolvedValueOnce(draft())
    const { user, onDraft } = setup(request)
    await user.upload(screen.getByLabelText('Photo of your sales page'), photo())
    await user.click(readPhoto())
    expect(await screen.findByRole('alert')).toHaveTextContent('We could not save your photo')
    expect(onDraft).not.toHaveBeenCalled()
    await user.click(readPhoto())
    expect(await screen.findByText(/Draft ready/)).toBeInTheDocument()
  })
})
