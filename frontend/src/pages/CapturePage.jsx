import { useNavigate } from 'react-router'
import { useAuth } from '../auth/useAuth'
import { rememberDraft } from '../auth/session'
import SalesCapture from '../components/SalesCapture'

export default function CapturePage() {
  const { request } = useAuth()
  const navigate = useNavigate()
  // Manual entry saves directly as CONFIRMED and shows its own confirmation, so it does not navigate.
  return <SalesCapture request={request} onManualSaved={() => {}} onDraft={draft => {
    rememberDraft(draft)
    // The extract response is the draft, so the review page renders without fetching it again.
    navigate(`/review/${encodeURIComponent(draft.submissionId)}`, { state: { draft } })
  }} />
}
