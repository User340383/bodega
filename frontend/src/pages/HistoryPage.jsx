import { useAuth } from '../auth/useAuth'
import SalesHistory from '../components/SalesHistory'

export default function HistoryPage() {
  const { request } = useAuth()
  return <SalesHistory request={request} revision={0} />
}
