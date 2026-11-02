import { useState } from 'react'

const fields = ['itemName', 'quantity', 'unitPrice']
const labels = { itemName: 'Item', quantity: 'Quantity', unitPrice: 'Unit price' }
const numeric = /^[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?$/

function fieldError(row, field) {
  const value = row[field].trim()
  if (!value) return `${labels[field]} is required.`
  if (field === 'itemName') return ''
  if (!numeric.test(value)) return 'Enter a finite number.'
  // Check sign and zero without rounding or underflow through JavaScript Number.
  const mantissa = value.split(/[eE]/)[0]
  const nonzero = /[1-9]/.test(mantissa)
  if ((mantissa.startsWith('-') && nonzero) || (field === 'quantity' && !nonzero)) return field === 'quantity' ? 'Quantity must be greater than zero.' : 'Price cannot be negative.'
  return ''
}

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
  const [status, setStatus] = useState(draft.status)
  const editable = ['NEEDS_VERIFICATION', 'AI_UNAVAILABLE'].includes(status)
  const extractedRows = rows.filter(row => row.extracted)
  const missingLink = extractedRows.some(row => typeof row.lineId !== 'string' || !row.lineId.trim()) ||
    new Set(extractedRows.map(row => row.lineId)).size !== extractedRows.length
  const invalid = missingLink || rows.length === 0 || rows.length > 100 || rows.some(row => fields.some(field =>
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
          quantity: row.quantity, unitPrice: row.unitPrice, verifiedFields: row.verifiedFields,
        })) }),
      })
      setResult(response)
      onConfirmed()
    } catch (err) { setError(err.message) }
    finally { setBusy(false) }
  }

  return <section aria-labelledby="draft-heading">
    <h2 id="draft-heading">Review draft</h2>
    <p>Sales date: {draft.saleDate} | {status}</p>
    <p>Check every row before confirming. Highlighted fields need verification or correction.</p>
    {status === 'REJECTED' && <button disabled={busy} onClick={async () => {
      setBusy(true)
      setError('')
      try {
        const updated = await request(`/api/v1/submissions/${draft.submissionId}/manual-review`, { method: 'POST' })
        setStatus(updated.status)
        setRows([])
      } catch (err) { setError(err.message) }
      finally { setBusy(false) }
    }}>Review manually</button>}
    {!editable && <p role="status">This submission is not awaiting confirmation.</p>}
    {missingLink && <p role="alert">This draft could not be loaded completely. Reopen it before confirming.</p>}
    <form onSubmit={confirm}>
      <fieldset disabled={busy || !!result || !editable}>
        <div className="table-scroll"><table>
          <thead><tr>{fields.map(field => <th key={field}>{labels[field]}</th>)}<th>Actions</th></tr></thead>
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
                    onChange={event => change(row.key, field, event.target.value)} />
                </label>
                {validation && <p id={errorId} className="field-error">{validation}</p>}
                {uncertain && <label className="verification"><input type="checkbox"
                  checked={row.verifiedFields.includes(field)} onChange={event => {
                    setRows(rows.map(current => current.key === row.key ? { ...current,
                      verifiedFields: event.target.checked ? [...new Set([...current.verifiedFields, field])] : current.verifiedFields.filter(value => value !== field),
                    } : current))
                  }} />{row.bands?.[field] === 'LOW' ? 'Could not read - enter value' : 'Please verify'}</label>}
              </td>
            })}
            <td><button type="button" onClick={() => setRows(rows.filter(current => current.key !== row.key))}>Remove row {index + 1}</button></td>
          </tr>)}</tbody>
        </table></div>
        <button type="button" disabled={rows.length >= 100} onClick={() => setRows([...rows, {
          key: crypto.randomUUID(), extracted: false, itemName: '', quantity: '', unitPrice: '', verifiedFields: [],
        }])}>Add row</button>
        <p>Use a nonblank item, a finite positive quantity and a finite nonnegative unit price.</p>
        {invalid && <p role="status">Complete all fields and verify highlighted values. Between 1 and 100 valid rows are required.</p>}
        <button type="submit" disabled={invalid}>{busy ? 'Saving...' : 'Confirm sales'}</button>
      </fieldset>
    </form>
    {error && <p role="alert">{error} Your draft is retained; you can try again.</p>}
    {result && <p role="status">Sales saved. {result.recordIds.length} records {result.created ? 'created' : 'already confirmed'}.</p>}
  </section>
}
