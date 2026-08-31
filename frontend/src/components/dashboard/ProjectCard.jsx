import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { Settings, Trash2, AlertTriangle } from 'lucide-react'
import ScoreBadge from '../results/ScoreBadge'

const STATUS_CONFIG = {
  configured:   { label: 'Configured',   cls: 'bg-pando-green-50 text-pando-green-600 border-pando-green-200' },
  incomplete:   { label: 'Incomplete',   cls: 'bg-warning-bg text-warning border-warning/30' },
  never_tested: { label: 'Never Tested', cls: 'bg-background text-text-muted border-border' },
}

export default function ProjectCard({ project, onDelete }) {
  const navigate = useNavigate()
  const [confirming, setConfirming] = useState(false)
  const [deleting, setDeleting] = useState(false)

  const { label, cls } = STATUS_CONFIG[project.status] ?? STATUS_CONFIG.never_tested

  async function handleDelete(e) {
    e.stopPropagation()
    setDeleting(true)
    try {
      await onDelete(project.project_id)
    } catch {
      setDeleting(false)
      setConfirming(false)
    }
  }

  return (
    <div className="bg-white border border-border rounded-2xl shadow-card hover:shadow-card-hover hover:-translate-y-0.5 transition-all duration-200 flex flex-col group overflow-hidden">

      {/* Clickable body → results page */}
      <div
        onClick={() => navigate(`/project/${project.project_id}/results`)}
        className="p-5 flex flex-col gap-4 cursor-pointer flex-1"
      >
        {/* Header */}
        <div className="flex items-start justify-between gap-3">
          <div className="min-w-0">
            <h3 className="text-text-primary font-bold text-[15px] leading-tight truncate group-hover:text-pando-green transition-colors">
              {project.project_name}
            </h3>
            <p className="text-text-muted text-[11px] font-mono mt-0.5 truncate">{project.project_id}</p>
          </div>
          <div className="flex items-center gap-2 flex-shrink-0">
            <span className={`inline-flex items-center px-2.5 py-0.5 rounded-full text-[11px] font-semibold border ${cls}`}>
              {label}
            </span>
            <button
              onClick={(e) => { e.stopPropagation(); setConfirming(true) }}
              title="Delete project"
              className="w-6 h-6 flex items-center justify-center rounded-lg text-text-muted opacity-0 group-hover:opacity-100 hover:bg-danger-bg hover:text-danger transition-all duration-150"
            >
              <Trash2 size={13} />
            </button>
          </div>
        </div>

        {/* Score row */}
        <div className="flex items-center gap-4 py-2 border-y border-border">
          <ScoreBadge score={project.last_score} size={52} />
          <div>
            <p className="text-text-muted text-[11px] uppercase tracking-wider font-medium">Last Test</p>
            <p className="text-text-primary text-sm font-semibold mt-0.5">
              {project.last_tested ?? <span className="text-text-muted font-normal italic">Never tested</span>}
            </p>
          </div>
        </div>
      </div>

      {/* Actions — fixed at bottom, not part of the clickable area */}
      <div className="px-5 pb-5">
        {confirming ? (
          <div className="flex flex-col gap-2">
            <div className="flex items-center gap-2 px-3 py-2 bg-danger-bg border border-danger/20 rounded-xl">
              <AlertTriangle size={13} className="text-danger flex-shrink-0" />
              <p className="text-danger text-xs font-medium leading-tight">
                Delete <span className="font-bold">{project.project_name}</span> and all its results?
              </p>
            </div>
            <div className="flex items-center gap-2">
              <button
                onClick={() => setConfirming(false)}
                disabled={deleting}
                className="flex-1 py-2 rounded-xl border border-border text-text-secondary text-sm font-medium hover:border-pando-green hover:text-pando-green transition-colors disabled:opacity-50"
              >
                Cancel
              </button>
              <button
                onClick={handleDelete}
                disabled={deleting}
                className="flex-1 py-2 rounded-xl bg-danger hover:bg-red-700 text-white text-sm font-semibold transition-colors disabled:opacity-60 flex items-center justify-center gap-1.5"
              >
                {deleting
                  ? <span className="w-4 h-4 border-2 border-white/40 border-t-white rounded-full animate-spin" />
                  : <Trash2 size={13} />}
                {deleting ? 'Deleting…' : 'Delete'}
              </button>
            </div>
          </div>
        ) : (
          <button
            onClick={(e) => { e.stopPropagation(); navigate(`/project/${project.project_id}/configure`) }}
            className="w-full flex items-center justify-center gap-1.5 py-2 rounded-xl border border-border text-text-secondary text-sm font-medium hover:border-pando-green hover:text-pando-green transition-colors"
          >
            <Settings size={13} />
            Configure
          </button>
        )}
      </div>
    </div>
  )
}
