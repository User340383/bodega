import { useAuth } from '../auth/useAuth'
import ResultsDashboard from '../components/ResultsDashboard'

export default function AnalysisPage() {
  const { request } = useAuth()
  return <ResultsDashboard request={request} revision={0} />
}
