import { memo, useEffect, useId, useRef, useState } from 'react'
import SalesTrendChart from './SalesTrendChart'
import './ResultsDashboard.css'

const TRENDS = {
  UP: { icon: '▲', label: 'UP', className: 'trend-up' },
  DOWN: { icon: '▼', label: 'DOWN', className: 'trend-down' },
  STABLE: { icon: '▬', label: 'STABLE', className: 'trend-stable' },
  INSUFFICIENT_DATA: { icon: '?', label: 'INSUFFICIENT DATA', className: 'trend-unknown' },
}

const NO_ESTIMATE = new Set(['NO_DATA', 'INSUFFICIENT_HISTORY'])
// M3 9.7 fixed notices (exact text). The API repeats the stock notice with every advice response.
export const STOCK_NOTICE = 'These numbers come only from the sales you logged. Check what you already have in stock before you buy.'
export const TEMPLATE_LABEL = 'Standard advice'

function TrendBadge({ trend }) {
  const { icon, label, className } = TRENDS[trend]
  return <span className={`trend-badge ${className}`}><span aria-hidden="true">{icon}</span> {label}</span>
}

function average(text, rounded) {
  if (text === null) return '—'
  return `${rounded ? '≈ ' : ''}${text}`
}

function changeText(estimate) {
  if (estimate.trend === 'INSUFFICIENT_DATA') return 'Needs 14 logged dates to compare.'
  if (estimate.changePercent === null) {
    return Number(estimate.currentAverage) === 0 ? 'No sales in either period.' : 'No sales in the previous 7 logged dates.'
  }
  const sign = Number(estimate.changePercent) <= 0 ? '' : '+'
  return `${sign}${estimate.changePercent}% against the previous 7 logged dates.`
}

// Memoized: requesting advice must not re-render every item card and chart (NFR-705: the progress
// indicator appears within 1 s even with 100 items on a slow phone CPU).
const ItemCard = memo(function ItemCard({ estimate }) {
  // Item keys can contain spaces, so they cannot serve as IDs for aria-labelledby.
  const headingId = useId()
  return <article className="item-card" aria-labelledby={headingId}>
    <div className="item-heading">
      <h4 id={headingId}>{estimate.itemName}</h4>
      <TrendBadge trend={estimate.trend} />
    </div>
    {estimate.suggestedQty !== null && <>
      <dl className="item-figures">
        {/* M3 section 3 labels (FR-406, FR-402); the average is shown to 1 decimal (M3 4.3). */}
        <div><dt>Suggested stock for your next 7 selling days</dt><dd>{estimate.suggestedQty}</dd></div>
        <div><dt>Average sold per logged day (last 7 logged days)</dt><dd>{estimate.currentAverageDisplay}</dd></div>
        <div><dt>Previous 7 logged days average</dt><dd>{average(estimate.previousAverage, estimate.previousAverageRounded)}</dd></div>
      </dl>
      <p className="item-note">{changeText(estimate)} Sold on {estimate.currentDaysWithSales} of the latest {estimate.windowLoggedDays} logged dates.
        {estimate.currentAverageRounded && ` Exact average: ${average(estimate.currentAverage, true)}.`}</p>
    </>}
    <SalesTrendChart itemName={estimate.itemName} series={estimate.series} />
  </article>
})

function sourceLabel(result) {
  if (result.source === 'TEMPLATE') return result.sourceLabel || TEMPLATE_LABEL
  if (result.provider === 'mock') return 'AI-worded by the mock provider (demo text, not a real AI model)'
  return 'AI-worded and checked against backend figures'
}

function GuidanceResult({ result }) {
  return <div className="guidance-result">
    {result.items.length > 0 && <p className="guidance-source"><strong>Source:</strong> {sourceLabel(result)}</p>}
    {result.failureReason && <p className="guidance-reason">{result.failureReason}</p>}
    {result.omittedItems > 0 && <p className="guidance-reason">Advice covers the {result.items.length} items with the highest suggested stock, to keep it short. The numbers for all items are shown above.</p>}
    {result.items.length > 0 && <ul className="guidance-items">
      {result.items.map(item => <li key={item.itemName}>{item.message}</li>)}
    </ul>}
    <p className="stock-notice"><strong>Check your stock:</strong> {result.stockNotice}</p>
  </div>
}

