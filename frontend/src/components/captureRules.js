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
  REJECTED: 'We could not use this input. Retake the photo, or review it manually and type the sales.',
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

export function textProblem(text) {
  const trimmed = text.trim()
  if (!trimmed) return 'Type or paste at least one sales line.'
  if (trimmed.length > MAX_TEXT_CHARS) return `Text can be at most ${MAX_TEXT_CHARS.toLocaleString('en-US')} characters.`
  return ''
}
