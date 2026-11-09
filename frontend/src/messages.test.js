// NFR-731 for messages the browser itself shows: cause + one action, <= 30 words, Flesch-Kincaid
// grade <= 8, no codes or provider names. Same formula as backend/app/readability.py.
import { describe, expect, it } from 'vitest'
import { FALLBACK_TEXT, fileProblem, textProblem } from './components/captureRules'
import { fieldError } from './components/draftRules'
import { errorFromResponse } from './api/client'

const WORD = /[A-Za-z]+(?:'[A-Za-z]+)?|\d+(?:[.,]\d+)*/g
const words = text => text.match(WORD) || []
function syllables(word) {
  if (/^\d/.test(word)) return 1
  const w = word.toLowerCase().replace("'", '')
  let count = (w.match(/[aeiouy]+/g) || []).length
  if (w.endsWith('e') && !/(le|ee|ye)$/.test(w) && count > 1) count -= 1
  return Math.max(count, 1)
}
function grade(text) {
  const tokens = words(text)
  const sentences = Math.max((text.trim().match(/[.!?]+(?=\s|$)/g) || []).length, 1)
  const total = tokens.reduce((sum, token) => sum + syllables(token), 0)
  return 0.39 * (tokens.length / sentences) + 11.8 * (total / tokens.length) - 15.59
}

const row = (field, value) => ({ itemName: 'Bread', quantity: '1', unitPrice: '1', [field]: value })
const MESSAGES = {
  ...FALLBACK_TEXT,
  noPhoto: fileProblem(null),
  badPhoto: fileProblem({ type: 'application/pdf', size: 10 }),
  noText: textProblem(''),
  longText: textProblem('x'.repeat(5001)),
  markup: textProblem('<b>'),
  control: textProblem('a\u0001'),
  ...Object.fromEntries([['quantity', '0'], ['quantity', '1.234'], ['quantity', '100000'], ['quantity', 'abc'],
    ['unitPrice', '-1'], ['unitPrice', '10000000'], ['itemName', ''], ['itemName', 'x'.repeat(101)]]
    .map(([field, value]) => [`${field}:${value.slice(0, 8)}`, fieldError(row(field, value), field)])),
  ...Object.fromEntries([401, 404, 409, 429, 500, 502, 503, 504].map(status => [`http${status}`, errorFromResponse(status, null).message])),
}
const ACTION = /\b(try|type|retake|choose|check|use|enter|wait|sign|refresh|paste|remove)\b/i

describe('NFR-731 browser messages', () => {
  it('uses the same calibration as the backend', () => {
    expect(grade('The cat sat on the mat.')).toBeCloseTo(-1.45, 1)
  })

  it.each(Object.entries(MESSAGES))('%s meets the limits', (name, text) => {
    expect(text).toBeTruthy()
    expect(words(text).length).toBeLessThanOrEqual(30)
    expect(grade(text)).toBeLessThanOrEqual(8)
    expect(text).not.toMatch(/\b(claude|anthropic|openai|gpt|HTTP|JSON|[A-Z]+_[A-Z_]+)\b/i)
    // Field messages name the problem in the field itself; every other message gives an action.
    if (!name.includes(':')) expect(text).toMatch(ACTION)
  })
})
