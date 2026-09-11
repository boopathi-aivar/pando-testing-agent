import { Moon, Sun } from 'lucide-react'
import { getUser } from '../../api/client'
import { useTheme } from '../../theme'

export default function TopBar({ title, sidebarOpen }) {
  const user = getUser()
  const { isDark, toggleTheme } = useTheme()
  const initials = user?.name
    ? user.name.split(' ').map((w) => w[0]).join('').slice(0, 2).toUpperCase()
    : 'AD'

  return (
    <header
      className="fixed right-0 top-0 h-16 bg-surface border-b border-border z-20 shadow-topbar transition-[left] duration-200 ease-out"
      style={{ left: sidebarOpen ? 300 : 64 }}
    >
      <div className="relative flex items-center justify-between h-full px-5">
        <div className="flex items-center min-w-0 z-10">
          {title && (
            <span className="text-text-primary font-semibold text-[15px] tracking-tight truncate">
              {title}
            </span>
          )}
        </div>

        <div className="absolute left-1/2 top-1/2 -translate-x-1/2 -translate-y-1/2 pointer-events-none">
          <div className="h-11 px-4 rounded-xl bg-[#0D1117] border border-white/10 flex items-center justify-center">
            <img
              src="/aivar-logo-white.webp"
              alt="Aivar"
              className="h-8 w-auto object-contain"
            />
          </div>
        </div>

        <div className="flex items-center gap-2 z-10">
          <button
            type="button"
            onClick={toggleTheme}
            title={isDark ? 'Switch to light theme' : 'Switch to dark theme'}
            className="w-9 h-9 flex items-center justify-center rounded-lg text-text-muted hover:bg-background hover:text-pando-green transition-colors"
            aria-label="Toggle theme"
          >
            {isDark ? <Sun size={17} /> : <Moon size={17} />}
          </button>
          <div className="w-9 h-9 rounded-full bg-pando-green flex items-center justify-center text-white text-[11px] font-bold flex-shrink-0">
            {initials}
          </div>
        </div>
      </div>
    </header>
  )
}
