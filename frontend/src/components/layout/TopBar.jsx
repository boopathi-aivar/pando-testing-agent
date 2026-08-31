import { useState } from 'react'
import { Bell, Search } from 'lucide-react'

export default function TopBar({ title, onSearch }) {
  const [query, setQuery] = useState('')

  const handleSearch = (e) => {
    setQuery(e.target.value)
    onSearch?.(e.target.value)
  }

  return (
    <header className="fixed left-[220px] right-0 top-0 h-14 bg-white border-b border-border flex items-center justify-between px-6 z-20 shadow-topbar">
      <div className="flex items-center gap-2">
        <span className="text-text-primary font-semibold text-[15px]">{title}</span>
      </div>

      <div className="flex items-center gap-3">
        <div className="relative">
          <Search size={14} className="absolute left-3 top-1/2 -translate-y-1/2 text-text-muted" />
          <input
            type="text"
            placeholder="Search projects..."
            value={query}
            onChange={handleSearch}
            className="w-60 pl-8 pr-3 py-1.5 text-sm"
          />
        </div>

        <button className="w-8 h-8 flex items-center justify-center rounded-lg text-text-muted hover:bg-background hover:text-text-secondary transition-colors relative">
          <Bell size={16} />
          <span className="absolute top-1.5 right-1.5 w-1.5 h-1.5 bg-pando-green rounded-full" />
        </button>

        <div className="w-8 h-8 rounded-full bg-pando-green flex items-center justify-center text-white text-[11px] font-bold flex-shrink-0">
          AD
        </div>
      </div>
    </header>
  )
}
