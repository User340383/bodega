import { useState } from 'react'
import { Link, useNavigate } from 'react-router'
import { readRecentDrafts } from '../auth/session'

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i
const STATUS_LABELS = {
  NEEDS_VERIFICATION: 'Needs checking', AI_UNAVAILABLE: 'Type the sales', REJECTED: 'Could not read', CONFIRMED: 'Saved',
}
const INPUT_LABELS = { IMAGE: 'Photo', TEXT: 'Text', MANUAL: 'Manual' }

// Reopen an unconfirmed draft. Drafts captured in this tab are listed here; any other one opens by its ID.
// When GET /api/v1/submissions (FR-216) exists, this list should come from it instead.
export default function DraftsPage() {
  const navigate = useNavigate()
  const [recent] = useState(readRecentDrafts)
  const [submissionId, setSubmissionId] = useState('')
  const [error, setError] = useState('')

  function open(event) {
    event.preventDefault()
    const id = submissionId.trim()
    if (!UUID.test(id)) { setError('Enter a submission ID like 10000000-0000-4000-8000-000000000003.'); return }
    navigate(`/review/${id}`)
  }

  return <>
    <section aria-labelledby="recent-heading">
      <h1 id="recent-heading">Pending drafts</h1>
      {recent.length ? <ul className="recent-list">
        {recent.map(item => <li key={item.submissionId}>
          <span>{item.saleDate || 'Unknown date'} · {INPUT_LABELS[item.inputType] || 'Capture'} · {STATUS_LABELS[item.status] || item.status}</span>
          <Link className="button-link" to={`/review/${item.submissionId}`}>Open</Link>
        </li>)}
      </ul> : <p>No drafts captured in this browser tab yet. <Link to="/capture">Capture sales</Link>.</p>}
    </section>
    <section aria-labelledby="open-heading">
      <h2 id="open-heading">Open a submission by ID</h2>
      <form onSubmit={open}>
        <label>Submission ID <input value={submissionId} autoComplete="off"
          onChange={event => { setSubmissionId(event.target.value); setError('') }} /></label>
        <button>Open draft</button>
      </form>
      {error && <p role="alert">{error}</p>}
    </section>
  </>
}
