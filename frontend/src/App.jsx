import { useCallback, useState } from 'react'
import DraftReview from './components/DraftReview'
import ResultsDashboard from './components/ResultsDashboard'
import SalesCapture from './components/SalesCapture'
import SalesHistory from './components/SalesHistory'
import './App.css'

// Minimal mount point until Luis supplies the shared shell/client.
function App() {
  const [code, setCode] = useState('')
  const [accessCode, setAccessCode] = useState('')
  const [submissionId, setSubmissionId] = useState('')
  const [draft, setDraft] = useState(null)
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(false)
  const [revision, setRevision] = useState(0)
  const [draftVersion, setDraftVersion] = useState(0)
  const request = useCallback(async (path, options = {}) => {
    // FormData (photo/text capture) must let the browser set its own multipart boundary.
    const json = !(options.body instanceof FormData)
    const response = await fetch(path, { ...options, headers: {
      Authorization: `Bearer ${accessCode}`, ...(json && { 'Content-Type': 'application/json' }), ...options.headers,
    } })
    const result = await response.json()
    if (!response.ok) {
      const detail = result.detail
      throw new Error(typeof detail === 'string' ? detail : Array.isArray(detail) ? detail.map(value => `${value.loc.join('.')}: ${value.msg}`).join('; ') : 'Request failed. Please try again.')
    }
    return result
  }, [accessCode])

  async function openDraft(event) {
    event.preventDefault()
    setError('')
    setLoading(true)
    try { setDraft(await request(`/api/v1/submissions/${encodeURIComponent(submissionId.trim())}`)) }
    catch (err) { setError(err.message) }
    finally { setLoading(false); setDraftVersion(value => value + 1) }
  }

  return <main>
    <header><h1>Bodega</h1><p>Capture, review and browse your sales.</p></header>
    <form onSubmit={event => { event.preventDefault(); setDraft(null); setAccessCode(code); setRevision(revision + 1) }}>
      <label>Owner access code <input type="password" required value={code} onChange={event => setCode(event.target.value)} /></label>
      <button>Connect</button>
    </form>
    {accessCode && <>
      <SalesCapture key={`capture:${accessCode}`} request={request} onDraft={captured => {
        setDraft(captured)
        setSubmissionId(String(captured.submissionId))
        setDraftVersion(value => value + 1)
      }} />
      <section><h2>Open a submission</h2>
        <p>Enter a submission ID to reopen a previously captured draft.</p>
        <form onSubmit={openDraft}><label>Submission ID <input required value={submissionId} onChange={event => setSubmissionId(event.target.value)} /></label>
          <button disabled={loading}>{loading ? 'Loading…' : 'Open draft'}</button>
        </form>
        {error && <p role="alert">{error}</p>}
      </section>
      {draft && <DraftReview key={`${draft.submissionId}:${draftVersion}`} draft={draft} request={request} onConfirmed={() => setRevision(revision + 1)} />}
      <ResultsDashboard key={accessCode} request={request} revision={revision} />
      <SalesHistory key={`${accessCode}:${revision}`} request={request} revision={revision} />
    </>}
  </main>
}
export default App
