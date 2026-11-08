import { Navigate, useLocation } from 'react-router'
import { useAuth } from './useAuth'

// Every page except /login needs the access code; after signing in the owner returns to the page they asked for.
export default function RequireAuth({ children }) {
  const { isSignedIn } = useAuth()
  const location = useLocation()
  if (!isSignedIn) return <Navigate to="/login" replace state={{ from: `${location.pathname}${location.search}` }} />
  return children
}
