import { useState, useEffect } from 'react'
import { useNavigate } from 'react-router-dom'
import { RefreshCw, Plus, Clock } from 'lucide-react'
import {
  getRetriggerProjects,
  getRetriggerJobs,
  createRetriggerProject,
  updateRetriggerProject,
  deleteRetriggerProject,
  createRetriggerJob,
} from '../api/client'
import RetriggerProjectModal from '../components/retrigger/RetriggerProjectModal'
import StatusBadge from '../components/retrigger/StatusBadge'

export default function Retrigger() {
  const [projects, setProjects] = useState([])
  const [jobs, setJobs] = useState([])
  const [selectedProjectId, setSelectedProjectId] = useState('')
  const [folderName, setFolderName] = useState('')  // NEW
  const [invoiceNumbers, setInvoiceNumbers] = useState('')
  const [loading, setLoading] = useState(true)
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState(null)
  const [success, setSuccess] = useState(null)
  const [showProjectModal, setShowProjectModal] = useState(false)
  const [editingProject, setEditingProject] = useState(null)
  const navigate = useNavigate()

  useEffect(() => {
    loadData()
  }, [])

  async function loadData() {
    setLoading(true)
    setError(null)
    try {
      const [projectsData, jobsData] = await Promise.all([
        getRetriggerProjects(),
        getRetriggerJobs(),
      ])
      setProjects(projectsData)
      setJobs(jobsData)
      if (projectsData.length > 0 && !selectedProjectId) {
        setSelectedProjectId(projectsData[0].project_id)
      }
    } catch (err) {
      setError(err.message)
    } finally {
      setLoading(false)
    }
  }

  async function handleSubmit(e) {
    e.preventDefault()
    if (!selectedProjectId) {
      setError('Please select a project')
      return
    }

    if (!folderName.trim()) {
      setError('Please enter a folder name')
      return
    }

    const invoices = invoiceNumbers
      .split('\n')
      .map((line) => line.trim())
      .filter((line) => line.length > 0)

    if (invoices.length === 0) {
      setError('Please enter at least one invoice number')
      return
    }

    setSubmitting(true)
    setError(null)
    setSuccess(null)

    try {
      const job = await createRetriggerJob(selectedProjectId, invoices, folderName.trim())
      setSuccess(`Job ${job.job_id} created successfully!`)
      setInvoiceNumbers('')
      setFolderName('')
      await loadData()
      // Navigate to job detail after a brief delay
      setTimeout(() => navigate(`/retrigger/jobs/${job.job_id}`), 1500)
    } catch (err) {
      setError(err.message)
    } finally {
      setSubmitting(false)
    }
  }

  async function handleSaveProject(data) {
    try {
      if (editingProject) {
        await updateRetriggerProject(editingProject.project_id, data)
      } else {
        await createRetriggerProject(data)
      }
      setShowProjectModal(false)
      setEditingProject(null)
      await loadData()
    } catch (err) {
      throw err
    }
  }

  async function handleDeleteProject(projectId) {
    if (!confirm('Are you sure you want to delete this project?')) return
    try {
      await deleteRetriggerProject(projectId)
      await loadData()
      if (selectedProjectId === projectId) {
        setSelectedProjectId(projects.length > 1 ? projects[0].project_id : '')
      }
    } catch (err) {
      setError(err.message)
    }
  }

  const recentJobs = jobs.slice(0, 10)

  return (
    <div className="max-w-6xl mx-auto">
      <div className="mb-6">
        <div className="flex items-center gap-3 mb-2">
          <div className="w-10 h-10 rounded-xl flex items-center justify-center" style={{ background: '#EEF2FF' }}>
            <RefreshCw size={20} style={{ color: '#6C5CE7' }} />
          </div>
          <div>
            <h2 className="text-text-primary font-bold text-lg">Retrigger (Re-ingestion)</h2>
            <p className="text-text-muted text-sm">Re-process invoices by triggering S3 events</p>
          </div>
        </div>
      </div>

      {error && (
        <div className="bg-danger-bg border border-danger/20 rounded-xl p-4 mb-6">
          <p className="text-danger text-sm font-medium">{error}</p>
        </div>
      )}

      {success && (
        <div className="bg-pando-green-50 border border-pando-green-200 rounded-xl p-4 mb-6">
          <p className="text-pando-green-600 text-sm font-medium">{success}</p>
        </div>
      )}

      {loading ? (
        <div className="bg-surface border border-border rounded-2xl p-8 shadow-card animate-pulse">
          <div className="h-6 w-40 bg-border rounded-lg mb-6" />
          <div className="h-10 w-full bg-background rounded-xl mb-4" />
          <div className="h-32 w-full bg-background rounded-xl mb-4" />
          <div className="h-10 w-32 bg-border rounded-xl" />
        </div>
      ) : (
        <>
          {/* Main Form */}
          <div className="bg-surface border border-border rounded-2xl p-6 shadow-card mb-6">
            <div className="flex items-center justify-between mb-6">
              <h3 className="text-text-primary font-semibold text-base">Start Re-ingestion</h3>
              <button
                onClick={() => {
                  setEditingProject(null)
                  setShowProjectModal(true)
                }}
                className="flex items-center gap-2 px-3 py-1.5 text-sm font-medium rounded-lg transition-colors"
                style={{ background: '#EEF2FF', color: '#6C5CE7' }}
                onMouseEnter={(e) => { e.currentTarget.style.background = '#E0DEFF' }}
                onMouseLeave={(e) => { e.currentTarget.style.background = '#EEF2FF' }}
              >
                <Plus size={14} />
                New Project
              </button>
            </div>

            <form onSubmit={handleSubmit}>
              <div className="mb-4">
                <label className="block text-text-secondary text-sm font-medium mb-2">
                  Project
                </label>
                <div className="flex gap-2">
                  <select
                    value={selectedProjectId}
                    onChange={(e) => setSelectedProjectId(e.target.value)}
                    className="flex-1 text-sm"
                    required
                  >
                    <option value="">Select a project...</option>
                    {projects.map((proj) => (
                      <option key={proj.project_id} value={proj.project_id}>
                        {proj.project_name} ({proj.s3_bucket})
                      </option>
                    ))}
                  </select>
                  {selectedProjectId && (
                    <>
                      <button
                        type="button"
                        onClick={() => {
                          const proj = projects.find((p) => p.project_id === selectedProjectId)
                          setEditingProject(proj)
                          setShowProjectModal(true)
                        }}
                        className="px-3 py-2 text-sm font-medium rounded-xl transition-colors"
                        style={{ background: '#F5F5F7', color: '#8B8B99' }}
                        onMouseEnter={(e) => { e.currentTarget.style.background = '#EBEBEF' }}
                        onMouseLeave={(e) => { e.currentTarget.style.background = '#F5F5F7' }}
                      >
                        Edit
                      </button>
                      <button
                        type="button"
                        onClick={() => handleDeleteProject(selectedProjectId)}
                        className="px-3 py-2 text-sm font-medium rounded-xl transition-colors"
                        style={{ background: '#FEE', color: '#E53E3E' }}
                        onMouseEnter={(e) => { e.currentTarget.style.background = '#FDD' }}
                        onMouseLeave={(e) => { e.currentTarget.style.background = '#FEE' }}
                      >
                        Delete
                      </button>
                    </>
                  )}
                </div>
              </div>

              <div className="mb-4">
                <label className="block text-text-secondary text-sm font-medium mb-2">
                  Folder Name *
                </label>
                <input
                  type="text"
                  value={folderName}
                  onChange={(e) => setFolderName(e.target.value)}
                  placeholder="e.g., Averitt_Batch1, RXO_Week42, MAX_January"
                  className="w-full text-sm"
                  required
                />
                <p className="text-text-muted text-xs mt-2">
                  Used to organize and search this batch of invoices
                </p>
              </div>

              <div className="mb-6">
                <label className="block text-text-secondary text-sm font-medium mb-2">
                  Invoice Numbers (one per line)
                </label>
                <textarea
                  value={invoiceNumbers}
                  onChange={(e) => setInvoiceNumbers(e.target.value)}
                  placeholder="INV-001&#10;INV-002&#10;INV-003"
                  rows={8}
                  className="w-full text-sm font-mono"
                  required
                />
                <p className="text-text-muted text-xs mt-2">
                  Enter invoice numbers to re-process. Each invoice will be deleted from DynamoDB and
                  re-ingested via S3 event.
                </p>
              </div>

              <button
                type="submit"
                disabled={submitting || !selectedProjectId}
                className="px-6 py-2.5 text-white rounded-xl text-sm font-semibold transition-colors shadow-sm disabled:opacity-50 disabled:cursor-not-allowed"
                style={{ background: '#6C5CE7' }}
                onMouseEnter={(e) => !submitting && (e.currentTarget.style.background = '#5A4BD1')}
                onMouseLeave={(e) => (e.currentTarget.style.background = '#6C5CE7')}
              >
                {submitting ? 'Starting...' : 'Start Re-ingestion'}
              </button>
            </form>
          </div>

          {/* Recent Jobs */}
          <div className="bg-surface border border-border rounded-2xl p-6 shadow-card">
            <div className="flex items-center gap-2 mb-4">
              <Clock size={18} className="text-text-muted" />
              <h3 className="text-text-primary font-semibold text-base">Recent Jobs</h3>
            </div>

            {recentJobs.length === 0 ? (
              <p className="text-text-muted text-sm py-8 text-center">No jobs yet</p>
            ) : (
              <div className="space-y-3">
                {recentJobs.map((job) => (
                  <div
                    key={job.job_id}
                    onClick={() => navigate(`/retrigger/jobs/${job.job_id}`)}
                    className="flex items-center justify-between p-4 rounded-xl border border-border hover:border-aivar-purple-200 hover:bg-aivar-purple-50/30 transition-all cursor-pointer"
                  >
                    <div className="flex-1 min-w-0">
                      <div className="flex items-center gap-3 mb-1">
                        <p className="text-text-primary font-semibold text-sm">{job.job_id}</p>
                        <StatusBadge status={job.status} size="sm" />
                      </div>
                      <p className="text-text-muted text-xs">
                        {job.project_name} • {job.folder_name} • {job.invoice_numbers?.length || 0} invoice(s)
                      </p>
                    </div>
                    <div className="text-text-muted text-xs text-right ml-4">
                      {new Date(job.created_at).toLocaleString()}
                    </div>
                  </div>
                ))}
              </div>
            )}
          </div>
        </>
      )}

      {showProjectModal && (
        <RetriggerProjectModal
          project={editingProject}
          onClose={() => {
            setShowProjectModal(false)
            setEditingProject(null)
          }}
          onSave={handleSaveProject}
        />
      )}
    </div>
  )
}
