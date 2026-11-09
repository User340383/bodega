// Sales-row rules shared by draft review and manual entry (FR-202 as clarified in M3 section 3,
// M3 7.2). The server repeats every check. Decimal text is validated exactly, never through
// JavaScript Number, so nothing is rounded or underflows.
export const FIELDS = ['itemName', 'quantity', 'unitPrice']
export const LABELS = { itemName: 'Item', quantity: 'Quantity', unitPrice: 'Unit price' }
export const MAX_ITEM_CHARS = 100
export const MAX_ROWS = 100
// NFR-731: short plain sentences ("Item is required." scores above grade 8).
const REQUIRED = { itemName: 'Type the item name in this box.', quantity: 'Type the quantity in this box.', unitPrice: 'Type the unit price in this box.' }
// Largest values in hundredths (2 decimal places).
const MAX_CENTS = { quantity: 9999999n, unitPrice: 999999999n }
const NUMERIC = /^([+-]?)(\d*)(?:\.(\d*))?(?:[eE]([+-]?\d+))?$/

// Owner-facing names for submission states (NFR-731: no internal codes on screen).
export const STATUS_LABELS = {
  UPLOADED: 'Being read', CLASSIFYING: 'Being read', NEEDS_VERIFICATION: 'Needs checking',
  AI_UNAVAILABLE: 'Type the sales', REJECTED: 'Could not read', CONFIRMED: 'Saved',
}

// Exact value as { negative, digits: BigInt, scale } meaning digits / 10^scale, or null.
export function parseDecimal(text) {
  const match = NUMERIC.exec(String(text).trim())
  if (!match || (!match[2] && !match[3])) return null
  const [, sign, whole = '', fraction = '', exponent = '0'] = match
  if (Math.abs(Number(exponent)) > 1000) return null
  let digits = BigInt(`${whole}${fraction}` || '0')
  let scale = fraction.length - Number(exponent)
  while (digits !== 0n && digits % 10n === 0n) { digits /= 10n; scale -= 1 }
  if (digits === 0n) scale = 0
  return { negative: sign === '-' && digits !== 0n, digits, scale }
}

export function fieldError(row, field) {
  const value = String(row[field] ?? '').trim()
  if (!value) return REQUIRED[field]
  if (field === 'itemName') return value.length > MAX_ITEM_CHARS ? `Use at most ${MAX_ITEM_CHARS} characters.` : ''
  const number = parseDecimal(value)
  if (!number) return 'Enter a number, such as 2 or 1.50.'
  if (field === 'quantity' && (number.negative || number.digits === 0n)) return 'Quantity must be greater than zero.'
  if (number.negative) return 'Price cannot be negative.'
  if (number.scale > 2) return 'Use at most 2 decimal places.'
  const cents = number.digits * 10n ** BigInt(2 - number.scale)
  if (cents > MAX_CENTS[field]) return field === 'quantity' ? 'Quantity can be at most 99,999.99.' : 'Price can be at most 9,999,999.99.'
  return ''
}

export function rowErrors(row) {
  return FIELDS.some(field => fieldError(row, field))
}

export function validLineId(lineId) {
  return (Number.isInteger(lineId) && lineId > 0) || (typeof lineId === 'string' && lineId.trim() !== '')
}
