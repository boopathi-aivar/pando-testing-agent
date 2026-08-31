import { useNavigate } from 'react-router-dom'
import { Settings, PlayCircle, Trash2, AlertTriangle, FolderOpen } from 'lucide-react'
import { useState } from 'react'
import { useProjects } from '../hooks/useProjects'
import { deleteProject } from '../api/client'

const STATUS_CONFIG = {
  configured:   { label: 'Configured',   cls: 'bg-pando-green-50 text-pando-green-600 border-pando-green-200' },
  incomplete:   { label: 'Incomplete',   cls: 'bg-warning-bg text-warning border-warning/30' },
  never_tested: { label: 'Never Tested', cls: 'bg-background text-text-muted border-border' },
}

function SkeletonRow() {
  return (
    <tr className="border-b border-border animate-pulse">
      <td className="px-5 py-4"><div className="h-4 w-36 bg-border rounded" /></td>
      <td className="px-5 py-4"><div className="h-3 w-28 bg-background rounded" /></td>
      <td className="px-5 py-4"><div className="h-3 w-48 bg-background rounded" /></td>
      <td className="px-5 py-4"><div className="h-5 w-20 bg-border rounded-full" /></td>
      <td className="px-5 py-4"><div className="h-3 w-24 bg-background rounded" /></td>
      <td className="px-5 py-4"><div className="h-8 w-28 bg-border rounded-xl" /></td>
    </tr>
  )
}

function DeleteConfirm({ project, onCancel, onDeleted }) {
  const [deleting, setDeleting] = useState(false)

  async function handleDelete() {
    setDeleting(true)
    try {
      await deleteProject(project.project_id)
      onDeleted(project.project_id)
    } catch {
      setDeleting(false)
    }
  }

  return (
    <div className="flex items-center gap-2">
      <span className="text-danger text-xs font-medium flex items-center gap-1">
        <AlertTriangle size={12} />
        Delete <strong>{project.project_name}</strong>?
      </span>
      <button
        onClick={onCancel}
        disabled={deleting}
        className="px-2.5 py-1 rounded-lg border border-border text-text-secondary text-xs font-medium hover:border-pando-green hover:text-pando-green transition-colors disabled:opacity-50"
      >
        Cancel
      </button>
      <button
        onClick={handleDelete}
        disabled={deleting}
        className="px-2.5 py-1 rounded-lg bg-danger hover:bg-red-700 text-white text-xs font-semibold transition-colors disabled:opacity-60 flex items-center gap-1"
      >
        {deleting
          ? <span className="w-3 h-3 border-2 border-white/40 border-t-white rounded-full animate-spin" />
          : <Trash2 size={11} />}
        {deleting ? 'Deleting…' : 'Delete'}
      </button>
    </div>
  )
}

function ProjectRow({ project, onDeleted }) {
  const navigate = useNavigate()
  const [confirming, setConfirming] = useState(false)
  const { label, cls } = STATUS_CONFIG[project.status] ?? STATUS_CONFIG.never_tested

  return (
    <tr className="border-b border-border hover:bg-background/70 transition-colors group">
      <td className="px-5 py-4">
        <span
          className="text-text-primary font-semibold text-sm cursor-pointer group-hover:text-pando-green transition-colors"
          onClick={() => navigate(`/project/${project.project_id}/results`)}
        >
          {project.project_name}
        </span>
      </td>
      <td className="px-5 py-4">
        <span className="font-mono text-xs text-text-muted">{project.project_id}</span>
      </td>
      <td className="px-5 py-4">
        <span className="font-mono text-xs text-text-secondary truncate max-w-[260px] block" title={project.cloudwatch_log_group}>
          {project.cloudwatch_log_group || <span className="italic text-text-muted not-italic font-sans">Not set</span>}
        </span>
      </td>
      <td className="px-5 py-4">
        <span className={`inline-flex items-center px-2.5 py-0.5 rounded-full text-[11px] font-semibold border ${cls}`}>
          {label}
        </span>
      </td>
      <td className="px-5 py-4">
        <span className="text-text-muted text-xs">
          {project.last_tested ?? <span className="italic">Never</span>}
        </span>
      </td>
      <td className="px-5 py-4">
        {confirming ? (
          <DeleteConfirm
            project={project}
            onCancel={() => setConfirming(false)}
            onDeleted={onDeleted}
          />
        ) : (
          <div className="flex items-center gap-2">
            <button
              onClick={() => navigate(`/project/${project.project_id}/configure`)}
              className="flex items-center gap-1.5 px-3 py-1.5 rounded-xl border border-border text-text-secondary text-xs font-semibold hover:border-pando-green hover:text-pando-green transition-colors"
            >
              <Settings size={12} />
              Edit Config
            </button>
            <button
              onClick={() => navigate(`/project/${project.project_id}/results`)}
              className="flex items-center gap-1.5 px-3 py-1.5 rounded-xl text-xs font-semibold transition-colors text-white"
              style={{ background: '#6C5CE7' }}
              onMouseEnter={(e) => { e.currentTarget.style.background = '#5A4BD1' }}
              onMouseLeave={(e) => { e.currentTarget.style.background = '#6C5CE7' }}
            >
              <PlayCircle size={12} />
              Results
            </button>
            <button
              onClick={() => setConfirming(true)}
              title="Delete project"
              className="w-7 h-7 flex items-center justify-center rounded-lg text-text-muted opacity-0 group-hover:opacity-100 hover:bg-danger-bg hover:text-danger transition-all duration-150"
            >
              <Trash2 size={13} />
            </button>
          </div>
        )}
      </td>
    </tr>
  )
}

