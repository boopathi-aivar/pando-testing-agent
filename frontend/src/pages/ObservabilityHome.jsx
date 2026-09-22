import { useState } from 'react'
import { Link } from 'react-router-dom'
import { Search } from 'lucide-react'
import { OBSERVABILITY_PROJECTS } from '../observability/projects'

export default function ObservabilityHome() {
  const [query, setQuery] = useState('')
  const q = query.trim().toLowerCase()
  const projects = OBSERVABILITY_PROJECTS.filter((p) => {
    if (!q) return true
    const hay = [
      p.name,
      p.id,
      p.description || '',
      ...(p.tabs || []).flatMap((t) => [t.id, t.label]),
    ]
      .join(' ')
      .toLowerCase()
    return hay.includes(q)
  })

  return (
    <div>
      <h1 className="text-2xl font-semibold text-text-primary tracking-tight">Observability</h1>
      <p className="mt-1 text-sm text-text-secondary">
        Select a project to view live processing dashboards.
      </p>

      <div className="mt-6 relative max-w-md">
        <Search
          size={16}
          className="absolute left-3 top-1/2 -translate-y-1/2 text-text-secondary pointer-events-none"
        />
        <input
          type="search"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder="Search projects…"
          className="w-full pl-9 pr-3 py-2.5 rounded-xl border border-border bg-white text-sm text-text-primary placeholder:text-text-secondary/70 outline-none focus:border-pando-green/50"
        />
      </div>

      {projects.length === 0 ? (
        <p className="mt-8 text-sm text-text-secondary">No projects match “{query.trim()}”.</p>
      ) : (
        <ul className="mt-6 grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
          {projects.map((project) => (
            <li key={project.id}>
              <Link
                to={`/observability/${project.id}`}
                className="block h-full px-4 py-3 rounded-xl border border-border bg-white hover:border-pando-green/40 hover:bg-background transition-colors"
              >
                <span className="font-medium text-text-primary">{project.name}</span>
                <p className="text-sm text-text-secondary mt-0.5">{project.description}</p>
              </Link>
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}
