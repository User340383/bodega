import { useEffect, useMemo, useRef, useState } from 'react'
import {
  FALLBACK_TEXT, INPUT_NOTICE, LOADING_TEXT, MAX_TEXT_CHARS, REQUEST_TIMEOUT_MS, fileProblem, localToday, textProblem,
} from './captureRules'
import './SalesCapture.css'

// José: capture screen (M3 4.3 /capture). Photo or typed text -> POST /api/v1/extract -> draft for review.
export default function SalesCapture({ request, onDraft, today = localToday }) {
  const [mode, setMode] = useState('photo')
  const [saleDate, setSaleDate] = useState(() => today())
  const [file, setFile] = useState(null)
  const [text, setText] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [outcome, setOutcome] = useState(null)
  const fileInput = useRef(null)

  // Local preview of the chosen photo; the object URL is released when the photo changes.
  const preview = useMemo(() => (file && typeof URL.createObjectURL === 'function' ? URL.createObjectURL(file) : ''), [file])
  useEffect(() => () => { if (preview) URL.revokeObjectURL(preview) }, [preview])

  const maxDate = today()
  const dateProblem = !saleDate ? 'Choose the sale date.' : saleDate > maxDate ? 'Sale date cannot be in the future.' : ''
  const inputProblem = mode === 'photo' ? fileProblem(file) : textProblem(text)
  const problem = dateProblem || inputProblem

  function reset() {
    setFile(null)
    setText('')
    setOutcome(null)
    setError('')
    if (fileInput.current) fileInput.current.value = ''
  }

  async function submit(event) {
    event.preventDefault()
    if (busy || problem) return
    setBusy(true)
    setError('')
    setOutcome(null)
    const body = new FormData()
    body.append('saleDate', saleDate)
    if (mode === 'photo') body.append('file', file)
    else body.append('text', text.trim())
    const controller = new AbortController()
    const timer = setTimeout(() => controller.abort(), REQUEST_TIMEOUT_MS)
    try {
      const draft = await request('/api/v1/extract', { method: 'POST', body, signal: controller.signal })
      setOutcome(draft)
      onDraft(draft)
    } catch (err) {
      setError(err.name === 'AbortError'
        ? 'This is taking too long. Check your connection and try again.'
        : err.message)
    } finally {
      clearTimeout(timer)
      setBusy(false)
    }
  }

  return <section aria-labelledby="capture-heading" className="capture">
    <h2 id="capture-heading">Capture sales</h2>
    <div role="tablist" aria-label="How to capture sales" className="capture-tabs">
      {[['photo', 'Photo'], ['text', 'Text']].map(([value, label]) => <button key={value} type="button" role="tab"
        aria-selected={mode === value} className={mode === value ? 'active' : ''} disabled={busy}
        onClick={() => { setMode(value); setError(''); setOutcome(null) }}>{label}</button>)}
    </div>
    <form onSubmit={submit}>
      <fieldset disabled={busy}>
        <label>Sale date <input type="date" required value={saleDate} max={maxDate}
          onChange={event => setSaleDate(event.target.value)} /></label>
        {mode === 'photo' ? <div className="capture-input">
          <label>Photo of your sales page <input ref={fileInput} type="file" accept="image/jpeg,image/png"
            capture="environment" onChange={event => setFile(event.target.files?.[0] ?? null)} /></label>
          {preview && <img className="capture-preview" src={preview} alt="Selected sales page" />}
        </div> : <div className="capture-input">
          <label htmlFor="sales-text">Sales text</label>
          <textarea id="sales-text" rows={8} value={text} placeholder={'Bread 3 1.50\nMilk 2 2.25'}
            onChange={event => setText(event.target.value)} aria-describedby="sales-text-count" />
          <p id="sales-text-count" className="hint">{text.trim().length.toLocaleString('en-US')} / {MAX_TEXT_CHARS.toLocaleString('en-US')} characters. One item per line: name, quantity, unit price.</p>
        </div>}
        {(file || text) && problem && <p className="field-error">{problem}</p>}
        {!(file || text) && dateProblem && <p className="field-error">{dateProblem}</p>}
        <p className="notice" id="input-notice">{INPUT_NOTICE}</p>
        <button type="submit" disabled={busy || !!problem} aria-describedby="input-notice">
          {busy ? 'Reading…' : mode === 'photo' ? 'Read photo' : 'Read text'}</button>
      </fieldset>
    </form>
    {busy && <p role="status" className="capture-loading"><span className="spinner" aria-hidden="true" />{LOADING_TEXT}</p>}
    {error && <p role="alert">{error} Nothing was saved; you can try again.</p>}
    {outcome && <div role="status" className={`capture-outcome ${outcome.status === 'NEEDS_VERIFICATION' ? 'ok' : 'fallback'}`}>
      {outcome.status === 'NEEDS_VERIFICATION'
        ? <p>Draft ready. Check the highlighted fields below, then confirm.</p>
        : <p>{outcome.message || FALLBACK_TEXT[outcome.status]} {outcome.status === 'AI_UNAVAILABLE' && 'Use “Add row” in the review below.'}</p>}
      {outcome.message && outcome.status === 'NEEDS_VERIFICATION' && <p>{outcome.message}</p>}
      {outcome.status === 'REJECTED' && <button type="button" onClick={reset}>Retake photo</button>}
      {outcome.maskedText && <details><summary>Text sent to the AI (contacts hidden)</summary>
        <pre className="masked-text">{outcome.maskedText}</pre></details>}
      {outcome.imageUrl && <img className="capture-preview" src={outcome.imageUrl} alt="Stored sales page (location data removed)" />}
    </div>}
  </section>
}
