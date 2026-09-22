import { Link, Navigate, useNavigate, useParams } from 'react-router-dom'
import { ChevronRight } from 'lucide-react'
import {
  OBSERVABILITY_PROJECTS,
  resolveObservabilityProject,
} from '../observability/projects'

const API_ORIGIN = import.meta.env.VITE_API_URL || ''

function tabTitle(hub, activeId) {
  const tab = hub.tabs?.find((t) => t.id === activeId)
  if (tab) return `${hub.name} — ${tab.label}`
  return `${hub.name} Invoice Processing`
}

export default function ObservabilityProject() {
  const { projectId } = useParams()
  const navigate = useNavigate()
  const resolved = resolveObservabilityProject(projectId)

  if (!resolved) {
    return <Navigate to="/observability" replace />
  }

  const { hub, activeId } = resolved
  const apiBase = `${API_ORIGIN}/api/observability/${activeId}`
  const params = new URLSearchParams({
    apiBase,
    project: activeId,
    title: tabTitle(hub, activeId),
  })
  const src = `${hub.src}?${params.toString()}`

  return (
    <div className="flex flex-col flex-1 min-h-0 gap-3">
      <div className="flex flex-wrap items-center gap-2 sm:gap-3 shrink-0 px-0.5">
        <nav className="flex items-center gap-1.5 text-sm min-w-0" aria-label="Breadcrumb">
          <Link
            to="/observability"
            className="text-text-secondary hover:text-text-primary transition-colors truncate"
          >
            Observability
          </Link>
          <ChevronRight size={14} className="text-text-secondary flex-shrink-0" aria-hidden />
          <span className="font-medium text-text-primary truncate">{hub.name}</span>
        </nav>

        <label className="ml-auto flex items-center gap-2 text-sm text-text-secondary">
          <span className="hidden sm:inline">Project</span>
          <select
            value={hub.id}
            onChange={(e) => navigate(`/observability/${e.target.value}`)}
            className="rounded-lg border border-border bg-white px-2.5 py-1.5 text-sm text-text-primary outline-none focus:border-pando-green/50 max-w-[12rem]"
            aria-label="Switch observability project"
          >
            {OBSERVABILITY_PROJECTS.map((p) => (
              <option key={p.id} value={p.id}>
                {p.name}
              </option>
            ))}
          </select>
        </label>
      </div>

      {hub.tabs?.length > 1 && (
        <div
          className="flex gap-1 shrink-0 p-1 rounded-xl border border-border bg-white w-fit"
          role="tablist"
          aria-label={`${hub.name} views`}
        >
          {hub.tabs.map((tab) => {
            const selected = tab.id === activeId
            return (
              <button
                key={tab.id}
                type="button"
                role="tab"
                aria-selected={selected}
                onClick={() => navigate(`/observability/${tab.id}`)}
                className="px-3 py-1.5 rounded-lg text-sm font-medium transition-colors"
                style={{
                  background: selected ? '#6C5CE7' : 'transparent',
                  color: selected ? '#fff' : undefined,
                }}
              >
                {tab.label}
              </button>
            )
          })}
        </div>
      )}

      <iframe
        key={activeId}
        title={tabTitle(hub, activeId)}
        src={src}
        className="w-full flex-1 min-h-0 border-0 rounded-xl bg-white"
      />
    </div>
  )
}
