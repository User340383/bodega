// The access code lives only in sessionStorage, so it is cleared when the tab closes (M3 4.3 /login).
// Storage can be unavailable (private mode, blocked site data); the app then keeps the code in memory only.
const CODE_KEY = 'bodega.accessCode'
const RECENT_KEY = 'bodega.recentDrafts'
const MAX_RECENT = 10

function storage() {
  try { return globalThis.sessionStorage ?? null } catch { return null }
}

export function readAccessCode() {
  try { return storage()?.getItem(CODE_KEY) || '' } catch { return '' }
}

export function writeAccessCode(code) {
  try { if (code) storage()?.setItem(CODE_KEY, code); else storage()?.removeItem(CODE_KEY) } catch { /* memory only */ }
}

// Drafts captured in this tab, so the owner can get back to one without copying its ID.
export function readRecentDrafts() {
  try {
    const value = JSON.parse(storage()?.getItem(RECENT_KEY) || '[]')
    return Array.isArray(value) ? value.filter(item => item && typeof item.submissionId === 'string') : []
  } catch { return [] }
}

export function rememberDraft(draft) {
  if (!draft?.submissionId) return
  const entry = {
    submissionId: String(draft.submissionId), saleDate: draft.saleDate ?? '', status: draft.status ?? '',
    inputType: draft.inputType ?? '', capturedAt: new Date().toISOString(),
  }
  const recent = [entry, ...readRecentDrafts().filter(item => item.submissionId !== entry.submissionId)].slice(0, MAX_RECENT)
  try { storage()?.setItem(RECENT_KEY, JSON.stringify(recent)) } catch { /* memory only */ }
}

export function clearSession() {
  try { storage()?.removeItem(CODE_KEY); storage()?.removeItem(RECENT_KEY) } catch { /* nothing stored */ }
}
