import { NavLink, useNavigate } from 'react-router-dom'
import { LayoutDashboard, FolderOpen, Settings, LogOut, PanelLeftClose } from 'lucide-react'
import { clearAuth, getUser } from '../../api/client'

const navItems = [
  { to: '/', icon: LayoutDashboard, label: 'Dashboard', end: true },
  { to: '/projects', icon: FolderOpen, label: 'Projects' },
  { to: '/settings', icon: Settings, label: 'Settings' },
]

export default function Sidebar({ open, onClose }) {
  const navigate = useNavigate()
  const user = getUser()

  const handleLogout = () => {
    clearAuth()
    navigate('/login', { replace: true })
  }

  const initials = user?.name
    ? user.name.split(' ').map((w) => w[0]).join('').slice(0, 2).toUpperCase()
    : 'AI'

  return (
    <aside
      className="fixed left-0 top-0 h-full w-[240px] flex flex-col z-30 overflow-hidden"
      style={{
        background: '#0D1117',
        transform: open ? 'translateX(0)' : 'translateX(-100%)',
        transition: 'transform 0.2s ease-out',
      }}
    >
      <div
        className="px-5 py-5 flex items-start justify-between gap-2"
        style={{ borderBottom: '1px solid rgba(255,255,255,0.08)' }}
      >
        <div className="min-w-0">
          <img
            src="/aivar-logo-white.webp"
            alt="Aivar"
            className="h-11 w-auto object-contain"
          />
          <p className="text-[11px] font-medium mt-3 tracking-[0.16em] uppercase" style={{ color: 'rgba(255,255,255,0.38)' }}>
            Pando Testing Agent
          </p>
        </div>
        <button
          type="button"
          onClick={onClose}
          title="Hide sidebar"
          className="mt-1 w-8 h-8 flex items-center justify-center rounded-lg flex-shrink-0"
          style={{ color: 'rgba(255,255,255,0.45)' }}
          onMouseEnter={(e) => { e.currentTarget.style.background = 'rgba(255,255,255,0.08)'; e.currentTarget.style.color = '#fff' }}
          onMouseLeave={(e) => { e.currentTarget.style.background = 'transparent'; e.currentTarget.style.color = 'rgba(255,255,255,0.45)' }}
        >
          <PanelLeftClose size={16} />
        </button>
      </div>

      <nav className="flex-1 px-3 py-4 flex flex-col gap-0.5">
        {navItems.map(({ to, icon: Icon, label, end }) => (
          <NavLink
            key={to}
            to={to}
            end={end}
            style={({ isActive }) => ({
              display: 'flex',
              alignItems: 'center',
              gap: 10,
              padding: '10px 12px',
              borderRadius: 10,
              fontSize: 14,
              fontWeight: 500,
              textDecoration: 'none',
              transition: 'background 0.15s, color 0.15s',
              background: isActive ? '#6C5CE7' : 'transparent',
              color: isActive ? '#ffffff' : 'rgba(255,255,255,0.55)',
            })}
            onMouseEnter={(e) => {
              if (e.currentTarget.getAttribute('aria-current') !== 'page') {
                e.currentTarget.style.background = 'rgba(255,255,255,0.07)'
                e.currentTarget.style.color = '#ffffff'
              }
            }}
            onMouseLeave={(e) => {
              const active = e.currentTarget.getAttribute('aria-current') === 'page'
              e.currentTarget.style.background = active ? '#6C5CE7' : 'transparent'
              e.currentTarget.style.color = active ? '#ffffff' : 'rgba(255,255,255,0.55)'
            }}
          >
            <Icon size={16} />
            {label}
          </NavLink>
        ))}
      </nav>

      <div className="px-3 pb-5 flex flex-col gap-2">
        <div
          className="flex items-center gap-2.5 px-3 py-2.5 rounded-xl"
          style={{ background: 'rgba(255,255,255,0.05)', border: '1px solid rgba(255,255,255,0.08)' }}
        >
          <div
            className="w-8 h-8 rounded-full flex items-center justify-center flex-shrink-0"
            style={{ background: '#6C5CE7' }}
          >
            <span className="text-white font-bold text-[10px]">{initials}</span>
          </div>
          <div className="min-w-0 flex-1">
            <p className="text-white text-xs font-semibold truncate">{user?.name ?? 'Aivar Admin'}</p>
            <p className="text-[10px] truncate" style={{ color: 'rgba(255,255,255,0.35)' }}>{user?.email ?? ''}</p>
          </div>
        </div>

        <button
          onClick={handleLogout}
          className="w-full flex items-center gap-2.5 px-3 py-2 rounded-xl text-sm font-medium transition-all"
          style={{ color: 'rgba(255,255,255,0.45)' }}
          onMouseEnter={(e) => { e.currentTarget.style.background = 'rgba(255,255,255,0.08)'; e.currentTarget.style.color = '#ffffff' }}
          onMouseLeave={(e) => { e.currentTarget.style.background = 'transparent'; e.currentTarget.style.color = 'rgba(255,255,255,0.45)' }}
        >
          <LogOut size={15} />
          Sign out
        </button>
      </div>
    </aside>
  )
}
