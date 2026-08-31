import { NavLink, useNavigate } from 'react-router-dom'
import { LayoutDashboard, FolderOpen, Settings, LogOut } from 'lucide-react'
import { clearAuth, getUser } from '../../api/client'

const navItems = [
  { to: '/', icon: LayoutDashboard, label: 'Dashboard', end: true },
  { to: '/projects', icon: FolderOpen, label: 'Projects' },
  { to: '/settings', icon: Settings, label: 'Settings' },
]

function AivarLogo() {
  return (
    <svg width="28" height="28" viewBox="0 0 28 28" fill="none" xmlns="http://www.w3.org/2000/svg">
      <path d="M14 2L25.5 8.5V19.5L14 26L2.5 19.5V8.5L14 2Z" stroke="white" strokeWidth="1.8" fill="none" strokeLinejoin="round"/>
      <path d="M9 14.5L12.5 18L19 11" stroke="#A29BFE" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round"/>
    </svg>
  )
}

export default function Sidebar() {
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
      className="fixed left-0 top-0 h-full w-[220px] flex flex-col z-30"
      style={{ background: '#0D1117' }}
    >
      {/* Logo */}
      <div className="px-5 py-5" style={{ borderBottom: '1px solid rgba(255,255,255,0.08)' }}>
        <div className="flex items-center gap-2.5">
          <AivarLogo />
          <span className="text-white font-bold text-[18px] tracking-widest uppercase">Aivar</span>
        </div>
        <p className="text-[11px] font-medium mt-1.5 tracking-wider uppercase" style={{ color: 'rgba(255,255,255,0.35)' }}>
          Pando Testing Agent
        </p>
      </div>

      {/* Nav */}
      <nav className="flex-1 px-3 py-4" style={{ display: 'flex', flexDirection: 'column', gap: 2 }}>
        {navItems.map(({ to, icon: Icon, label, end }) => (
          <NavLink
            key={to}
            to={to}
            end={end}
            style={({ isActive }) => ({
              display: 'flex',
              alignItems: 'center',
              gap: 10,
              padding: '9px 12px',
              borderRadius: 8,
              fontSize: 14,
              fontWeight: 500,
              textDecoration: 'none',
              transition: 'background 0.15s, color 0.15s',
              background: isActive ? '#6C5CE7' : 'transparent',
              color: isActive ? '#ffffff' : 'rgba(255,255,255,0.55)',
            })}
            onMouseEnter={(e) => {
              if (!e.currentTarget.dataset.active) {
                e.currentTarget.style.background = 'rgba(255,255,255,0.07)'
                e.currentTarget.style.color = '#ffffff'
              }
            }}
            onMouseLeave={(e) => {
              if (!e.currentTarget.dataset.active) {
                e.currentTarget.style.background = e.currentTarget.getAttribute('aria-current') ? '#6C5CE7' : 'transparent'
                e.currentTarget.style.color = e.currentTarget.getAttribute('aria-current') ? '#ffffff' : 'rgba(255,255,255,0.55)'
              }
            }}
          >
            <Icon size={16} />
            {label}
          </NavLink>
        ))}
      </nav>

      {/* User + logout */}
      <div className="px-3 pb-4" style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
        <div
          className="flex items-center gap-2.5 px-3 py-2.5 rounded-lg"
          style={{ background: 'rgba(255,255,255,0.05)', border: '1px solid rgba(255,255,255,0.08)' }}
        >
          <div
            className="w-7 h-7 rounded-full flex items-center justify-center flex-shrink-0"
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
          className="w-full flex items-center gap-2.5 px-3 py-2 rounded-lg text-sm font-medium transition-all"
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
