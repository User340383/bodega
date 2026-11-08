import { useEffect, useState } from 'react'

const SOURCE_LABELS = { AI_EXTRACTED: 'Read by AI, checked by you', MANUAL: 'Typed by you' }

// Gabriel: confirmed sales ledger (FR-303, M3 4.3 /history). GET /api/v1/sales returns the last
// 28 days by default; the owner can choose any range of up to 366 days. Deleting a submission
// (FR-304, M3 7.4) needs the word DELETE typed into an in-page confirmation.
export default function SalesHistory({ request, revision = 0 }) {
  const [range, setRange] = useState({ start: '', end: '' })
  const [state, setState] = useState({ data: null, loading: true, error: '' })
  const [reload, setReload] = useState(0)
  const [pending, setPending] = useState(null)
  const [typed, setTyped] = useState('')
  const [deleting, setDeleting] = useState(false)
  const [notice, setNotice] = useState('')

  useEffect(() => {
    let active = true
    const params = new URLSearchParams()
    if (range.start) params.set('start', range.start)
    if (range.end) params.set('end', range.end)
    const query = params.toString()
    request(`/api/v1/sales${query ? `?${query}` : ''}`).then(data => {
      if (active) setState({ data, loading: false, error: '' })
    }).catch(error => {
      if (active) setState({ data: null, loading: false, error: error.message })
    })
    return () => { active = false }
  }, [request, revision, range, reload])

  function refresh() {
    setState(current => ({ ...current, loading: true, error: '' }))
    setReload(value => value + 1)
  }

  function changeRange(field, value) {
    setState(current => ({ ...current, loading: true }))
    setRange(current => ({ ...current, [field]: value }))
  }

  function askDelete(record) {
    const rows = state.data.records.filter(row => row.submissionId === record.submissionId).length
    setPending({ submissionId: record.submissionId, saleDate: record.saleDate, rows })
    setTyped('')
    setNotice('')
  }

  async function confirmDelete(event) {
    event.preventDefault()
    if (typed !== 'DELETE' || deleting) return
    setDeleting(true)
    try {
      await request(`/api/v1/submissions/${encodeURIComponent(pending.submissionId)}`, { method: 'DELETE' })
      setNotice(`Deleted ${pending.rows} ${pending.rows === 1 ? 'record' : 'records'} from ${pending.saleDate}.`)
      setPending(null)
      refresh()
    } catch (error) {
      setNotice(`${error.message} Nothing was deleted.`)
    } finally {
      setDeleting(false)
    }
  }

  const records = state.data?.records || []
  return <section aria-labelledby="history-heading">
    <h2 id="history-heading">Confirmed sales history</h2>
    <div className="history-controls">
      <label>From <input type="date" value={range.start} max={range.end || undefined}
        onChange={event => changeRange('start', event.target.value)} /></label>
      <label>To <input type="date" value={range.end} min={range.start || undefined}
        onChange={event => changeRange('end', event.target.value)} /></label>
      <button type="button" onClick={refresh}>Refresh history</button>
    </div>
    {state.data && <p className="hint">Showing {state.data.start} to {state.data.end}.</p>}
    {notice && <p role="status" className="status-note">{notice}</p>}

    {pending && <form className="confirm-delete" role="group" aria-labelledby="delete-heading" onSubmit={confirmDelete}>
      <h3 id="delete-heading">Delete sales from {pending.saleDate}?</h3>
      <p>This deletes {pending.rows} {pending.rows === 1 ? 'record' : 'records'} and the saved photo or text for that
        submission. It cannot be undone. Type DELETE to confirm.</p>
      <label>Type DELETE <input value={typed} autoComplete="off" onChange={event => setTyped(event.target.value)} /></label>
      <div className="link-row">
        <button type="submit" className="danger" disabled={typed !== 'DELETE' || deleting}>{deleting ? 'Deleting…' : 'Delete'}</button>
        <button type="button" className="secondary" disabled={deleting}
          onClick={() => { setPending(null); setNotice('Nothing was deleted.') }}>Cancel</button>
      </div>
    </form>}

    {state.loading ? <p role="status">Loading sales…</p> : state.error ? <p role="alert">{state.error}</p> :
      !records.length ? <p>No confirmed sales in these dates. <a href="/capture">Capture sales</a> to add some.</p> :
      <div className="table-scroll"><table>
        <thead><tr><th scope="col">Sales date</th><th scope="col">Item</th><th scope="col">Quantity</th>
          <th scope="col">Unit price</th><th scope="col">Line total</th><th scope="col">Source</th><th scope="col">Actions</th></tr></thead>
        <tbody>{records.map(record => <tr key={record.recordId}>
          <td>{record.saleDate}</td><td>{record.itemName}</td><td>{record.quantity}</td><td>{record.unitPrice}</td>
          <td>{record.lineTotal}</td><td>{SOURCE_LABELS[record.source] || record.source}</td>
          <td>{record.lineNo === 1 && <button type="button" className="secondary" onClick={() => askDelete(record)}
            aria-label={`Delete submission from ${record.saleDate} starting with ${record.itemName}`}>Delete submission</button>}</td>
        </tr>)}</tbody>
      </table></div>}
  </section>
}
