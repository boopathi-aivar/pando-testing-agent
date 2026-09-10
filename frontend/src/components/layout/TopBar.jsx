import { Bell, Search, PanelLeftOpen, PanelLeftClose } from 'lucide-react'
import { getUser } from '../../api/client'

export default function TopBar({ title, sidebarOpen, onToggleSidebar }) {
  const user = getUser()
  const initials = user?.name
    ? user.name.split(' ').map((w) => w[0]).join('').slice(0, 2).toUpperCase()
    : 'AD'

  return (
    <header
      className="fixed right-0 top-0 h-14 bg-white border-b border-border flex items-center justify-between px-6 z-20 shadow-topbar transition-[left] duration-200 ease-out"
      style={{ left: sidebarOpen ? 240 : 0 }}
    >
      <div className="flex items-center gap-3 min-w-0">
        <button
          type="button"
          onClick={onToggleSidebar}
          title={sidebarOpen ? 'Hide sidebar' : 'Show sidebar'}
          className="w-8 h-8 flex items-center justify-center rounded-lg text-text-secondary hover:bg-background hover:text-pando-green transition-colors flex-shrink-0"
          aria-label={sidebarOpen ? 'Hide sidebar' : 'Show sidebar'}
        >
          {sidebarOpen ? <PanelLeftClose size={18} /> : <PanelLeftOpen size={18} />}
        </button>
        <span className="text-text-primary font-semibold text-[15px] tracking-tight truncate">{title}</span>
      </div>

      <div className="flex items-center gap-3">
        <div className="relative hidden md:block">
          <Search size={14} className="absolute left-3 top-1/2 -translate-y-1/2 text-text-muted" />
          <input
            type="text"
            placeholder="Search projects..."
            className="w-72 pl-8 pr-3 py-1.5 text-sm"
          />
        </div>

        <button
          type="button"
          className="w-8 h-8 flex items-center justify-center rounded-lg text-text-muted hover:bg-background hover:text-text-secondary transition-colors relative"
          aria-label="Notifications"
        >
          <Bell size={16} />
        </button>

        <div className="w-8 h-8 rounded-full bg-pando-green flex items-center justify-center text-white text-[11px] font-bold flex-shrink-0">
          {initials}
        </div>
      </div>
    </header>
  )
}
