import { useState, useEffect } from 'react'
import { useParams, useNavigate } from 'react-router-dom'
import { ArrowLeft, Download, Eye, EyeOff, Pause, Play } from 'lucide-react'
import { getRetriggerJob, triggerRetriggerFetchRecords, pauseRetriggerJob, resumeRetriggerJob } from '../api/client'
import StatusBadge from '../components/retrigger/StatusBadge'
import StepTracker from '../components/retrigger/StepTracker'

export default function RetriggerJobDetail() {
  const { jobId } = useParams()
  const navigate = useNavigate()
  const [job, setJob] = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)
  const [fetchingRecords, setFetchingRecords] = useState(false)
  const [pausingJob, setPausingJob] = useState(false)
  const [resumingJob, setResumingJob] = useState(false)
  const [showRecords, setShowRecords] = useState(false)
  const [autoRefresh, setAutoRefresh] = useState(true)

  useEffect(() => {
    loadJob()
  }, [jobId])

  useEffect(() => {
    if (!autoRefresh || !job) return
    if (job.status === 'running' || job.status === 'pending') {
      const interval = setInterval(loadJob, 2000)
      return () => clearInterval(interval)
    } else {
      setAutoRefresh(false)
    }
  }, [job, autoRefresh, jobId])

  async function loadJob() {
    try {
      const data = await getRetriggerJob(jobId)
      setJob(data)
      setError(null)
    } catch (err) {
      setError(err.message)
    } finally {
      setLoading(false)
    }
  }

  async function handleFetchRecords() {
    setFetchingRecords(true)
    try {
      await triggerRetriggerFetchRecords(jobId)
      setTimeout(loadJob, 1000)
    } catch (err) {
      setError(err.message)
    } finally {
      setFetchingRecords(false)
    }
  }

  async function handlePause() {
    setPausingJob(true)
    try {
      await pauseRetriggerJob(jobId)
      await loadJob()
    } catch (err) {
      setError(err.message)
    } finally {
      setPausingJob(false)
    }
  }

  async function handleResume() {
    setResumingJob(true)
    try {
      await resumeRetriggerJob(jobId)
      setAutoRefresh(true) // Re-enable auto-refresh
      await loadJob()
    } catch (err) {
      setError(err.message)
    } finally {
      setResumingJob(false)
    }
  }

  if (loading) {
    return (
      <div className="max-w-5xl mx-auto">
        <div className="bg-surface border border-border rounded-2xl p-8 shadow-card animate-pulse">
          <div className="h-6 w-48 bg-border rounded-lg mb-6" />
          <div className="h-40 w-full bg-background rounded-xl mb-6" />
          <div className="h-64 w-full bg-background rounded-xl" />
        </div>
      </div>
    )
  }

  if (error) {
    return (
      <div className="max-w-5xl mx-auto">
        <div className="bg-danger-bg border border-danger/20 rounded-xl p-6">
          <p className="text-danger text-sm font-medium mb-4">{error}</p>
          <button
            onClick={() => navigate('/retrigger')}
            className="text-danger text-sm underline hover:no-underline font-medium"
          >
            Back to Bulk Ops
          </button>
        </div>
      </div>
    )
  }

  if (!job) {
    return (
      <div className="max-w-5xl mx-auto">
        <div className="bg-surface border border-border rounded-2xl p-8 text-center">
          <p className="text-text-muted text-sm">Job not found</p>
          <button
            onClick={() => navigate('/retrigger')}
            className="mt-4 text-sm text-aivar-purple-500 underline hover:no-underline"
          >
            Back to Bulk Ops
          </button>
        </div>
      </div>
    )
  }

  const isComplete = job.status === 'completed' || job.status === 'failed'
  const hasRecords = job.records && job.records.length > 0

  return (
    <div className="max-w-5xl mx-auto">
      {/* Header */}
      <div className="mb-6">
        <button
          onClick={() => navigate('/retrigger')}
          className="flex items-center gap-2 text-text-muted hover:text-text-primary text-sm font-medium mb-4 transition-colors"
        >
          <ArrowLeft size={16} />
          Back to Bulk Ops
        </button>
        <div className="flex items-center justify-between">
          <div>
            <h2 className="text-text-primary font-bold text-lg mb-1">{job.job_id}</h2>
            <p className="text-text-muted text-sm">
              {(job.action || 'retrigger').replaceAll('_', ' ')} • {job.project_name} • Folder: {job.folder_name} • {job.invoice_numbers?.length || 0} invoice(s)
            </p>
          </div>
          <div className="flex items-center gap-2">
            {job.status === 'running' && (job.action || 'retrigger') === 'retrigger' && (
              <button
                onClick={handlePause}
                disabled={pausingJob}
                className="flex items-center gap-2 px-4 py-2 rounded-xl text-sm font-semibold transition-colors shadow-sm disabled:opacity-50"
                style={{ background: '#F59E0B', color: '#fff' }}
                onMouseEnter={(e) => !pausingJob && (e.currentTarget.style.background = '#D97706')}
                onMouseLeave={(e) => (e.currentTarget.style.background = '#F59E0B')}
              >
                <Pause size={14} />
                {pausingJob ? 'Pausing...' : 'Pause'}
              </button>
            )}
            {job.status === 'paused' && (job.action || 'retrigger') === 'retrigger' && (
              <button
                onClick={handleResume}
                disabled={resumingJob}
                className="flex items-center gap-2 px-4 py-2 rounded-xl text-sm font-semibold transition-colors shadow-sm disabled:opacity-50"
                style={{ background: '#10B981', color: '#fff' }}
                onMouseEnter={(e) => !resumingJob && (e.currentTarget.style.background = '#059669')}
                onMouseLeave={(e) => (e.currentTarget.style.background = '#10B981')}
              >
                <Play size={14} />
                {resumingJob ? 'Resuming...' : 'Resume'}
              </button>
            )}
            <StatusBadge status={job.status} />
          </div>
        </div>
      </div>

      {/* Summary Cards */}
      {job.summary && (
        <div className="grid grid-cols-2 md:grid-cols-4 gap-4 mb-6">
          <div className="bg-surface border border-border rounded-xl p-4">
            <p className="text-text-muted text-xs mb-1">PKs Found</p>
            <p className="text-text-primary text-2xl font-bold">{job.summary.fetch_pks || 0}</p>
          </div>
          <div className="bg-surface border border-border rounded-xl p-4">
            <p className="text-text-muted text-xs mb-1">Deleted</p>
            <p className="text-text-primary text-2xl font-bold">{job.summary.delete_records || 0}</p>
          </div>
          <div className="bg-surface border border-border rounded-xl p-4">
            <p className="text-text-muted text-xs mb-1">Triggered</p>
            <p className="text-text-primary text-2xl font-bold">{job.summary.reingest_files || 0}</p>
          </div>
          <div className="bg-surface border border-border rounded-xl p-4">
            <p className="text-text-muted text-xs mb-1">Failed</p>
            <p className="text-danger text-2xl font-bold">{job.summary.failed || 0}</p>
          </div>
        </div>
      )}

      {/* Error Alert */}
      {job.error && (
        <div className="bg-danger-bg border border-danger/20 rounded-xl p-4 mb-6">
          <p className="text-danger text-sm font-semibold mb-1">Error</p>
          <p className="text-danger text-sm font-mono">{job.error}</p>
        </div>
      )}

      {/* Step Tracker */}
      <div className="bg-surface border border-border rounded-2xl p-6 shadow-card mb-6">
        <h3 className="text-text-primary font-semibold text-base mb-4">Pipeline Progress</h3>
        <StepTracker steps={job.steps} />
      </div>

      {/* Logs */}
      <div className="bg-surface border border-border rounded-2xl p-6 shadow-card mb-6">
        <div className="flex items-center justify-between mb-4">
          <h3 className="text-text-primary font-semibold text-base">Logs</h3>
          {!isComplete && (
            <span className="text-xs text-text-muted">
              Auto-refreshing every 2s
            </span>
          )}
        </div>
        <div className="bg-background rounded-xl p-4 max-h-96 overflow-y-auto font-mono text-xs">
          {job.logs && job.logs.length > 0 ? (
            <div className="space-y-1">
              {job.logs.map((log, idx) => (
                <div key={idx} className="flex gap-3">
                  <span className="text-text-muted whitespace-nowrap">
                    {new Date(log.ts).toLocaleTimeString()}
                  </span>
                  <span
                    className={
                      log.level === 'error'
                        ? 'text-danger font-semibold'
                        : log.level === 'warning'
                        ? 'text-orange-500'
                        : 'text-text-secondary'
                    }
                  >
                    {log.msg}
                  </span>
                </div>
              ))}
            </div>
          ) : (
            <p className="text-text-muted">No logs yet</p>
          )}
        </div>
      </div>

      {/* Fetch Records Section */}
      {isComplete && (
        <div className="bg-surface border border-border rounded-2xl p-6 shadow-card mb-6">
          <div className="flex items-center justify-between mb-4">
            <div>
              <h3 className="text-text-primary font-semibold text-base mb-1">Records</h3>
              <p className="text-text-muted text-sm">
                Fetch the processed records from DynamoDB
              </p>
            </div>
            <div className="flex items-center gap-2">
              {hasRecords && (
                <button
                  onClick={() => setShowRecords(!showRecords)}
                  className="flex items-center gap-2 px-3 py-2 text-sm font-medium rounded-xl transition-colors bg-background border border-border text-text-secondary hover:bg-surface hover:text-text-primary"
                >
                  {showRecords ? <EyeOff size={14} /> : <Eye size={14} />}
                  {showRecords ? 'Hide' : 'Show'} JSON
                </button>
              )}
              <button
                onClick={handleFetchRecords}
                disabled={fetchingRecords}
                className="flex items-center gap-2 px-4 py-2 text-white rounded-xl text-sm font-semibold transition-colors shadow-sm disabled:opacity-50 bg-[#6C5CE7] hover:bg-[#5A4BD1]"
              >
                <Download size={14} />
                {fetchingRecords ? 'Fetching...' : 'Fetch Records'}
              </button>
            </div>
          </div>

          {hasRecords && showRecords && (
            <div className="bg-background rounded-xl p-4 max-h-96 overflow-auto">
              <pre className="text-xs font-mono text-text-secondary">
                {JSON.stringify(job.records, null, 2)}
              </pre>
            </div>
          )}

          {hasRecords && !showRecords && (
            <div className="bg-pando-green-50 border border-pando-green-200 rounded-xl p-4">
              <p className="text-pando-green-600 text-sm font-medium">
                ✓ {job.records.length} record(s) fetched
              </p>
            </div>
          )}
        </div>
      )}


      {/* Fetch artifacts */}
      {Array.isArray(job.artifacts) && job.artifacts.length > 0 && (
        <div className="bg-surface border border-border rounded-2xl p-6 shadow-card mb-6">
          <h3 className="text-text-primary font-semibold text-base mb-4">Saved files</h3>
          <p className="text-text-muted text-xs mb-3">
            Folder: {job.folder_name}{job.output_dir ? ` • ${job.output_dir}` : ''}
          </p>
          <div className="space-y-2">
            {job.artifacts.map((art, idx) => (
              <div
                key={`${art.invoice || 'inv'}-${idx}`}
                className="flex items-center justify-between gap-3 p-3 rounded-xl border border-border bg-background"
              >
                <div className="min-w-0">
                  <p className="text-sm font-medium text-text-primary truncate">
                    {art.invoice}{art.filename ? ` • ${art.filename}` : ''}
                  </p>
                  <p className="text-xs text-text-muted truncate">
                    {art.status}
                    {art.error ? ` — ${art.error}` : ''}
                    {art.s3_uri ? ` — ${art.s3_uri}` : ''}
                  </p>
                </div>
                {art.status === 'OK' && art.filename && (
                  <button
                    type="button"
                    className="text-xs font-semibold px-3 py-1.5 rounded-lg bg-aivar-purple-50 text-[#6C5CE7] hover:bg-aivar-purple-100"
                    onClick={() => {
                      const token = localStorage.getItem('pando_token')
                      fetch(`/api/retrigger/jobs/${encodeURIComponent(job.job_id)}/artifacts/${encodeURIComponent(art.filename)}`, {
                        headers: token ? { Authorization: `Bearer ${token}` } : {},
                      })
                        .then(async (res) => {
                          if (!res.ok) throw new Error(`Download failed (${res.status})`)
                          const blob = await res.blob()
                          const url = URL.createObjectURL(blob)
                          const a = document.createElement('a')
                          a.href = url
                          a.download = art.filename
                          a.click()
                          URL.revokeObjectURL(url)
                        })
                        .catch((err) => alert(err.message))
                    }}
                  >
                    Download
                  </button>
                )}
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Invoice Numbers */}
      <div className="bg-surface border border-border rounded-2xl p-6 shadow-card">
        <h3 className="text-text-primary font-semibold text-base mb-3">Invoice Numbers</h3>
        <div className="flex flex-wrap gap-2">
          {job.invoice_numbers?.map((inv, idx) => (
            <span
              key={idx}
              className="px-3 py-1.5 rounded-lg text-xs font-mono font-medium bg-background border border-border text-text-secondary"
            >
              {inv}
            </span>
          ))}
        </div>
      </div>

      <div className="mt-4 text-center text-xs text-text-muted">
        Created: {new Date(job.created_at).toLocaleString()} • Updated:{' '}
        {new Date(job.updated_at).toLocaleString()}
      </div>
    </div>
  )
}
