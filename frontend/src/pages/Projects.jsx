import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { FolderOpen, Plus } from 'lucide-react'
import { useProjects } from '../hooks/useProjects'
import ProjectCard from '../components/dashboard/ProjectCard'
import { deleteProject } from '../api/client'

function SkeletonCard() {
  return (
    <div className="bg-white border border-border rounded-2xl p-5 shadow-card animate-pulse">
      <div className="flex items-start justify-between mb-4">
        <div>
          <div className="h-4 w-32 bg-border rounded-lg mb-2" />
          <div className="h-3 w-20 bg-background rounded-lg" />
        </div>
        <div className="h-6 w-20 bg-border rounded-full" />
      </div>
      <div className="flex items-center gap-4 py-3 border-y border-border mb-4">
        <div className="w-[52px] h-[52px] rounded-full bg-border" />
        <div>
          <div className="h-2.5 w-14 bg-background rounded mb-2" />
          <div className="h-4 w-24 bg-border rounded" />
        </div>
      </div>
      <div className="flex gap-2">
        <div className="flex-1 h-9 bg-background rounded-xl" />
        <div className="flex-1 h-9 bg-border rounded-xl" />
      </div>
    </div>
  )
}

export default function Projects() {
  const { projects, loading, error, refetch } = useProjects()
  const [search, setSearch] = useState('')
  const [deletedIds, setDeletedIds] = useState(new Set())
  const navigate = useNavigate()

  async function handleDelete(projectId) {
    await deleteProject(projectId)
    setDeletedIds((prev) => new Set([...prev, projectId]))
  }

  const filtered = projects.filter(
    (p) =>
      !deletedIds.has(p.project_id) &&
      (p.project_name.toLowerCase().includes(search.toLowerCase()) ||
        p.project_id.toLowerCase().includes(search.toLowerCase()))
  )

  return (
    <div className="p-6 max-w-[1400px]">
      <div className="flex items-center justify-between mb-6">
        <div>
          <h2 className="text-text-primary font-bold text-base">All Projects</h2>
          <p className="text-text-muted text-xs mt-0.5">{projects.length} projects configured</p>
        </div>
        <div className="flex items-center gap-3">
          <input
            type="text"
            placeholder="Filter by name or ID..."
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            className="w-56 text-sm"
          />
          <button
            onClick={() => navigate('/project/new/configure')}
            className="flex items-center gap-2 px-4 py-2 text-white rounded-xl text-sm font-semibold transition-colors shadow-sm"
            style={{ background: '#6C5CE7' }}
            onMouseEnter={(e) => { e.currentTarget.style.background = '#5A4BD1' }}
            onMouseLeave={(e) => { e.currentTarget.style.background = '#6C5CE7' }}
          >
            <Plus size={14} />
            New Project
          </button>
        </div>
      </div>

      {error && (
        <div className="bg-danger-bg border border-danger/20 rounded-xl p-4 mb-6 flex items-center justify-between">
          <p className="text-danger text-sm font-medium">Failed to load projects: {error}</p>
          <button onClick={refetch} className="text-danger text-sm underline hover:no-underline font-medium">Retry</button>
        </div>
      )}

      {loading ? (
        <div className="grid grid-cols-3 gap-4">
          {Array.from({ length: 6 }).map((_, i) => <SkeletonCard key={i} />)}
        </div>
      ) : filtered.length === 0 ? (
        <div className="flex flex-col items-center justify-center py-20 text-text-muted">
          <div className="w-16 h-16 rounded-2xl flex items-center justify-center mb-4" style={{ background: '#EEF2FF', border: '2px solid #E0DEFF' }}>
            <FolderOpen size={28} style={{ color: '#6C5CE7' }} />
          </div>
          <p className="text-base font-semibold text-text-secondary">No projects found</p>
          <p className="text-sm mt-1">Try adjusting your search or create a new project.</p>
        </div>
      ) : (
        <div className="grid grid-cols-3 gap-4">
          {filtered.map((p) => (
            <ProjectCard key={p.project_id} project={p} onDelete={handleDelete} />
          ))}
        </div>
      )}
    </div>
  )
}
