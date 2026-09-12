import { useState, useEffect, useCallback } from 'react'
import { useNavigate } from 'react-router-dom'
import { BookOpen, Plus, Trash2, X, FileCode, ChevronRight } from 'lucide-react'
import { getDocProjects, createDocProject, deleteDocProject } from '../api/client'

function useDocProjects() {
  const [docs, setDocs] = useState([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)

  const refetch = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      setDocs(await getDocProjects())
    } catch (err) {
      setError(err.message)
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => { refetch() }, [refetch])
  return { docs, loading, error, refetch }
}

function NewDocProjectModal({ open, onClose, onCreated }) {
  const [projectName, setProjectName] = useState('')
  const [s3Path, setS3Path] = useState('')
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState(null)

  useEffect(() => {
    if (open) { setProjectName(''); setS3Path(''); setError(null); setSubmitting(false) }
  }, [open])

  if (!open) return null

  const valid = projectName.trim() && s3Path.trim()

  async function handleSubmit(e) {
    e.preventDefault()
    if (!valid || submitting) return
    setSubmitting(true)
    setError(null)
    try {
      const created = await createDocProject({
        project_name: projectName.trim(),
        s3_path: s3Path.trim(),
      })
      onCreated(created)
    } catch (err) {
      setError(err.message)
      setSubmitting(false)
    }
  }

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center p-4"
      style={{ background: 'rgba(13,17,23,0.55)' }}
      onClick={onClose}
    >
      <div
        className="bg-surface border border-border rounded-2xl shadow-card w-full max-w-md"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-center justify-between px-6 py-4 border-b border-border">
          <h3 className="text-text-primary font-bold text-base">New Documentation Project</h3>
          <button
            onClick={onClose}
            className="w-8 h-8 flex items-center justify-center rounded-lg text-text-muted hover:bg-background"
            aria-label="Close"
          >
            <X size={18} />
          </button>
        </div>

        <form onSubmit={handleSubmit} className="px-6 py-5 flex flex-col gap-4">
          <div>
            <label className="block text-text-secondary text-xs font-semibold mb-1.5">
              Project Name
            </label>
            <input
              type="text"
              autoFocus
              value={projectName}
              onChange={(e) => setProjectName(e.target.value)}
              placeholder="e.g. GE Freight Prompt Template"
              className="w-full text-sm"
            />
          </div>

          <div>
            <label className="block text-text-secondary text-xs font-semibold mb-1.5">
              S3 Path to Prompt Template
            </label>
            <input
              type="text"
              value={s3Path}
              onChange={(e) => setS3Path(e.target.value)}
              placeholder="s3://my-bucket/path/to/prompt_template.py"
              className="w-full text-sm font-mono"
            />
            <p className="text-text-muted text-[11px] mt-1.5">
              The S3 location of the prompt template file. Accepts s3://bucket/key or bucket/key.
            </p>
          </div>

          {error && (
            <div className="bg-danger-bg border border-danger/20 rounded-xl px-3 py-2">
              <p className="text-danger text-xs font-medium">{error}</p>
            </div>
          )}

          <div className="flex items-center justify-end gap-2 pt-1">
            <button
              type="button"
              onClick={onClose}
              className="px-4 py-2 text-sm font-semibold text-text-secondary rounded-xl hover:bg-background"
            >
              Cancel
            </button>
            <button
              type="submit"
              disabled={!valid || submitting}
              className="flex items-center gap-2 px-4 py-2 text-white rounded-xl text-sm font-semibold transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
              style={{ background: '#6C5CE7' }}
            >
              {submitting ? 'Creating…' : 'Create Project'}
            </button>
          </div>
        </form>
      </div>
    </div>
  )
}

