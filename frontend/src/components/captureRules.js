// Capture-screen rules shared by SalesCapture and its tests (M3 4.3, 9.7).
export const INPUT_NOTICE = "Your photo or text is sent to Anthropic's Claude AI service to read your sales. " +
  'We remove photo location data and hide phone numbers and emails in typed text, but anything written on the ' +
  'page can be seen by the AI. Please leave out customer names and personal details.'
export const LOADING_TEXT = 'Reading your sales log… this can take up to a minute'
export const MAX_FILE_BYTES = 10 * 1024 * 1024
export const MAX_TEXT_CHARS = 5000
export const REQUEST_TIMEOUT_MS = 90_000

export const FALLBACK_TEXT = {
  AI_UNAVAILABLE: 'We could not read this right now. You can type the sales below.',
  REJECTED: 'We could not read this photo. Use good light, lay the page flat, and fit the whole page in the frame. You can also type the sales below.',
}

export function localToday(now = new Date()) {
  const pad = value => String(value).padStart(2, '0')
  return `${now.getFullYear()}-${pad(now.getMonth() + 1)}-${pad(now.getDate())}`
}

// Fast feedback only: the server repeats every check (magic bytes, size, dates).
export function fileProblem(file) {
  if (!file) return 'Choose a photo of your sales page.'
  if (!['image/jpeg', 'image/png'].includes(file.type) || file.size > MAX_FILE_BYTES) return 'Choose a JPEG or PNG photo under 10 MB.'
  return ''
}

// Same rule as the server (NFR-714): no web code/tags and no control characters.
// eslint-disable-next-line no-control-regex
const CONTROL = /[\u0000-\u0008\u000b\u000c\u000e-\u001f\u007f]/
const MARKUP = /<\s*\/?\s*[A-Za-z!?][^>]*>|<\s*script|javascript\s*:/i

export function textProblem(text) {
  const trimmed = text.trim()
  if (!trimmed) return 'Type or paste at least one sales line.'
  if (trimmed.length > MAX_TEXT_CHARS) return `Text can be at most ${MAX_TEXT_CHARS.toLocaleString('en-US')} characters. Remove some lines and try again.`
  if (CONTROL.test(trimmed)) return 'Text contains characters that are not allowed. Type or paste plain sales lines.'
  if (MARKUP.test(trimmed)) return 'Text cannot contain web code or tags. Type or paste plain sales lines.'
  return ''
}
