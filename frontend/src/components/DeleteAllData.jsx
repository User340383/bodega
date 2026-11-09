import { useState } from 'react'

// FR-304 / SRS AC-21: delete every stored sales record and all raw input after the owner types
// DELETE. Canceling, or typing anything else, deletes nothing. Reports "N records deleted".
export default function DeleteAllData({ request, onDeleted }) {
  const [open, setOpen] = useState(false)
  const [typed, setTyped] = useState('')
  const [busy, setBusy] = useState(false)
  const [message, setMessage] = useState('')

  async function submit(event) {
    event.preventDefault()
    if (typed !== 'DELETE' || busy) return
    setBusy(true)
    try {
      const result = await request('/api/v1/data/delete-all', { method: 'POST', body: { confirmation: typed } })
      setMessage(result.message)
      setOpen(false)
      setTyped('')
      onDeleted?.(result)
    } catch (error) {
      setMessage(`${error.message}`)
    } finally {
      setBusy(false)
    }
  }

  return <section aria-labelledby="delete-all-heading" className="delete-all">
    <h2 id="delete-all-heading">Delete all my sales data</h2>
    <p>This removes every saved sale, every draft, and every stored photo or text. It cannot be undone.</p>
    {message && <p role="status" className="status-note">{message}</p>}
    {!open ? <button type="button" className="danger" onClick={() => { setOpen(true); setMessage('') }}>Delete all data</button> :
      <form className="confirm-delete" onSubmit={submit} aria-labelledby="delete-all-confirm">
        <h3 id="delete-all-confirm">Delete everything?</h3>
        <label>Type DELETE to confirm <input value={typed} autoComplete="off" onChange={event => setTyped(event.target.value)} /></label>
        <div className="link-row">
          <button type="submit" className="danger" disabled={typed !== 'DELETE' || busy}>{busy ? 'Deleting…' : 'Delete all data'}</button>
          <button type="button" className="secondary" disabled={busy}
            onClick={() => { setOpen(false); setTyped(''); setMessage('Nothing was deleted.') }}>Cancel</button>
        </div>
      </form>}
  </section>
}
