import { NavLink, Outlet } from 'react-router'
import { useAuth } from '../auth/useAuth'
import './AppLayout.css'

const NAV_ITEMS = [
  { to: '/capture', label: 'Capture' },
  { to: '/drafts', label: 'Pending drafts' },
  { to: '/history', label: 'History' },
  { to: '/analysis', label: 'Analysis' },
]

export default function AppLayout() {
  const { logout } = useAuth()
  return <div className="app-shell">
    <a className="skip-link" href="#main-content">Skip to content</a>
    <header className="app-header">
      <NavLink to="/capture" className="brand">Bodega</NavLink>
      <nav aria-label="Main">
        <ul className="nav-list">
          {NAV_ITEMS.map(item => <li key={item.to}>
            <NavLink to={item.to} className={({ isActive }) => `nav-link${isActive ? ' active' : ''}`}>{item.label}</NavLink>
          </li>)}
        </ul>
      </nav>
      <button type="button" className="sign-out" onClick={() => logout()}>Sign out</button>
    </header>
    <main id="main-content" tabIndex={-1}>
      <Outlet />
    </main>
  </div>
}
