import { useNavigate } from 'react-router'
import { useAuth } from '../auth/useAuth'
import { rememberDraft } from '../auth/session'
import SalesCapture from '../components/SalesCapture'

export default function CapturePage() {
  const { request } = useAuth()
  const navigate = useNavigate()
  return <SalesCapture request={request} onDraft={draft => {
    rememberDraft(draft)
    // The extract response is the draft, so the review page renders without fetching it again.
    navigate(`/review/${encodeURIComponent(draft.submissionId)}`, { state: { draft } })
  }} />
}
