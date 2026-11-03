import { useEffect, useState } from 'react'

export default function SalesHistory({ request, revision }) {
  const [page, setPage] = useState(0)
  const [startDate, setStartDate] = useState('')
  const [endDate, setEndDate] = useState('')
  const [appliedDates, setAppliedDates] = useState({ startDate: '', endDate: '' })
  const [state, setState] = useState({ records: [], loading: true, error: '' })
  const [retry, setRetry] = useState(0)
  const invalidRange = Boolean(startDate && endDate && startDate > endDate)
  useEffect(() => {
    let active = true
    const params = new URLSearchParams({ limit: '100', offset: String(page * 100) })
    if (appliedDates.startDate) params.set('startDate', appliedDates.startDate)
    if (appliedDates.endDate) params.set('endDate', appliedDates.endDate)
    request(`/api/v1/sales?${params}`).then(result => {
      if (active) setState({ records: result.records, loading: false, error: '' })
    }).catch(error => {
      if (active) setState({ records: [], loading: false, error: error.message })
    })
    return () => { active = false }
  }, [request, revision, page, retry, appliedDates])
  return <section aria-labelledby="history-heading">
    <h2 id="history-heading">Confirmed sales history</h2>
    <form onSubmit={event => {
      event.preventDefault()
      if (invalidRange) return
      setState({ records: [], loading: true, error: '' })
      setPage(0)
      setAppliedDates({ startDate, endDate })
    }}>
      <label>From date <input type="date" value={startDate} onChange={event => setStartDate(event.target.value)} /></label>
      <label>To date <input type="date" value={endDate} onChange={event => setEndDate(event.target.value)} /></label>
      {invalidRange && <p role="alert">End date must be on or after start date.</p>}
      <button type="submit" disabled={invalidRange}>Filter dates</button>
    </form>
    <button onClick={() => { setState({ records: [], loading: true, error: '' }); setRetry(retry + 1) }}>Refresh history</button>
    {state.loading ? <p role="status">Loading sales…</p> : state.error ? <p role="alert">{state.error}</p> : <>
      {!state.records.length ? <p>No confirmed sales on this page.</p> : <div className="table-scroll"><table>
        <thead><tr><th>Sales date</th><th>Item</th><th>Quantity</th><th>Unit price</th><th>Source</th></tr></thead>
        <tbody>{state.records.map(record => <tr key={record.id}>
          <td>{record.saleDate}</td><td>{record.itemName}</td><td>{record.quantity}</td><td>{record.unitPrice}</td><td>{record.source}</td>
        </tr>)}</tbody>
      </table></div>}
      <div className="pagination">
        <button disabled={page === 0} onClick={() => { setState({ records: [], loading: true, error: '' }); setPage(page - 1) }}>Previous</button>
        <span>Page {page + 1}</span>
        <button disabled={state.records.length < 100} onClick={() => { setState({ records: [], loading: true, error: '' }); setPage(page + 1) }}>Next</button>
      </div>
    </>}
  </section>
}
