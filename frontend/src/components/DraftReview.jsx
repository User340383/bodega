import { useState } from 'react'
import { FIELDS as fields, LABELS as labels, MAX_ITEM_CHARS, MAX_ROWS, STATUS_LABELS, fieldError, validLineId } from './draftRules'

// Every open state accepts the same confirm (M3 Diagram 4). For AI_UNAVAILABLE and REJECTED the
// table starts empty and the owner types the rows (M3 4.3 /review).
const EDITABLE = ['NEEDS_VERIFICATION', 'AI_UNAVAILABLE', 'REJECTED']

function reviewRows(draft) {
  return (draft.lines || []).map((line, index) => ({
    ...line, key: `draft-${index}`, extracted: true, verifiedFields: [],
    ...Object.fromEntries(fields.map(field => [field, line.bands?.[field] === 'LOW' ? '' : String(line[field] ?? '')])),
  }))
}

export default function DraftReview({ draft, request, onConfirmed }) {
  const [rows, setRows] = useState(() => reviewRows(draft))
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [result, setResult] = useState(null)
  const status = draft.status
  const editable = EDITABLE.includes(status)
  const extractedRows = rows.filter(row => row.extracted)
  const missingLink = extractedRows.some(row => !validLineId(row.lineId)) ||
    new Set(extractedRows.map(row => String(row.lineId))).size !== extractedRows.length
  const invalid = missingLink || rows.length === 0 || rows.length > MAX_ROWS || rows.some(row => fields.some(field =>
    fieldError(row, field) || (row.extracted && row.bands?.[field] !== 'HIGH' && !row.verifiedFields.includes(field)),
  ))

  function change(key, field, value) {
    setRows(rows.map(row => row.key === key ? {
      ...row, [field]: value, verifiedFields: [...new Set([...row.verifiedFields, field])],
    } : row))
  }

  async function confirm(event) {
    event.preventDefault()
    if (busy || invalid || result || !editable) return
    setBusy(true)
    setError('')
    try {
      const response = await request('/api/v1/confirm', {
        method: 'POST',
        body: JSON.stringify({ submissionId: draft.submissionId, lines: rows.map(row => ({
          draftLineId: row.extracted ? row.lineId : null, source: row.extracted ? 'AI_EXTRACTED' : 'MANUAL',
          itemName: row.itemName.trim(),
          // M3 4.2: JSON numbers. Exact here: values are validated to at most 2 decimal places.
          quantity: Number(row.quantity.trim()), unitPrice: Number(row.unitPrice.trim()), verifiedFields: row.verifiedFields,
        })) }),
      })
      setResult(response)
      onConfirmed?.(response)
    } catch (err) { setError(err.message) }
    finally { setBusy(false) }
  }

  return <section aria-labelledby="draft-heading">
    <h2 id="draft-heading">Review draft</h2>
    <p>Sales date: {draft.saleDate} · {STATUS_LABELS[status] || status}</p>
    {editable && <p>Check every row before confirming. Highlighted fields need a tick or a correction.</p>}
    {!editable && <p role="status">{status === 'CONFIRMED'
      ? 'These sales are already saved. You can see them in History.'
      : 'This sales log is still being read. Refresh in a moment.'}</p>}
    {missingLink && <p role="alert">This draft could not be loaded completely. Reopen it before confirming.</p>}
    <form onSubmit={confirm}>
      <fieldset disabled={busy || !!result || !editable}>
        <div className="table-scroll"><table>
          <thead><tr>{fields.map(field => <th key={field} scope="col">{labels[field]}</th>)}<th scope="col">Actions</th></tr></thead>
          <tbody>{rows.map((row, index) => <tr key={row.key}>
            {fields.map(field => {
              const uncertain = row.extracted && row.bands?.[field] !== 'HIGH'
              const validation = fieldError(row, field)
              const errorId = `row-${index}-${field}-error`
              return <td key={field} className={uncertain ? 'verify' : ''}>
                <label>
                  <span className="sr-only">Row {index + 1} {labels[field]}</span>
                  <input required
                    aria-invalid={!!validation} aria-describedby={validation ? errorId : undefined}
                    inputMode={field === 'itemName' ? 'text' : 'decimal'} value={row[field]}
                    maxLength={field === 'itemName' ? MAX_ITEM_CHARS + 20 : undefined}
                    onChange={event => change(row.key, field, event.target.value)} />
                </label>
                {validation && <p id={errorId} className="field-error">{validation}</p>}
                {uncertain && <label className="verification"><input type="checkbox"
                  checked={row.verifiedFields.includes(field)} onChange={event => {
                    setRows(rows.map(current => current.key === row.key ? { ...current,
                      verifiedFields: event.target.checked ? [...new Set([...current.verifiedFields, field])] : current.verifiedFields.filter(value => value !== field),
                    } : current))
                  }} />{row.bands?.[field] === 'LOW' ? 'Could not read — enter value' : 'Please verify'}</label>}
              </td>
            })}
            <td><button type="button" onClick={() => setRows(rows.filter(current => current.key !== row.key))}>Remove row {index + 1}</button></td>
          </tr>)}</tbody>
        </table></div>
        <button type="button" disabled={rows.length >= MAX_ROWS} onClick={() => setRows([...rows, {
          key: crypto.randomUUID(), extracted: false, itemName: '', quantity: '', unitPrice: '', verifiedFields: [],
        }])}>Add row</button>
        <p className="hint">Each row needs an item (up to 100 characters), a quantity above 0 and a price of 0 or more, with at most 2 decimal places.</p>
        {editable && invalid && <p role="status">Complete all fields and tick highlighted values. Between 1 and 100 valid rows are required.</p>}
        <button type="submit" disabled={invalid}>{busy ? 'Saving...' : 'Confirm sales'}</button>
      </fieldset>
    </form>
    {error && <p role="alert">{error} Your draft is retained; you can try again.</p>}
    {result && <p role="status">Sales saved. {result.recordIds.length} records {result.created ? 'created' : 'already confirmed'}.</p>}
  </section>
}
