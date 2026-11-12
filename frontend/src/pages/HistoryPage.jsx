import { useState } from 'react'
import { useAuth } from '../auth/useAuth'
import DeleteAllData from '../components/DeleteAllData'
import SalesHistory from '../components/SalesHistory'

export default function HistoryPage() {
  const { request } = useAuth()
  const [revision, setRevision] = useState(0)
  return <>
    <SalesHistory request={request} revision={revision} />
    <DeleteAllData request={request} onDeleted={() => setRevision(value => value + 1)} />
  </>
}
