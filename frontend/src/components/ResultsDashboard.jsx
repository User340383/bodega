import { useEffect, useId, useRef, useState } from 'react'
import SalesTrendChart from './SalesTrendChart'
import './ResultsDashboard.css'

const TRENDS = {
  UP: { icon: '▲', label: 'UP', className: 'trend-up' },
  DOWN: { icon: '▼', label: 'DOWN', className: 'trend-down' },
  STABLE: { icon: '▬', label: 'STABLE', className: 'trend-stable' },
  INSUFFICIENT_DATA: { icon: '?', label: 'INSUFFICIENT DATA', className: 'trend-unknown' },
}

const NO_ESTIMATE = new Set(['NO_DATA', 'INSUFFICIENT_HISTORY'])

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
    return estimate.currentAverage === '0' ? 'No sales in either period.' : 'No sales in the previous 7 logged dates.'
  }
  const sign = estimate.changePercent.startsWith('-') || estimate.changePercent === '0' ? '' : '+'
  return `${sign}${estimate.changePercent}% against the previous 7 logged dates.`
}

function ItemCard({ estimate }) {
  // Item keys can contain spaces, so they cannot serve as IDs for aria-labelledby.
  const headingId = useId()
  return <article className="item-card" aria-labelledby={headingId}>
    <div className="item-heading">
      <h4 id={headingId}>{estimate.itemName}</h4>
      <TrendBadge trend={estimate.trend} />
    </div>
    {estimate.suggestedQty !== null && <>
      <dl className="item-figures">
        <div><dt>Planning quantity</dt><dd>{estimate.suggestedQty}</dd></div>
        <div><dt>Average per logged date</dt><dd>{average(estimate.currentAverage, estimate.currentAverageRounded)}</dd></div>
        <div><dt>Previous 7-date average</dt><dd>{average(estimate.previousAverage, estimate.previousAverageRounded)}</dd></div>
      </dl>
      <p className="item-note">{changeText(estimate)} Sold on {estimate.currentDaysWithSales} of the latest {estimate.windowLoggedDays} logged dates.</p>
    </>}
    <SalesTrendChart itemName={estimate.itemName} series={estimate.series} />
  </article>
}

function sourceLabel(result) {
  if (result.source === 'TEMPLATE') return 'Template wording from backend figures'
  if (result.provider === 'mock') return 'AI-worded by the mock provider (demo text, not a real AI model)'
  return 'AI-worded and checked against backend figures'
}

function GuidanceResult({ result }) {
  return <div className="guidance-result">
    {result.items.length > 0 && <p className="guidance-source"><strong>Source:</strong> {sourceLabel(result)}</p>}
    {result.failureReason && <p className="guidance-reason">{result.failureReason}</p>}
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

      {analysis.status === 'NO_DATA' && <p>Confirm sales to start building a history. Nothing is estimated until then.</p>}
      {analysis.status === 'INSUFFICIENT_HISTORY' && <p>No averages or planning quantities are shown yet. The charts below show confirmed sales only.</p>}

      {analysis.estimates.length > 0 && <>
        <h3>Items</h3>
        <div className="item-grid">{analysis.estimates.map(estimate => <ItemCard key={estimate.itemKey} estimate={estimate} />)}</div>
      </>}

      <div className="guidance-panel">
        <h3>Restocking guidance</h3>
        <p className="guidance-intro">Planning suggestions based on confirmed sales. Bodega does not record current stock, so these are not low-stock alerts.</p>
        <button type="button" onClick={requestGuidance} disabled={loading || NO_ESTIMATE.has(analysis.status) || Boolean(currentGuidance?.loading)}>
          {currentGuidance?.loading ? 'Getting guidance…' : 'Get restocking guidance'}
        </button>
        {NO_ESTIMATE.has(analysis.status) && <p className="guidance-reason">Guidance becomes available after {analysis.requiredDays} logged sales dates.</p>}
        {currentGuidance?.loading && <p role="status">Getting guidance…</p>}
        {currentGuidance?.error && <p role="alert">{currentGuidance.error}</p>}
        {currentGuidance?.result && <GuidanceResult result={currentGuidance.result} />}
        {guidanceRetired && <p className="guidance-reason">Earlier guidance was cleared because the analysis changed. Request it again for the current results.</p>}
      </div>
    </div>}
  </section>
}
