import { useEffect, useState } from 'react'
import { Link, useNavigate } from 'react-router'
import { useAuth } from '../auth/useAuth'
import { STATUS_LABELS } from '../components/draftRules'

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i
const INPUT_LABELS = { IMAGE: 'Photo', TEXT: 'Text', MANUAL: 'Manual' }

// Pending drafts (FR-216, M3 4.3 /drafts): every open submission from GET /api/v1/submissions,
// loaded from PostgreSQL so drafts reopen across sessions, with Open and Discard. Discarding
// deletes the draft and its saved photo or text.
export default function DraftsPage() {
  const { request } = useAuth()
  const navigate = useNavigate()
  const [state, setState] = useState({ drafts: [], loading: true, error: '' })
  const [reload, setReload] = useState(0)
  const [discarding, setDiscarding] = useState(null)
  const [notice, setNotice] = useState('')
  const [submissionId, setSubmissionId] = useState('')
  const [error, setError] = useState('')

  useEffect(() => {
    let active = true
    request('/api/v1/submissions').then(drafts => {
      if (active) setState({ drafts, loading: false, error: '' })
    }).catch(failure => { if (active) setState({ drafts: [], loading: false, error: failure.message }) })
    return () => { active = false }
  }, [request, reload])

  async function discard(draft) {
    setDiscarding(draft.submissionId)
    setNotice('')
    try {
      await request(`/api/v1/submissions/${encodeURIComponent(draft.submissionId)}`, { method: 'DELETE' })
      setNotice(`Discarded the draft from ${draft.saleDate}.`)
      setReload(value => value + 1)
    } catch (failure) {
      setNotice(`${failure.message} The draft was kept.`)
    } finally {
      setDiscarding(null)
    }
  }

  function open(event) {
    event.preventDefault()
    const id = submissionId.trim()
    if (!UUID.test(id)) { setError('Enter a submission ID like 10000000-0000-4000-8000-000000000003.'); return }
    navigate(`/review/${id}`)
  }

  return <>
    <section aria-labelledby="pending-heading">
      <h1 id="pending-heading">Pending drafts</h1>
      <p>Drafts are not part of your sales history until you confirm them. Unconfirmed drafts are deleted after 30 days.</p>
      {notice && <p role="status" className="status-note">{notice}</p>}
      {state.loading ? <p role="status">Loading drafts…</p> : state.error ? <p role="alert">{state.error}</p> :
        state.drafts.length ? <ul className="recent-list">
          {state.drafts.map(draft => <li key={draft.submissionId}>
            <span>{draft.saleDate} · {INPUT_LABELS[draft.inputType] || 'Capture'} · {STATUS_LABELS[draft.status] || draft.status}
              {draft.lineCount ? ` · ${draft.lineCount} ${draft.lineCount === 1 ? 'row' : 'rows'}` : ''}</span>
            <span className="link-row">
              <Link className="button-link" to={`/review/${draft.submissionId}`}>Open</Link>
              <button type="button" className="secondary" disabled={discarding === draft.submissionId}
                aria-label={`Discard draft from ${draft.saleDate}`} onClick={() => discard(draft)}>
                {discarding === draft.submissionId ? 'Discarding…' : 'Discard'}</button>
            </span>
          </li>)}
        </ul> : <><p>No pending drafts.</p><p><Link className="button-link" to="/capture">Capture sales</Link></p></>}
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
