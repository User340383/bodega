import { Navigate, Route, Routes } from 'react-router'
import AuthProvider from './auth/AuthProvider'
import RequireAuth from './auth/RequireAuth'
import AppLayout from './layout/AppLayout'
import AnalysisPage from './pages/AnalysisPage'
import CapturePage from './pages/CapturePage'
import DraftsPage from './pages/DraftsPage'
import HistoryPage from './pages/HistoryPage'
import LoginPage from './pages/LoginPage'
import NotFoundPage from './pages/NotFoundPage'
import ReviewPage from './pages/ReviewPage'
import './App.css'

// Application shell: routes from M3 4.3. Every page except /login requires the owner access code.
// The router itself (BrowserRouter in main.jsx, MemoryRouter in tests) wraps <App />.
export default function App({ clientOptions }) {
  return <AuthProvider clientOptions={clientOptions}>
    <Routes>
      <Route path="/login" element={<LoginPage />} />
      <Route element={<RequireAuth><AppLayout /></RequireAuth>}>
        <Route index element={<Navigate to="/capture" replace />} />
        <Route path="capture" element={<CapturePage />} />
        <Route path="review/:submissionId" element={<ReviewPage />} />
        <Route path="drafts" element={<DraftsPage />} />
        <Route path="history" element={<HistoryPage />} />
        <Route path="analysis" element={<AnalysisPage />} />
        <Route path="*" element={<NotFoundPage />} />
      </Route>
    </Routes>
  </AuthProvider>
}
