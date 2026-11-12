import { describe, expect, it, vi } from 'vitest'
import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import ResultsDashboard from './ResultsDashboard'
import { axisMaximum, chartNumber } from './chartValues'

const point = (day, quantity, movingAverage = null, extra = {}) => ({
  date: `2026-04-${day}`, quantity, observed: true, movingAverage, movingAverageRounded: false, calendarDaysSkipped: 0, ...extra,
})

const bread = {
  itemKey: 'bread', itemName: 'Bread', windowLoggedDays: 7,
  currentAverage: '4', currentAverageRounded: false, currentAverageDisplay: '4.0', currentTotal: '28', currentDaysWithSales: 7,
  previousAverage: '2', previousAverageRounded: false, previousTotal: '14', previousDaysWithSales: 7,
  trend: 'UP', changePercent: '100', suggestedQty: '28',
  series: [point('24', '2'), point('25', '0', null, { observed: false }), point('28', '4', '2.5', { calendarDaysSkipped: 2 }), point('30', '4', '4', { calendarDaysSkipped: 1 })],
}

const baseAnalysis = {
  asOf: '2026-04-30', historyStart: '2026-04-03', historyEnd: '2026-04-30', loggedDays: 14, requiredDays: 7, trendRequiredDays: 14,
  status: 'READY', statusMessage: 'Estimates and trends use the latest 14 of 14 logged sales dates.',
  quantityBasis: 'Suggested quantities are sales-based planning guidance.',
  loggedDates: [],
  currentWindow: { start: '2026-04-24', end: '2026-04-30', loggedDays: 7 },
  previousWindow: { start: '2026-04-17', end: '2026-04-23', loggedDays: 7 },
  estimates: [bread],
}

const guidanceResult = (overrides = {}) => ({
  asOf: '2026-04-30', status: 'READY', source: 'AI_WORDED', groundingPassed: true, failedRule: null, failureReason: null,
  providerCalled: true, provider: 'real', sourceLabel: null,
  stockNotice: 'These numbers come only from the sales you logged. Check what you already have in stock before you buy.',
  items: [{ itemName: 'Bread', suggestedQty: '28', currentAverage: '4', previousAverage: '2', trend: 'UP', message: 'Bread: plan 28 units.' }],
  ...overrides,
})

function deferred() {
  let resolve, reject
  const promise = new Promise((res, rej) => { resolve = res; reject = rej })
  return { promise, resolve, reject }
}

function router({ analysis = baseAnalysis, guidance = guidanceResult() } = {}) {
  return vi.fn((path) => {
    if (path.startsWith('/api/v1/analysis')) return Promise.resolve(analysis)
    if (path === '/api/v1/guidance') return Promise.resolve(guidance)
    return Promise.reject(new Error(`Unexpected ${path}`))
  })
}

const analysisCalls = request => request.mock.calls.filter(([path]) => path.startsWith('/api/v1/analysis'))
const guidanceButton = () => screen.findByRole('button', { name: 'Get restocking advice' })

