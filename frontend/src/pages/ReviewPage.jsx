import { useEffect, useState } from 'react'
import { Link, useLocation, useParams } from 'react-router'
import { useAuth } from '../auth/useAuth'
import { rememberDraft } from '../auth/session'
import DraftReview from '../components/DraftReview'
import { FALLBACK_TEXT } from '../components/captureRules'

// The draft table sits beside the photo (signed URL) or the masked text (M3 4.3 /review/:submissionId).
function SourcePanel({ draft }) {
  if (draft.imageUrl) return <section className="source-panel" aria-labelledby="source-heading">
    <h2 id="source-heading">Your photo</h2>
    <img src={draft.imageUrl} alt="Sales page you captured" />
  </section>
  if (draft.maskedText) return <section className="source-panel" aria-labelledby="source-heading">
    <h2 id="source-heading">Your text</h2>
    <p>Phone numbers and emails were removed.</p>
    <pre>{draft.maskedText}</pre>
  </section>
  return null
}

export default function ReviewPage() {
  const { submissionId } = useParams()
  const location = useLocation()
  const { request } = useAuth()
  const handed = location.state?.draft
  const initial = handed && String(handed.submissionId) === submissionId ? handed : null
  const [state, setState] = useState({ draft: initial, loading: !initial, error: '' })
  const [confirmed, setConfirmed] = useState(false)

  useEffect(() => {
    if (initial) return undefined
    let active = true
    request(`/api/v1/submissions/${encodeURIComponent(submissionId)}`).then(draft => {
      if (!active) return
      rememberDraft(draft)
      setState({ draft, loading: false, error: '' })
    }).catch(error => { if (active) setState({ draft: null, loading: false, error: error.message }) })
    return () => { active = false }
  }, [initial, request, submissionId])

  if (state.loading) return <p role="status">Loading draft…</p>
  if (state.error) return <section>
    <h1>Review draft</h1>
    <p role="alert">{state.error}</p>
    <div className="link-row"><Link className="button-link" to="/drafts">Back to pending drafts</Link></div>
  </section>

  const { draft } = state
  const fallback = FALLBACK_TEXT[draft.status]
  return <>
    {fallback && <div className="status-note" role="status">
      <p>{draft.message || fallback}</p>
      {draft.status === 'REJECTED' && draft.inputType === 'IMAGE' &&
        <Link className="button-link" to="/capture">Retake the photo</Link>}
    </div>}
    <div className="review-layout">
      <SourcePanel draft={draft} />
      <DraftReview key={String(draft.submissionId)} draft={draft} request={request} onConfirmed={() => setConfirmed(true)} />
    </div>
    {confirmed && <nav aria-label="Next steps" className="link-row">
      <Link className="button-link" to="/capture">Capture more sales</Link>
      <Link className="button-link" to="/history">View history</Link>
      <Link className="button-link" to="/analysis">See analysis</Link>
    </nav>}
  </>
}
