import { NavLink, useNavigate } from 'react-router-dom'
import { LayoutDashboard, FolderOpen, BookOpen, Settings, LogOut, PanelLeftClose, PanelLeftOpen } from 'lucide-react'
import { clearAuth, getUser } from '../../api/client'
import BrandLockup from './BrandLockup'

const navItems = [
  { to: '/', icon: LayoutDashboard, label: 'Dashboard', end: true },
  { to: '/projects', icon: FolderOpen, label: 'Projects' },
  { to: '/documentation', icon: BookOpen, label: 'Documentation' },
  { to: '/settings', icon: Settings, label: 'Settings' },
]

export default function Sidebar({ open, onToggle }) {
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
      className="fixed left-0 top-0 h-full flex flex-col z-30 overflow-hidden"
      style={{
        width: open ? 300 : 64,
        background: '#0D1117',
        transition: 'width 0.2s ease-out',
      }}
    >
      <div
        className={`flex items-center py-4 ${open ? 'px-3 justify-between gap-2' : 'px-0 justify-center'}`}
        style={{ borderBottom: '1px solid rgba(255,255,255,0.08)', minHeight: 64 }}
      >
        {open && (
          <div className="min-w-0 flex-1">
            <BrandLockup size="sm" />
          </div>
        )}
        <button
          type="button"
          onClick={onToggle}
          title={open ? 'Hide sidebar' : 'Show sidebar'}
          className="w-9 h-9 flex items-center justify-center rounded-lg flex-shrink-0"
          style={{ color: 'rgba(255,255,255,0.7)' }}
          onMouseEnter={(e) => { e.currentTarget.style.background = 'rgba(255,255,255,0.08)'; e.currentTarget.style.color = '#fff' }}
          onMouseLeave={(e) => { e.currentTarget.style.background = 'transparent'; e.currentTarget.style.color = 'rgba(255,255,255,0.7)' }}
          aria-label={open ? 'Hide sidebar' : 'Show sidebar'}
        >
          {open ? <PanelLeftClose size={18} /> : <PanelLeftOpen size={18} />}
        </button>
      </div>

      {open && (
        <>
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
        </>
      )}
    </aside>
  )
}