function DocCard({ doc, onOpen, onDelete }) {
  const [deleting, setDeleting] = useState(false)

  async function handleDelete(e) {
    e.stopPropagation()
    if (deleting) return
    if (!window.confirm(`Delete documentation project "${doc.project_name}"?`)) return
    setDeleting(true)
    try {
      await onDelete(doc.doc_id)
    } catch {
      setDeleting(false)
    }
  }

  return (
    <div
      onClick={() => onOpen(doc.doc_id)}
      className="bg-surface border border-border rounded-2xl p-5 shadow-card cursor-pointer transition-all hover:border-[#6C5CE7]/40 hover:shadow-md group"
    >
      <div className="flex items-start justify-between mb-4">
        <div className="flex items-center gap-3 min-w-0">
          <div
            className="w-10 h-10 rounded-xl flex items-center justify-center flex-shrink-0"
            style={{ background: '#EEF2FF', border: '1px solid #E0DEFF' }}
          >
            <FileCode size={18} style={{ color: '#6C5CE7' }} />
          </div>
          <div className="min-w-0">
            <h3 className="text-text-primary font-semibold text-sm truncate">{doc.project_name}</h3>
            <p className="text-text-muted text-[11px] truncate font-mono">{doc.s3_path}</p>
          </div>
        </div>
        <button
          onClick={handleDelete}
          disabled={deleting}
          className="w-8 h-8 flex items-center justify-center rounded-lg text-text-muted hover:text-danger hover:bg-danger-bg opacity-0 group-hover:opacity-100 transition-opacity flex-shrink-0"
          aria-label="Delete"
        >
          <Trash2 size={15} />
        </button>
      </div>
      <div className="flex items-center justify-between pt-3 border-t border-border">
        <span className="text-text-muted text-xs">View documentation</span>
        <ChevronRight size={16} className="text-text-muted group-hover:text-[#6C5CE7] transition-colors" />
      </div>
    </div>
  )
}

export default function Documentation() {
  const { docs, loading, error, refetch } = useDocProjects()
  const [modalOpen, setModalOpen] = useState(false)
  const [deletedIds, setDeletedIds] = useState(new Set())
  const navigate = useNavigate()

  async function handleDelete(docId) {
    await deleteDocProject(docId)
    setDeletedIds((prev) => new Set([...prev, docId]))
  }

  const visible = docs.filter((d) => !deletedIds.has(d.doc_id))

  return (
    <div>
      <div className="flex items-center justify-between mb-6">
        <div>
          <h2 className="text-text-primary font-bold text-base">Documentation</h2>
          <p className="text-text-muted text-xs mt-0.5">
            {visible.length} documentation project{visible.length !== 1 ? 's' : ''}
          </p>
        </div>
        <button
          onClick={() => setModalOpen(true)}
          className="flex items-center gap-2 px-4 py-2 text-white rounded-xl text-sm font-semibold transition-colors shadow-sm"
          style={{ background: '#6C5CE7' }}
          onMouseEnter={(e) => { e.currentTarget.style.background = '#5A4BD1' }}
          onMouseLeave={(e) => { e.currentTarget.style.background = '#6C5CE7' }}
        >
          <Plus size={14} />
          New Project
        </button>
      </div>

      {error && (
        <div className="bg-danger-bg border border-danger/20 rounded-xl p-4 mb-6 flex items-center justify-between">
          <p className="text-danger text-sm font-medium">Failed to load documentation: {error}</p>
          <button onClick={refetch} className="text-danger text-sm underline hover:no-underline font-medium">Retry</button>
        </div>
      )}

      {loading ? (
        <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-5">
          {Array.from({ length: 3 }).map((_, i) => (
            <div key={i} className="bg-surface border border-border rounded-2xl p-5 shadow-card animate-pulse h-32" />
          ))}
        </div>
      ) : visible.length === 0 ? (
        <div className="flex flex-col items-center justify-center py-20 text-text-muted">
          <div className="w-16 h-16 rounded-2xl flex items-center justify-center mb-4" style={{ background: '#EEF2FF', border: '2px solid #E0DEFF' }}>
            <BookOpen size={28} style={{ color: '#6C5CE7' }} />
          </div>
          <p className="text-base font-semibold text-text-secondary">No documentation projects yet</p>
          <p className="text-sm mt-1">Create one to summarize a prompt template stored in S3.</p>
        </div>
      ) : (
        <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-5">
          {visible.map((d) => (
            <DocCard key={d.doc_id} doc={d} onOpen={(id) => navigate(`/documentation/${id}`)} onDelete={handleDelete} />
          ))}
        </div>
      )}

      <NewDocProjectModal
        open={modalOpen}
        onClose={() => setModalOpen(false)}
        onCreated={(created) => {
          setModalOpen(false)
          navigate(`/documentation/${created.doc_id}`)
        }}
      />
    </div>
  )
}