export default function SettingsPage() {
  const { projects, loading, error, refetch } = useProjects()
  const navigate = useNavigate()
  const [deletedIds, setDeletedIds] = useState(new Set())

  function handleDeleted(id) {
    setDeletedIds((prev) => new Set([...prev, id]))
  }

  const visible = projects.filter((p) => !deletedIds.has(p.project_id))

  return (
    <div className="p-6 max-w-[1200px]">
      {/* Page header */}
      <div className="mb-6">
        <h1 className="text-text-primary font-bold text-lg">Settings</h1>
        <p className="text-text-muted text-sm mt-0.5">Manage configuration for all projects</p>
      </div>

      {/* Projects table card */}
      <div className="bg-white border border-border rounded-2xl shadow-card overflow-hidden">
        {/* Card header */}
        <div className="flex items-center justify-between px-5 py-4 border-b border-border">
          <div>
            <h2 className="text-text-primary font-semibold text-sm">Projects</h2>
            {!loading && (
              <p className="text-text-muted text-xs mt-0.5">{visible.length} project{visible.length !== 1 ? 's' : ''}</p>
            )}
          </div>
          <button
            onClick={() => navigate('/project/new/configure')}
            className="flex items-center gap-1.5 px-4 py-2 rounded-xl text-sm font-semibold text-white transition-colors shadow-sm"
            style={{ background: '#6C5CE7' }}
            onMouseEnter={(e) => { e.currentTarget.style.background = '#5A4BD1' }}
            onMouseLeave={(e) => { e.currentTarget.style.background = '#6C5CE7' }}
          >
            + New Project
          </button>
        </div>

        {error && (
          <div className="px-5 py-4 bg-danger-bg border-b border-danger/20 flex items-center justify-between">
            <p className="text-danger text-sm font-medium">Failed to load projects: {error}</p>
            <button onClick={refetch} className="text-danger text-sm underline hover:no-underline font-medium">Retry</button>
          </div>
        )}

        <div className="overflow-x-auto">
          <table className="w-full">
            <thead>
              <tr className="border-b border-border bg-background/60">
                <th className="px-5 py-3 text-left text-xs font-semibold text-text-muted uppercase tracking-wider">Project Name</th>
                <th className="px-5 py-3 text-left text-xs font-semibold text-text-muted uppercase tracking-wider">ID</th>
                <th className="px-5 py-3 text-left text-xs font-semibold text-text-muted uppercase tracking-wider">CloudWatch Log Group</th>
                <th className="px-5 py-3 text-left text-xs font-semibold text-text-muted uppercase tracking-wider">Status</th>
                <th className="px-5 py-3 text-left text-xs font-semibold text-text-muted uppercase tracking-wider">Last Tested</th>
                <th className="px-5 py-3 text-left text-xs font-semibold text-text-muted uppercase tracking-wider">Actions</th>
              </tr>
            </thead>
            <tbody>
              {loading
                ? Array.from({ length: 4 }).map((_, i) => <SkeletonRow key={i} />)
                : visible.length === 0
                ? (
                  <tr>
                    <td colSpan={6} className="px-5 py-16 text-center">
                      <div className="flex flex-col items-center gap-3">
                        <div className="w-12 h-12 rounded-2xl flex items-center justify-center" style={{ background: '#EEF2FF' }}>
                          <FolderOpen size={22} style={{ color: '#6C5CE7' }} />
                        </div>
                        <p className="text-text-secondary font-semibold text-sm">No projects yet</p>
                        <p className="text-text-muted text-xs">Create your first project to get started.</p>
                        <button
                          onClick={() => navigate('/project/new/configure')}
                          className="mt-1 px-4 py-2 rounded-xl text-sm font-semibold text-white"
                          style={{ background: '#6C5CE7' }}
                        >
                          + New Project
                        </button>
                      </div>
                    </td>
                  </tr>
                )
                : visible.map((p) => (
                  <ProjectRow key={p.project_id} project={p} onDeleted={handleDeleted} />
                ))
              }
            </tbody>
          </table>
        </div>
      </div>
    </div>
  )
}
