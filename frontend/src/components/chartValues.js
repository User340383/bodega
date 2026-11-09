// Amounts arrive as exact JSON numbers (strings in older responses). Values are displayed as
// received. Number is used only to position chart marks, and only after these checks.

const ZERO_TEXT = /^0*\.?0*$/

/** Pixel-position value for a decimal string, or null when a float cannot represent it. */
export function chartNumber(text) {
  const value = Number(text)
  if (!Number.isFinite(value)) return null // Overflow, such as a 400-digit quantity.
  if (value === 0 && !ZERO_TEXT.test(text)) return null // Underflow, such as 1e-400.
  return value
}

/** Smallest round axis maximum at or above `largest`, or null if that overflows. */
export function axisMaximum(largest) {
  if (largest <= 0) return 1
  const magnitude = 10 ** Math.floor(Math.log10(largest))
  const multiple = [1, 2, 5, 10].find(step => step * magnitude >= largest) ?? 10
  const top = multiple * magnitude
  return Number.isFinite(top) && top > 0 ? top : null
}

/** Compact, approximate label for an axis tick. Exact values are in the table. */
export function tickLabel(value) {
  return value.toLocaleString('en-US', { maximumSignificantDigits: 3, notation: value >= 10000 ? 'compact' : 'standard' })
}