describe('Results dashboard analysis', () => {
  it('shows the effective date, history range, estimates and exact strings', async () => {
    render(<ResultsDashboard request={router()} revision={0} />)
    expect(screen.getByRole('status')).toHaveTextContent('Loading analysis')
    expect(await screen.findByText('As of 2026-04-30.')).toBeInTheDocument()
    expect(screen.getByText(/from 2026-04-03 to 2026-04-30: 14 logged dates/)).toBeInTheDocument()
    expect(screen.getByRole('heading', { name: 'Bread' })).toBeInTheDocument()
    const card = screen.getByRole('article', { name: 'Bread' })
    // M3 section 3 / 4.3 labels; the average is shown to 1 decimal.
    expect(within(card).getByText('Suggested stock for your next 7 selling days').nextSibling).toHaveTextContent('28')
    expect(within(card).getByText('Average sold per logged day (last 7 logged days)').nextSibling).toHaveTextContent('4.0')
    expect(within(card).getByText('Previous 7 logged days average').nextSibling).toHaveTextContent('2')
    // M3 9.7: the stock-check notice appears with the estimates, not only with advice.
    expect(screen.getByText('Check your stock:').parentElement).toHaveTextContent(
      'These numbers come only from the sales you logged. Check what you already have in stock before you buy.')
    expect(within(card).getByText(/\+100% against the previous 7 logged dates/)).toBeInTheDocument()
    expect(screen.getByText(/sales-based planning guidance/)).toBeInTheDocument()
    expect(screen.queryByRole('status')).not.toBeInTheDocument()
  })

  it('names item cards correctly when the item label has spaces', async () => {
    const analysis = { ...baseAnalysis, estimates: [{ ...bread, itemKey: 'whole milk', itemName: 'Whole milk' }] }
    render(<ResultsDashboard request={router({ analysis })} revision={0} />)
    expect(await screen.findByRole('article', { name: 'Whole milk' })).toBeInTheDocument()
  })

  it.each([['UP', '▲'], ['DOWN', '▼'], ['STABLE', '▬'], ['INSUFFICIENT_DATA', '?']])('labels trend %s with text and a non-color indicator', async (trend, icon) => {
    const analysis = { ...baseAnalysis, estimates: [{ ...bread, trend, changePercent: null }] }
    render(<ResultsDashboard request={router({ analysis })} revision={0} />)
    const label = trend === 'INSUFFICIENT_DATA' ? 'INSUFFICIENT DATA' : trend
    const badge = await screen.findByText(label)
    expect(badge).toHaveTextContent(icon)
  })

  it('renders long decimals without numeric conversion and flags rounded averages', async () => {
    const analysis = { ...baseAnalysis, estimates: [{
      ...bread, suggestedQty: '864197523086419752308', currentAverage: '123456789012345678901.1234567',
      previousAverage: '1.428571', previousAverageRounded: true,
      series: [point('24', '123456789012345678901.1234567', '123456789012345678901.1234567')],
    }] }
    render(<ResultsDashboard request={router({ analysis })} revision={0} />)
    expect(await screen.findByText('864197523086419752308')).toBeInTheDocument()
    expect(screen.getAllByText('123456789012345678901.1234567').length).toBeGreaterThan(0)
    expect(screen.getByText('≈ 1.428571')).toBeInTheDocument()
  })

  it('plots bars, the moving average, absent observations and irregular dates, with a table alternative', async () => {
    render(<ResultsDashboard request={router()} revision={0} />)
    const chart = await screen.findByRole('img', { name: /Bread: confirmed sales and 7-logged-date moving average across 4 logged/ })
    expect(within(chart).getAllByTestId('sales-bar')).toHaveLength(3)
    expect(within(chart).getAllByTestId('absent-marker')).toHaveLength(1)
    expect(within(chart).getAllByTestId('calendar-gap')).toHaveLength(2)
    expect(within(chart).getByTestId('average-line').getAttribute('points').split(' ')).toHaveLength(2)
    expect(screen.getByText('Date logged, item not sold (counted as 0)')).toBeInTheDocument()
    expect(screen.getByText('Calendar dates not logged (unknown, not zero)')).toBeInTheDocument()

    const table = screen.getByRole('table', { name: 'Daily values for Bread', hidden: true })
    const rows = within(table).getAllByRole('row', { hidden: true })
    expect(rows).toHaveLength(5)
    expect(rows[2]).toHaveTextContent('2026-04-25')
    expect(rows[2]).toHaveTextContent('0 (not sold this date)')
    expect(rows[1]).toHaveTextContent('Not enough dates')
    expect(rows[3]).toHaveTextContent('2.5')
    expect(rows[3].lastChild).toHaveTextContent('2')
  })

  it('replaces the chart when values cannot be represented, keeping exact table values', async () => {
    const huge = '1' + '0'.repeat(400)
    const analysis = { ...baseAnalysis, estimates: [{ ...bread, series: [point('24', huge, huge), point('25', '1e-400')] }] }
    render(<ResultsDashboard request={router({ analysis })} revision={0} />)
    expect(await screen.findByText(/too large or too small to plot/)).toBeInTheDocument()
    expect(screen.queryByRole('img')).not.toBeInTheDocument()
    const table = screen.getByRole('table', { hidden: true })
    expect(table).toHaveTextContent(huge)
    expect(table).toHaveTextContent('1e-400')
  })

  it('shows an empty state without fabricated charts or estimates', async () => {
    const analysis = { ...baseAnalysis, status: 'NO_DATA', statusMessage: 'No confirmed sales exist in this 28-day period.', loggedDays: 0, currentWindow: null, previousWindow: null, estimates: [] }
    const request = router({ analysis })
    render(<ResultsDashboard request={request} revision={0} />)
    expect(await screen.findByText('No confirmed sales exist in this 28-day period.')).toBeInTheDocument()
    expect(screen.queryByRole('img')).not.toBeInTheDocument()
    expect(screen.queryByText('Suggested stock for your next 7 selling days')).not.toBeInTheDocument()
    // FR-602: a path to the input form, and no fabricated values.
    expect(screen.getByRole('link', { name: 'Capture sales' })).toHaveAttribute('href', '/capture')
    expect(screen.queryByText('Check your stock:')).not.toBeInTheDocument()
    expect(await guidanceButton()).toBeDisabled()
  })

  it('shows insufficient history with confirmed sales only and no provider call', async () => {
    const analysis = {
      ...baseAnalysis, status: 'INSUFFICIENT_HISTORY', statusMessage: '3 of 7 logged sales dates. 4 more needed for an estimate.', loggedDays: 3, currentWindow: null, previousWindow: null,
      estimates: [{ ...bread, currentAverage: null, currentTotal: null, currentDaysWithSales: null, previousAverage: null, previousTotal: null, previousDaysWithSales: null, trend: 'INSUFFICIENT_DATA', changePercent: null, suggestedQty: null, series: [point('24', '2'), point('25', '2'), point('26', '2')] }],
    }
    const request = router({ analysis })
    render(<ResultsDashboard request={request} revision={0} />)
    expect(await screen.findByText(/4 more needed/)).toBeInTheDocument()
    expect(screen.getByText('INSUFFICIENT DATA')).toBeInTheDocument()
    expect(screen.queryByText('Suggested stock for your next 7 selling days')).not.toBeInTheDocument()
    expect(screen.getByText(/Log at least 7 days of sales to see estimates \(3 of 7 so far\)\./)).toBeInTheDocument()
    expect(screen.queryByTestId('average-line')).not.toBeInTheDocument()
    expect(screen.queryByText('Average of the latest 7 logged dates')).not.toBeInTheDocument()
    expect(within(screen.getByRole('img')).getAllByTestId('sales-bar')).toHaveLength(3)
    expect(await guidanceButton()).toBeDisabled()
    expect(screen.getByText(/after 7 logged sales dates/)).toBeInTheDocument()
    expect(request.mock.calls.some(([path]) => path === '/api/v1/guidance')).toBe(false)
  })

  it('shows estimate-only history with an explicit missing trend', async () => {
    const analysis = { ...baseAnalysis, status: 'ESTIMATE_ONLY', previousWindow: null, estimates: [{ ...bread, previousAverage: null, previousTotal: null, trend: 'INSUFFICIENT_DATA', changePercent: null }] }
    render(<ResultsDashboard request={router({ analysis })} revision={0} />)
    expect(await screen.findByText('INSUFFICIENT DATA')).toBeInTheDocument()
    expect(screen.getByText(/Needs 14 logged dates/)).toBeInTheDocument()
    expect(screen.getByText('Previous 7 logged days average').nextSibling).toHaveTextContent('—')
    expect(await guidanceButton()).toBeEnabled()
  })

  it('reports errors and recovers through the refresh control', async () => {
    const request = vi.fn().mockRejectedValueOnce(new Error('Sales could not be loaded')).mockResolvedValueOnce(baseAnalysis)
    const user = userEvent.setup()
    render(<ResultsDashboard request={request} revision={0} />)
    expect(await screen.findByRole('alert')).toHaveTextContent('Sales could not be loaded')
    expect(screen.queryByRole('button', { name: 'Get restocking advice' })).not.toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Refresh analysis' }))
    expect(await screen.findByRole('heading', { name: 'Bread' })).toBeInTheDocument()
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
  })

  it('reloads after a confirmation changes the revision', async () => {
    const request = router()
    const { rerender } = render(<ResultsDashboard request={request} revision={0} />)
    await screen.findByRole('heading', { name: 'Bread' })
    expect(analysisCalls(request)).toHaveLength(1)
    rerender(<ResultsDashboard request={request} revision={1} />)
    await waitFor(() => expect(analysisCalls(request)).toHaveLength(2))
  })

  it('requests the selected date and ignores a late response for an earlier selection', async () => {
    const slow = deferred()
    const fast = deferred()
    const request = vi.fn(path => {
      if (path === '/api/v1/analysis') return Promise.resolve(baseAnalysis)
      if (path === '/api/v1/analysis?asOf=2026-04-10') return slow.promise
      if (path === '/api/v1/analysis?asOf=2026-04-20') return fast.promise
      return Promise.reject(new Error(path))
    })
    const user = userEvent.setup()
    render(<ResultsDashboard request={request} revision={0} />)
    await screen.findByRole('heading', { name: 'Bread' })
    const input = screen.getByLabelText('Analysis date')
    await user.clear(input)
    await user.type(input, '2026-04-10')
    await user.clear(input)
    await user.type(input, '2026-04-20')
    fast.resolve({ ...baseAnalysis, asOf: '2026-04-20' })
    expect(await screen.findByText('As of 2026-04-20.')).toBeInTheDocument()
    slow.resolve({ ...baseAnalysis, asOf: '2026-04-10' })
    await new Promise(resolve => setTimeout(resolve, 0))
    expect(screen.getByText('As of 2026-04-20.')).toBeInTheDocument()
    expect(screen.queryByText('As of 2026-04-10.')).not.toBeInTheDocument()
  })
})

