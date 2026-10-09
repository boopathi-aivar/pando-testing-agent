import { useEffect, useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { Search, Settings2, Trash2 } from 'lucide-react'
import { deleteObservabilityView, getObservabilityProjects } from '../api/client'
import ObservabilityViewForm from '../components/observability/ObservabilityViewForm'

export default function ObservabilityHome() {
  const [query, setQuery] = useState('')
  const [projects, setProjects] = useState([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)
  const [editingViewId, setEditingViewId] = useState(null)
  const [formKey, setFormKey] = useState(0)
  const navigate = useNavigate()

  async function load() {
    setLoading(true)
    setError(null)
    try {
      const data = await getObservabilityProjects()
      setProjects(data.projects || [])
    } catch (err) {
      setError(err.message)
      setProjects([])
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    load()
  }, [])

  const q = query.trim().toLowerCase()
  const filtered = projects.filter((p) => {
    if (!q) return true
    const hay = [p.name, p.id, p.description || '', p.table || ''].join(' ').toLowerCase()
    return hay.includes(q)
  })

  function openEdit(viewId) {
    setEditingViewId(viewId)
    setFormKey((k) => k + 1)
  }

  function resetForm() {
    setEditingViewId(null)
    setFormKey((k) => k + 1)
  }

  async function handleDelete(project) {
    if (!confirm(`Remove “${project.name}” from Observability?`)) return
    try {
      await deleteObservabilityView(project.id)
      if (editingViewId === project.id) resetForm()
      await load()
    } catch (err) {
      setError(err.message)
    }
  }

  function handleSaved(saved) {
    resetForm()
    load()
    if (saved?.view_id) {
      navigate(`/observability/${saved.view_id}`)
    }
  }

  return (
    <div>
      <div>
        <h1 className="text-2xl font-semibold text-text-primary tracking-tight">Observability</h1>
        <p className="mt-1 text-sm text-text-secondary">
          Add a DynamoDB table, pick columns and actions, or manage existing projects below.
        </p>
      </div>

      <section className="mt-6 rounded-2xl border border-border bg-surface p-5 shadow-card">
        <div className="mb-4">
          <h2 className="text-base font-semibold text-text-primary">
            {editingViewId ? 'Edit project' : 'Add Observability project'}
          </h2>
          <p className="text-sm text-text-secondary mt-0.5">
            Table name → discover fields → pick columns and actions.
          </p>
        </div>
        <ObservabilityViewForm
          key={formKey}
          viewId={editingViewId}
          onCancel={editingViewId ? resetForm : undefined}
          onSaved={handleSaved}
        />
      </section>

      {(projects.length > 0 || loading) && (
        <>
          <div className="mt-8 flex flex-wrap items-center justify-between gap-3">
            <h2 className="text-sm font-semibold text-text-primary">Projects</h2>
            {projects.length > 3 && (
              <div className="relative max-w-xs w-full sm:w-56">
                <Search
                  size={16}
                  className="absolute left-3 top-1/2 -translate-y-1/2 text-text-secondary pointer-events-none"
                />
                <input
                  type="search"
                  value={query}
                  onChange={(e) => setQuery(e.target.value)}
                  placeholder="Search…"
                  className="w-full pl-9 pr-3 py-2 rounded-xl text-sm"
                />
              </div>
            )}
          </div>

          {error && (
            <div className="mt-3 bg-danger-bg border border-danger/20 rounded-xl p-3 text-sm text-danger">
              {error}
            </div>
          )}

          {loading ? (
            <p className="mt-3 text-sm text-text-secondary">Loading projects…</p>
          ) : filtered.length === 0 ? (
            <p className="mt-3 text-sm text-text-secondary">
              {query.trim() ? `No projects match “${query.trim()}”.` : null}
            </p>
          ) : (
            <ul className="mt-3 grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
              {filtered.map((project) => (
                <li key={project.id} className="relative">
                  <Link
                    to={`/observability/${project.id}`}
                    className="block h-full px-4 py-3 pr-20 rounded-xl border border-border bg-surface hover:border-pando-green/40 hover:bg-background transition-colors"
                  >
                    <span className="font-medium text-text-primary">{project.name}</span>
                    <p className="text-sm text-text-secondary mt-0.5">
                      {project.description || (project.table ? `Table: ${project.table}` : '')}
                    </p>
                  </Link>
                  <div className="absolute top-2 right-2 flex gap-1">
                    <button
                      type="button"
                      title="Edit"
                      onClick={(e) => {
                        e.preventDefault()
                        openEdit(project.id)
                      }}
                      className="p-1.5 rounded-lg bg-surface border border-border hover:bg-background"
                    >
                      <Settings2 size={14} className="text-text-secondary" />
                    </button>
                    <button
                      type="button"
                      title="Remove"
                      onClick={(e) => {
                        e.preventDefault()
                        handleDelete(project)
                      }}
                      className="p-1.5 rounded-lg bg-surface border border-border hover:bg-background"
                    >
                      <Trash2 size={14} className="text-danger" />
                    </button>
                  </div>
                </li>
              ))}
            </ul>
          )}
        </>
      )}

      {!loading && projects.length === 0 && error && (
        <div className="mt-4 bg-danger-bg border border-danger/20 rounded-xl p-3 text-sm text-danger">
          {error}
        </div>
      )}
    </div>
  )
}