export default function ResultsDashboard({ request, revision }) {
  const [asOf, setAsOf] = useState('')
  const [reload, setReload] = useState(0)
  const selection = `${revision}|${asOf}|${reload}`
  const [loaded, setLoaded] = useState({ selection: null, analysis: null, error: '' })
  const [guidance, setGuidance] = useState({ selection: null, loading: false, result: null, error: '' })
  const guidanceAttempt = useRef(0)

  useEffect(() => {
    let active = true
    const query = asOf ? `?asOf=${encodeURIComponent(asOf)}` : ''
    request(`/api/v1/analysis${query}`).then(analysis => {
      if (active) setLoaded({ selection, analysis, error: '' })
    }).catch(error => {
      if (active) setLoaded({ selection, analysis: null, error: error.message })
    })
    return () => { active = false }
  }, [request, selection, asOf])

  const loading = loaded.selection !== selection
  const { analysis, error } = loaded
  const currentGuidance = guidance.selection === selection ? guidance : null
  // Guidance belongs to one analysis: any change of data or date retires it automatically.
  const guidanceRetired = guidance.selection !== null && guidance.selection !== selection && Boolean(guidance.result)

  async function requestGuidance() {
    const attempt = ++guidanceAttempt.current
    const owner = selection
    setGuidance({ selection: owner, loading: true, result: null, error: '' })
    try {
      const result = await request('/api/v1/guidance', { method: 'POST', body: JSON.stringify({ asOf: analysis.asOf }) })
      if (attempt === guidanceAttempt.current) setGuidance({ selection: owner, loading: false, result, error: '' })
    } catch (failure) {
      if (attempt === guidanceAttempt.current) setGuidance({ selection: owner, loading: false, result: null, error: failure.message })
    }
  }

  return <section aria-labelledby="results-heading" className="results-dashboard">
    <h2 id="results-heading">Results dashboard</h2>
    <div className="results-controls">
      <label>Analysis date <input type="date" value={asOf} onChange={event => setAsOf(event.target.value)} /></label>
      {asOf && <button type="button" className="secondary" onClick={() => setAsOf('')}>Use today</button>}
      <button type="button" onClick={() => setReload(reload + 1)}>Refresh analysis</button>
    </div>

    {loading && <p role="status">Loading analysis…</p>}
    {!loading && error && <p role="alert">{error}</p>}

    {analysis && <div className={loading ? 'results-body is-stale' : 'results-body'} aria-busy={loading}>
      <p className="results-scope">
        <strong>As of {analysis.asOf}.</strong> Sales history from {analysis.historyStart} to {analysis.historyEnd}: {analysis.loggedDays} logged {analysis.loggedDays === 1 ? 'date' : 'dates'}.
      </p>
      <p className={`results-status status-${analysis.status.toLowerCase()}`}>{analysis.statusMessage}</p>

      {!NO_ESTIMATE.has(analysis.status) && <p className="results-basis">{analysis.quantityBasis}</p>}
      {analysis.currentWindow && <p className="results-basis">
        Latest 7 logged dates: {analysis.currentWindow.start} to {analysis.currentWindow.end}.
        {analysis.previousWindow && ` Previous 7 logged dates: ${analysis.previousWindow.start} to ${analysis.previousWindow.end}.`}
      </p>}

      {analysis.status === 'NO_DATA' && <p className="results-empty">No sales are saved for these dates yet, so nothing is estimated.{' '}
        <a className="button-link" href="/capture">Capture sales</a></p>}
      {analysis.status === 'INSUFFICIENT_HISTORY' && <p className="results-empty">
        Log at least {analysis.requiredDays} days of sales to see estimates ({analysis.loggedDays} of {analysis.requiredDays} so far).
        The charts below show confirmed sales only.</p>}
      {!NO_ESTIMATE.has(analysis.status) && <p className="stock-notice"><strong>Check your stock:</strong> {STOCK_NOTICE}</p>}

      {analysis.estimates.length > 0 && <>
        <h3>Items</h3>
        <div className="item-grid">{analysis.estimates.map(estimate => <ItemCard key={estimate.itemKey} estimate={estimate} />)}</div>
      </>}

      <div className="guidance-panel">
        <h3>Restocking advice</h3>
        <p className="guidance-intro">Planning suggestions based on confirmed sales. Bodega does not record current stock, so these are not low-stock alerts.</p>
        <button type="button" onClick={requestGuidance} disabled={loading || NO_ESTIMATE.has(analysis.status) || Boolean(currentGuidance?.loading)}>
          {currentGuidance?.loading ? 'Getting advice…' : 'Get restocking advice'}
        </button>
        {NO_ESTIMATE.has(analysis.status) && <p className="guidance-reason">Guidance becomes available after {analysis.requiredDays} logged sales dates.</p>}
        {currentGuidance?.loading && <p role="status">Getting advice… this can take up to 15 seconds.</p>}
        {currentGuidance?.error && <p role="alert">{currentGuidance.error}</p>}
        {currentGuidance?.result && <GuidanceResult result={currentGuidance.result} />}
        {guidanceRetired && <p className="guidance-reason">Earlier guidance was cleared because the analysis changed. Request it again for the current results.</p>}
      </div>
    </div>}
  </section>
}
