import { useState } from 'react'
import { FIELDS, LABELS, MAX_ITEM_CHARS, MAX_ROWS, fieldError } from './draftRules'
import { localToday } from './captureRules'

const blankRow = () => ({ key: crypto.randomUUID(), itemName: '', quantity: '', unitPrice: '' })

// José: standalone manual entry (FR-104, M3 4.3 "Enter manually", SD-04). No AI request is made.
// The submission ID is generated once per form, so retrying after a network error can never
// save the same sales twice; the server returns the existing record IDs instead.
export default function ManualEntry({ request, onSaved, today = localToday }) {
  const [submissionId, setSubmissionId] = useState(() => crypto.randomUUID())
  const [saleDate, setSaleDate] = useState(() => today())
  const [rows, setRows] = useState(() => [blankRow()])
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [serverFields, setServerFields] = useState({})  // 422 error.fields, e.g. "lines.0.quantity"
  const [saved, setSaved] = useState(null)
  const maxDate = today()
  const dateProblem = !saleDate ? 'Choose the sale date.' : saleDate > maxDate ? 'Sale date cannot be in the future.' : ''
  const invalid = Boolean(dateProblem) || rows.length === 0 || rows.some(row => FIELDS.some(field => fieldError(row, field)))

  function change(key, field, value) {
    setServerFields({})
    setRows(rows.map(row => row.key === key ? { ...row, [field]: value } : row))
  }

  async function save(event) {
    event.preventDefault()
    if (busy || invalid || saved) return
    setBusy(true)
    setError('')
    try {
      const result = await request('/api/v1/manual-entries', { method: 'POST', body: {
        submissionId, saleDate,
        lines: rows.map(row => ({ itemName: row.itemName.trim(), quantity: Number(row.quantity.trim()), unitPrice: Number(row.unitPrice.trim()) })),
      } })
      setSaved(result)
      onSaved?.(result)
    } catch (err) {
      setError(err.message)
      setServerFields(err.fields || {})
    } finally {
      setBusy(false)
    }
  }

  function startAgain() {
    setSubmissionId(crypto.randomUUID())
    setRows([blankRow()])
    setSaved(null)
    setError('')
  }

  return <form onSubmit={save} aria-labelledby="manual-heading" className="manual-entry">
    <h3 id="manual-heading">Enter sales manually</h3>
    <p>Type each item you sold. Nothing is sent to the AI service.</p>
    <fieldset disabled={busy || Boolean(saved)}>
      <label>Sale date <input type="date" required value={saleDate} max={maxDate}
        onChange={event => setSaleDate(event.target.value)} /></label>
      {(dateProblem || serverFields.saleDate) && <p className="field-error">{dateProblem || serverFields.saleDate}</p>}
      <div className="table-scroll"><table>
        <thead><tr>{FIELDS.map(field => <th key={field} scope="col">{LABELS[field]}</th>)}<th scope="col">Actions</th></tr></thead>
        <tbody>{rows.map((row, index) => <tr key={row.key}>
          {FIELDS.map(field => {
            const touched = row[field] !== ''
            const problem = (touched ? fieldError(row, field) : '') || serverFields[`lines.${index}.${field}`] || ''
            const errorId = `manual-${index}-${field}-error`
            return <td key={field}>
              <label>
                <span className="sr-only">Row {index + 1} {LABELS[field]}</span>
                <input required value={row[field]} aria-invalid={Boolean(problem)}
                  aria-describedby={problem ? errorId : undefined}
                  inputMode={field === 'itemName' ? 'text' : 'decimal'}
                  maxLength={field === 'itemName' ? MAX_ITEM_CHARS + 20 : undefined}
                  onChange={event => change(row.key, field, event.target.value)} />
              </label>
              {problem && <p id={errorId} className="field-error">{problem}</p>}
            </td>
          })}
          <td><button type="button" disabled={rows.length === 1}
            onClick={() => setRows(rows.filter(current => current.key !== row.key))}>Remove row {index + 1}</button></td>
        </tr>)}</tbody>
      </table></div>
      <button type="button" disabled={rows.length >= MAX_ROWS} onClick={() => setRows([...rows, blankRow()])}>Add row</button>
      <button type="submit" disabled={invalid}>{busy ? 'Saving...' : 'Save sales'}</button>
    </fieldset>
    {error && <p role="alert">{error} Nothing was lost; press Save sales again.</p>}
    {saved && <div role="status">
      <p>Sales saved. {saved.recordIds.length} {saved.recordIds.length === 1 ? 'record' : 'records'} {saved.created ? 'created' : 'already saved'}.</p>
      <button type="button" onClick={startAgain}>Enter more sales</button>
    </div>}
  </form>
}