describe('Results dashboard guidance', () => {
  it('posts the effective analysis date and shows message, source and stock notice', async () => {
    const request = router()
    const user = userEvent.setup()
    render(<ResultsDashboard request={request} revision={0} />)
    await user.click(await guidanceButton())
    expect(await screen.findByText('Bread: plan 28 units.')).toBeInTheDocument()
    expect(request).toHaveBeenCalledWith('/api/v1/guidance', { method: 'POST', body: JSON.stringify({ asOf: '2026-04-30' }) })
    expect(screen.getByText(/AI-worded and checked against backend figures/)).toBeInTheDocument()
    const notices = screen.getAllByText('Check your stock:')
    expect(notices).toHaveLength(2)  // With the estimates and with the advice (M3 9.7).
    expect(notices[1].parentElement).toHaveTextContent('Check what you already have in stock before you buy.')
    expect(screen.getByText(/Bodega does not record current stock/)).toBeInTheDocument()
  })

  it('labels mock wording so it cannot be mistaken for a real model', async () => {
    const user = userEvent.setup()
    render(<ResultsDashboard request={router({ guidance: guidanceResult({ provider: 'mock' }) })} revision={0} />)
    await user.click(await guidanceButton())
    expect(await screen.findByText(/mock provider \(demo text, not a real AI model\)/)).toBeInTheDocument()
  })

  it('labels the template fallback and shows the safe reason', async () => {
    const guidance = guidanceResult({ source: 'TEMPLATE', groundingPassed: false, failedRule: 'UNSUPPORTED_NUMBER', failureReason: 'The provider used a number the analysis does not support, so its wording was rejected.' })
    const user = userEvent.setup()
    render(<ResultsDashboard request={router({ guidance })} revision={0} />)
    await user.click(await guidanceButton())
    expect((await screen.findByText('Source:')).parentElement).toHaveTextContent('Source: Standard advice') // M3 9.7 label.
    expect(screen.getByText(/does not support, so its wording was rejected/)).toBeInTheDocument()
  })

  it('renders provider text as plain text, never as HTML', async () => {
    const markup = '<img src=x onerror="alert(1)"> Bread'
    const guidance = guidanceResult({ items: [{ ...guidanceResult().items[0], message: markup }] })
    const user = userEvent.setup()
    const { container } = render(<ResultsDashboard request={router({ guidance })} revision={0} />)
    await user.click(await guidanceButton())
    expect(await screen.findByText(markup)).toBeInTheDocument()
    expect(container.querySelector('img')).toBeNull()
  })

  it('shows loading then a retryable error', async () => {
    const pending = deferred()
    const request = vi.fn(path => (path === '/api/v1/guidance' ? pending.promise : Promise.resolve(baseAnalysis)))
    const user = userEvent.setup()
    render(<ResultsDashboard request={request} revision={0} />)
    await user.click(await guidanceButton())
    expect(screen.getByRole('status')).toHaveTextContent('Getting advice')
    expect(screen.getByRole('button', { name: 'Getting advice…' })).toBeDisabled()
    pending.reject(new Error('Unauthorized'))
    expect(await screen.findByRole('alert')).toHaveTextContent('Unauthorized')
    expect(await guidanceButton()).toBeEnabled()
  })

  it('clears guidance when the revision changes', async () => {
    const request = router()
    const user = userEvent.setup()
    const { rerender } = render(<ResultsDashboard request={request} revision={0} />)
    await user.click(await guidanceButton())
    await screen.findByText('Bread: plan 28 units.')
    rerender(<ResultsDashboard request={request} revision={1} />)
    expect(screen.queryByText('Bread: plan 28 units.')).not.toBeInTheDocument()
    expect(await screen.findByText(/Earlier guidance was cleared/)).toBeInTheDocument()
  })

  it('clears guidance when the analysis date changes and posts the new date', async () => {
    const request = vi.fn((path, options) => {
      if (path === '/api/v1/guidance') {
        return Promise.resolve(guidanceResult({ asOf: JSON.parse(options.body).asOf, items: [{ ...guidanceResult().items[0], message: `Bread for ${JSON.parse(options.body).asOf}` }] }))
      }
      return Promise.resolve({ ...baseAnalysis, asOf: path.includes('asOf=') ? '2026-04-20' : '2026-04-30' })
    })
    const user = userEvent.setup()
    render(<ResultsDashboard request={request} revision={0} />)
    await user.click(await guidanceButton())
    await screen.findByText('Bread for 2026-04-30')
    const input = screen.getByLabelText('Analysis date')
    await user.type(input, '2026-04-20')
    expect(await screen.findByText('As of 2026-04-20.')).toBeInTheDocument()
    expect(screen.queryByText('Bread for 2026-04-30')).not.toBeInTheDocument()
    await user.click(await guidanceButton())
    expect(await screen.findByText('Bread for 2026-04-20')).toBeInTheDocument()
  })

  it('never shows a late guidance response after the analysis changed', async () => {
    const pending = deferred()
    const request = vi.fn(path => (path === '/api/v1/guidance' ? pending.promise : Promise.resolve(baseAnalysis)))
    const user = userEvent.setup()
    const { rerender } = render(<ResultsDashboard request={request} revision={0} />)
    await user.click(await guidanceButton())
    rerender(<ResultsDashboard request={request} revision={1} />)
    await screen.findByRole('button', { name: 'Get restocking advice' })
    pending.resolve(guidanceResult({ items: [{ ...guidanceResult().items[0], message: 'Stale advice' }] }))
    await new Promise(resolve => setTimeout(resolve, 0))
    expect(screen.queryByText('Stale advice')).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Get restocking advice' })).toBeEnabled()
  })

  it('ignores an older guidance response after a newer request', async () => {
    const first = deferred()
    const second = deferred()
    const pendingGuidance = [first, second]
    const request = vi.fn(path => (path === '/api/v1/guidance' ? pendingGuidance.shift().promise : Promise.resolve(baseAnalysis)))
    const user = userEvent.setup()
    const { rerender } = render(<ResultsDashboard request={request} revision={0} />)
    await user.click(await guidanceButton())
    rerender(<ResultsDashboard request={request} revision={1} />)
    await user.click(await guidanceButton())
    second.resolve(guidanceResult({ items: [{ ...guidanceResult().items[0], message: 'Newest advice' }] }))
    expect(await screen.findByText('Newest advice')).toBeInTheDocument()
    first.resolve(guidanceResult({ items: [{ ...guidanceResult().items[0], message: 'Oldest advice' }] }))
    await new Promise(resolve => setTimeout(resolve, 0))
    expect(screen.getByText('Newest advice')).toBeInTheDocument()
    expect(screen.queryByText('Oldest advice')).not.toBeInTheDocument()
  })
})

describe('Chart value conversion', () => {
  it('rejects values a float cannot represent', () => {
    expect(chartNumber('2.5')).toBe(2.5)
    expect(chartNumber('0')).toBe(0)
    expect(chartNumber('0.000')).toBe(0)
    expect(chartNumber('1' + '0'.repeat(400))).toBeNull()
    expect(chartNumber('1e-400')).toBeNull()
    expect(chartNumber('0.' + '0'.repeat(400) + '1')).toBeNull()
  })

  it('chooses round axis maxima and reports overflow', () => {
    expect(axisMaximum(0)).toBe(1)
    expect(axisMaximum(4)).toBe(5)
    expect(axisMaximum(28)).toBe(50)
    expect(axisMaximum(100)).toBe(100)
    expect(axisMaximum(1.7e308)).toBeNull()
  })
})

describe('Results dashboard advice length (NFR-734)', () => {
  it('says when advice covers only some items', async () => {
    const user = userEvent.setup()
    render(<ResultsDashboard request={router({ guidance: guidanceResult({ omittedItems: 3 }) })} revision={0} />)
    await user.click(await guidanceButton())
    expect(await screen.findByText(/Advice covers the 1 items with the highest suggested stock/)).toBeInTheDocument()
  })
})
