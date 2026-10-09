import { useCallback, useEffect, useMemo, useState } from 'react'
import {
  AlertTriangle,
  Check,
  ChevronLeft,
  ChevronRight,
  Copy,
  ExternalLink,
  FileText,
  Loader2,
  Maximize2,
  Minimize2,
  RefreshCw,
  Settings2,
  X,
} from 'lucide-react'
import {
  getObservabilityCloudwatchLink,
  getObservabilityInvoiceDetail,
  getObservabilityInvoices,
  getToken,
  retriggerObservabilityInvoice,
} from '../api/client'
import ObservabilityViewModal from '../components/observability/ObservabilityViewModal'

const API_BASE = import.meta.env.VITE_API_URL || ''
const PAGE_SIZE = 20
const TABLE_COLUMN_LIMIT = 5

/** Checklist / DDB names → detail API (parse_attachment) keys */
const FIELD_ALIASES = {
  error: 'error_message',
  textract_classification_result: 'textract_carrier',
  image_model_classification_result: 'vision_carrier',
  stage_validation: 'missing_fields',
}

function humanLabel(id) {
  return String(id || '')
    .replace(/_/g, ' ')
    .replace(/\b\w/g, (c) => c.toUpperCase())
}

function isEmptyValue(v) {
  if (v == null) return true
  if (v === '') return true
  if (Array.isArray(v) && v.length === 0) return true
  if (typeof v === 'object' && !Array.isArray(v) && Object.keys(v).length === 0) return true
  return false
}

function resolveValue(source, fieldId) {
  if (!source) return undefined
  const direct = source[fieldId]
  if (!isEmptyValue(direct)) return direct
  const alias = FIELD_ALIASES[fieldId]
  if (alias) {
    const aliased = source[alias]
    if (!isEmptyValue(aliased)) return aliased
  }
  // Common fallbacks for checklist ids that map to identity fields
  if (fieldId === 'pk' && source.email_id) return `EMAIL#${source.email_id}`
  if (fieldId === 'sk' && source.attachment_id) return `ATTACHMENT#${source.attachment_id}`
  return direct
}

function formatDisplay(val) {
  if (val == null || val === '') return null
  if (Array.isArray(val)) return val.length ? val.join(', ') : null
  if (typeof val === 'object') {
    try {
      return JSON.stringify(val, null, 2)
    } catch {
      return String(val)
    }
  }
  return String(val)
}

function cellValue(row, colId) {
  return formatDisplay(resolveValue(row, colId)) || '—'
}

function statusClass(status) {
  const s = (status || '').toLowerCase()
  if (s.includes('success') || s === 'completed' || s === 'done') {
    return 'bg-emerald-500/15 text-emerald-600'
  }
  if (s.includes('fail') || s.includes('error')) return 'bg-red-500/15 text-red-600'
  if (s.includes('process') || s.includes('pending')) return 'bg-amber-500/15 text-amber-700'
  return 'bg-background text-text-secondary'
}

function StatusPill({ value }) {
  const text = formatDisplay(value) || '—'
  return (
    <span className={`inline-block px-2 py-0.5 rounded-md text-xs font-medium ${statusClass(text)}`}>
      {text}
    </span>
  )
}

/** Shared length rules for layout + expand (no field-name hardcoding). */
const EXPAND_THRESHOLD = 120
const WIDE_THRESHOLD = 48
const PREVIEW_CHARS = 280

function valueNeedsExpand(text, value) {
  if (value != null && typeof value === 'object') return true
  if (!text) return false
  return text.length > EXPAND_THRESHOLD || text.includes('\n')
}

function valueNeedsWide(text, value) {
  if (value != null && typeof value === 'object') return true
  if (!text) return false
  return text.length > WIDE_THRESHOLD || text.includes('\n')
}

function ExpandableValue({ text, needsExpand }) {
  const [expanded, setExpanded] = useState(false)
  const [copied, setCopied] = useState(false)
  if (!text) return <span className="text-text-secondary">—</span>

  const preview =
    !expanded && needsExpand && text.length > PREVIEW_CHARS
      ? `${text.slice(0, PREVIEW_CHARS)}…`
      : text

  async function copy() {
    try {
      await navigator.clipboard.writeText(text)
      setCopied(true)
      setTimeout(() => setCopied(false), 1500)
    } catch {
      /* ignore */
    }
  }

  if (!needsExpand) {
    return <span className="text-sm text-text-primary break-words">{text}</span>
  }

  return (
    <div className="min-w-0 space-y-2">
      <div className="relative">
        <pre
          className={`text-xs text-text-primary whitespace-pre-wrap break-all font-mono bg-background rounded-lg px-3 py-2.5 border border-border ${
            expanded ? 'max-h-[min(48vh,440px)] overflow-y-auto' : 'max-h-28 overflow-hidden'
          }`}
        >
          {preview}
        </pre>
        <button
          type="button"
          onClick={copy}
          title="Copy"
          className="absolute top-2 right-2 p-1.5 rounded-md bg-surface border border-border text-text-secondary hover:text-text-primary shadow-sm"
        >
          {copied ? <Check size={12} className="text-emerald-600" /> : <Copy size={12} />}
        </button>
      </div>
      <button
        type="button"
        onClick={() => setExpanded((v) => !v)}
        className="inline-flex items-center gap-1.5 text-xs font-semibold px-2.5 py-1 rounded-lg border border-border hover:bg-background"
        style={{ color: '#6C5CE7' }}
      >
        {expanded ? <Minimize2 size={12} /> : <Maximize2 size={12} />}
        {expanded ? 'Collapse' : 'Expand'}
      </button>
    </div>
  )
}

function DetailFieldCard({ fieldId, value }) {
  const label = humanLabel(fieldId)
  const isStatus = fieldId === 'status' || fieldId.endsWith('_status')
  const display = formatDisplay(value)
  const wide = valueNeedsWide(display, value)
  const needsExpand = valueNeedsExpand(display, value)

  return (
    <div
      className={`rounded-xl border border-border bg-background/50 px-3.5 py-3 min-w-0 ${
        wide ? 'sm:col-span-2' : ''
      }`}
    >
      <div className="text-[11px] font-medium uppercase tracking-wide text-text-secondary mb-1.5">
        {label}
      </div>
      {isStatus ? (
        <StatusPill value={value} />
      ) : display == null ? (
        <span className="text-sm text-text-secondary">—</span>
      ) : (
        <ExpandableValue text={display} needsExpand={needsExpand} />
      )}
    </div>
  )
}

export default function ObservabilityDynamic({ projectId, projectName }) {
  const [columns, setColumns] = useState([])
  const [actions, setActions] = useState({
    view_pdf: true,
    cloudwatch: true,
    retrigger: true,
  })
  const [items, setItems] = useState([])
  const [total, setTotal] = useState(0)
  const [page, setPage] = useState(1)
  const [totalPages, setTotalPages] = useState(1)
  const [invoiceNo, setInvoiceNo] = useState('')
  const [invoiceNoApplied, setInvoiceNoApplied] = useState('')
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)

  const [detail, setDetail] = useState(null)
  const [detailLoading, setDetailLoading] = useState(false)
  const [actionBusy, setActionBusy] = useState(null)
  const [retriggerConfirm, setRetriggerConfirm] = useState(false)
  const [retriggerError, setRetriggerError] = useState(null)
  const [retriggerSuccess, setRetriggerSuccess] = useState(null)
  const [pdfOpen, setPdfOpen] = useState(false)
  const [pdfExpanded, setPdfExpanded] = useState(false)
  const [pdfUrl, setPdfUrl] = useState(null) // bare blob URL (for download / print)
  const [pdfTitle, setPdfTitle] = useState('Invoice PDF')
  const [pdfError, setPdfError] = useState(null)
  const [pdfLoading, setPdfLoading] = useState(false)
  const [editOpen, setEditOpen] = useState(false)

  const tableColumns = useMemo(
    () => columns.slice(0, TABLE_COLUMN_LIMIT),
    [columns],
  )

  const load = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const data = await getObservabilityInvoices(projectId, {
        page,
        page_size: PAGE_SIZE,
        invoice_number: invoiceNoApplied.trim() || undefined,
      })
      setItems(data.items || [])
      setTotal(data.total || 0)
      setTotalPages(data.total_pages || 1)
      if (data.columns?.length) setColumns(data.columns)
      if (data.actions) setActions(data.actions)
    } catch (err) {
      setError(err.message)
      setItems([])
    } finally {
      setLoading(false)
    }
  }, [projectId, page, invoiceNoApplied])

  useEffect(() => {
    load()
  }, [load])

  useEffect(() => {
    const t = setTimeout(() => {
      setInvoiceNoApplied(invoiceNo)
      setPage(1)
    }, 300)
    return () => clearTimeout(t)
  }, [invoiceNo])

  useEffect(() => {
    setPage(1)
    setInvoiceNo('')
    setInvoiceNoApplied('')
  }, [projectId])

  async function openDetail(row) {
    if (!row?.email_id || !row?.attachment_id) return
    setDetailLoading(true)
    setDetail(null)
    setRetriggerConfirm(false)
    setRetriggerError(null)
    setRetriggerSuccess(null)
    try {
      const d = await getObservabilityInvoiceDetail(
        projectId,
        row.email_id,
        row.attachment_id,
      )
      setDetail(d)
    } catch (err) {
      setError(err.message)
    } finally {
      setDetailLoading(false)
    }
  }

  async function handleCloudwatch(row) {
    setActionBusy('cw')
    try {
      const res = await getObservabilityCloudwatchLink(
        projectId,
        row.email_id,
        row.attachment_id,
      )
      if (res?.url) window.open(res.url, '_blank', 'noopener,noreferrer')
      else setError(res?.detail || 'No CloudWatch link found')
    } catch (err) {
      setError(err.message)
    } finally {
      setActionBusy(null)
    }
  }

  function toggleRetriggerConfirm() {
    setRetriggerError(null)
    setRetriggerSuccess(null)
    setRetriggerConfirm((v) => !v)
  }

  async function executeRetrigger(emailId) {
    if (!emailId) return
    setActionBusy('retrigger')
    setRetriggerError(null)
    try {
      const result = await retriggerObservabilityInvoice(projectId, emailId)
      const n = result?.deleted_count ?? 0
      setRetriggerConfirm(false)
      setRetriggerSuccess(
        `Re-triggered successfully — ${n} record${n !== 1 ? 's' : ''} deleted, pipeline restarted.`,
      )
      await load()
      setTimeout(() => setRetriggerSuccess(null), 6000)
    } catch (err) {
      setRetriggerError(err.message || 'Re-trigger failed')
    } finally {
      setActionBusy(null)
    }
  }

  async function handlePdf(row) {
    if (!row?.email_id || !row?.attachment_id) return
    setActionBusy('pdf')
    setRetriggerConfirm(false)
    setPdfLoading(true)
    setPdfError(null)
    setPdfTitle('Loading PDF…')
    if (pdfUrl) {
      URL.revokeObjectURL(pdfUrl)
      setPdfUrl(null)
    }
    // Open modal shell immediately (same pattern as Delicato)
    setPdfOpen(true)
    setPdfExpanded(false)
    try {
      const token = getToken()
      const headers = {}
      if (token) headers.Authorization = `Bearer ${token}`
      const path =
        `${API_BASE}/api/observability/${encodeURIComponent(projectId)}` +
        `/invoices/${encodeURIComponent(row.email_id)}/${encodeURIComponent(row.attachment_id)}/pdf`
      const res = await fetch(path, { headers })
      if (!res.ok) {
        let detailMsg = `Failed to load PDF (${res.status})`
        try {
          const j = await res.json()
          if (j?.detail) {
            detailMsg = typeof j.detail === 'string' ? j.detail : JSON.stringify(j.detail)
          }
        } catch {
          /* ignore */
        }
        throw new Error(detailMsg)
      }
      const blob = await res.blob()
      setPdfUrl(URL.createObjectURL(blob))
      const cd = res.headers.get('Content-Disposition') || ''
      const m = /filename="?([^"]+)"?/i.exec(cd)
      setPdfTitle(m ? m[1] : formatDisplay(resolveValue(row, 'filename')) || 'Invoice PDF')
    } catch (err) {
      setPdfError(err.message || 'Failed to load PDF')
      setPdfTitle('Invoice PDF')
    } finally {
      setPdfLoading(false)
      setActionBusy(null)
    }
  }

  function closePdfModal() {
    if (pdfUrl) URL.revokeObjectURL(pdfUrl)
    setPdfOpen(false)
    setPdfExpanded(false)
    setPdfUrl(null)
    setPdfError(null)
    setPdfLoading(false)
    setPdfTitle('Invoice PDF')
  }

  useEffect(() => {
    return () => {
      if (pdfUrl) URL.revokeObjectURL(pdfUrl)
    }
  }, [pdfUrl])

  const colSpan = tableColumns.length + 1
  const detailTitle =
    formatDisplay(resolveValue(detail, 'invoice_number')) ||
    formatDisplay(resolveValue(detail, 'filename')) ||
    projectName ||
    'Detail'

  return (
    <div className="flex flex-col flex-1 min-h-0 gap-3">
      <div className="shrink-0 flex flex-wrap items-end gap-4 rounded-xl border border-border bg-surface px-5 py-3.5 shadow-sm">
        <div className="flex flex-col gap-1.5 min-w-[12rem] flex-1 max-w-xs">
          <label htmlFor="obs-invoice-filter" className="text-[11px] font-semibold uppercase tracking-wide text-text-secondary">
            Invoice No
          </label>
          <input
            id="obs-invoice-filter"
            type="search"
            value={invoiceNo}
            onChange={(e) => setInvoiceNo(e.target.value)}
            placeholder="Filter invoice number"
            autoComplete="off"
            className="h-[34px] w-full text-[13px] rounded-md border border-border bg-surface px-2.5 outline-none focus:border-[#7c3aed]"
          />
        </div>

        <div className="flex items-center gap-2 ml-auto pb-0.5">
          <button
            type="button"
            onClick={load}
            className="inline-flex items-center gap-1.5 h-[34px] px-3 rounded-md text-[13px] font-medium border border-border hover:bg-background"
          >
            <RefreshCw size={14} />
            Refresh
          </button>
          <button
            type="button"
            onClick={() => setEditOpen(true)}
            className="inline-flex items-center gap-1.5 h-[34px] px-3 rounded-md text-[13px] font-medium border border-border hover:bg-background"
          >
            <Settings2 size={14} />
            Edit columns
          </button>
        </div>
      </div>

      {error && (
        <div className="bg-danger-bg border border-danger/20 rounded-xl px-3 py-2 text-sm text-danger shrink-0">
          {error}
        </div>
      )}

      <div className="flex-1 min-h-0 overflow-auto rounded-xl border border-border bg-surface">
        <table className="w-full text-sm text-left">
          <thead className="sticky top-0 bg-surface border-b border-border z-10">
            <tr>
              {tableColumns.map((c) => (
                <th
                  key={c.id}
                  className="px-3 py-2.5 font-medium text-text-secondary whitespace-nowrap"
                >
                  {humanLabel(c.label || c.id)}
                </th>
              ))}
              <th className="px-3 py-2.5 font-medium text-text-secondary w-24">Details</th>
            </tr>
          </thead>
          <tbody>
            {loading ? (
              <tr>
                <td colSpan={colSpan} className="px-3 py-10 text-center text-text-secondary">
                  <span className="inline-flex items-center gap-2">
                    <Loader2 size={16} className="animate-spin" />
                    Loading…
                  </span>
                </td>
              </tr>
            ) : items.length === 0 ? (
              <tr>
                <td colSpan={colSpan} className="px-3 py-10 text-center text-text-secondary">
                  No records
                </td>
              </tr>
            ) : (
              items.map((row) => (
                <tr
                  key={`${row.email_id}-${row.attachment_id}`}
                  className="border-t border-border hover:bg-background/60"
                >
                  {tableColumns.map((c) => (
                    <td key={c.id} className="px-3 py-2 text-text-primary max-w-[14rem] truncate">
                      {c.id === 'status' ? (
                        <StatusPill value={resolveValue(row, c.id)} />
                      ) : (
                        cellValue(row, c.id)
                      )}
                    </td>
                  ))}
                  <td className="px-3 py-2">
                    <button
                      type="button"
                      onClick={() => openDetail(row)}
                      className="text-xs font-medium px-2.5 py-1 rounded-lg border border-border hover:border-[#6C5CE7]/40"
                      style={{ color: '#6C5CE7' }}
                    >
                      Details
                    </button>
                  </td>
                </tr>
              ))
            )}
          </tbody>
        </table>
      </div>

      <div className="flex items-center gap-2 shrink-0 text-sm text-text-secondary">
        <button
          type="button"
          disabled={page <= 1}
          onClick={() => setPage((p) => Math.max(1, p - 1))}
          className="p-1.5 rounded-lg border border-border disabled:opacity-40"
        >
          <ChevronLeft size={16} />
        </button>
        <span>
          Page {page} of {totalPages} · {total} total
        </span>
        <button
          type="button"
          disabled={page >= totalPages}
          onClick={() => setPage((p) => p + 1)}
          className="p-1.5 rounded-lg border border-border disabled:opacity-40"
        >
          <ChevronRight size={16} />
        </button>
      </div>

      {(detail || detailLoading) && (
        <div
          className="fixed inset-0 z-50 flex items-center justify-center bg-black/45 p-3 sm:p-6"
          onClick={() => setDetail(null)}
        >
          <div
            className="w-full max-w-3xl max-h-[min(92vh,880px)] bg-surface rounded-2xl border border-border shadow-2xl flex flex-col overflow-hidden"
            onClick={(e) => e.stopPropagation()}
            role="dialog"
            aria-modal="true"
            aria-label="Invoice details"
          >
            <div className="flex items-start gap-3 px-5 py-4 border-b border-border shrink-0">
              <div className="min-w-0 flex-1">
                <p className="text-[11px] uppercase tracking-wide text-text-secondary">Invoice detail</p>
                <h2 className="font-semibold text-text-primary truncate text-base mt-0.5">
                  {detailLoading ? 'Loading…' : detailTitle}
                </h2>
                {detail?.status && (
                  <div className="mt-2">
                    <StatusPill value={detail.status} />
                  </div>
                )}
              </div>
              <div className="flex flex-wrap items-center gap-1.5 shrink-0 justify-end">
                {detail && actions.view_pdf && (
                  <button
                    type="button"
                    disabled={actionBusy === 'pdf'}
                    onClick={() => handlePdf(detail)}
                    className="inline-flex items-center gap-1.5 text-xs font-medium px-2.5 py-1.5 rounded-lg border border-border hover:bg-background disabled:opacity-50"
                  >
                    <FileText size={13} />
                    View PDF
                  </button>
                )}
                {detail && actions.cloudwatch && (
                  <button
                    type="button"
                    disabled={actionBusy === 'cw'}
                    onClick={() => handleCloudwatch(detail)}
                    className="inline-flex items-center gap-1.5 text-xs font-medium px-2.5 py-1.5 rounded-lg border border-border hover:bg-background disabled:opacity-50"
                  >
                    <ExternalLink size={13} />
                    CloudWatch
                  </button>
                )}
                {detail && actions.retrigger && detail.email_id && (
                  <button
                    type="button"
                    disabled={actionBusy === 'retrigger'}
                    onClick={toggleRetriggerConfirm}
                    className={`inline-flex items-center gap-1.5 text-xs font-medium px-2.5 py-1.5 rounded-lg border hover:bg-background disabled:opacity-50 ${
                      retriggerConfirm
                        ? 'border-amber-500/50 text-amber-700 bg-amber-50'
                        : 'border-border'
                    }`}
                  >
                    <RefreshCw size={13} className={actionBusy === 'retrigger' ? 'animate-spin' : ''} />
                    {actionBusy === 'retrigger' ? 'Retriggering…' : 'Re-trigger'}
                  </button>
                )}
                <button
                  type="button"
                  onClick={() => setDetail(null)}
                  className="w-8 h-8 rounded-lg flex items-center justify-center hover:bg-background"
                  aria-label="Close"
                >
                  <X size={16} />
                </button>
              </div>
            </div>

            <div className="flex-1 overflow-y-auto px-5 py-4 space-y-4">
              {detailLoading && (
                <p className="text-sm text-text-secondary inline-flex items-center gap-2">
                  <Loader2 size={14} className="animate-spin" /> Loading…
                </p>
              )}
              {detail && (
                <>
                  {retriggerConfirm && detail.email_id && (
                    <div className="flex flex-col sm:flex-row gap-3 rounded-xl border-[1.5px] border-amber-500 bg-amber-50 p-3.5">
                      <div className="text-amber-600 shrink-0 mt-0.5">
                        <AlertTriangle size={18} />
                      </div>
                      <div className="min-w-0 flex-1">
                        <p className="text-sm font-bold text-amber-900 m-0 mb-1.5">
                          Re-trigger invoice processing?
                        </p>
                        <div className="inline-flex items-center gap-1.5 max-w-full mb-2 px-2 py-1 rounded-md bg-amber-100 border border-amber-300 overflow-hidden">
                          <span className="text-[10px] font-bold tracking-wide text-white bg-amber-500 px-1.5 py-0.5 rounded shrink-0">
                            PK
                          </span>
                          <span className="text-[11px] font-mono text-amber-950 break-all">
                            EMAIL#{detail.email_id}
                          </span>
                        </div>
                        <ul className="m-0 pl-4 text-xs text-amber-950 leading-relaxed list-disc">
                          <li>All DynamoDB records under this PK will be deleted</li>
                          <li>The original file will be re-uploaded to S3 to restart the pipeline</li>
                          <li>This action cannot be undone</li>
                        </ul>
                        {retriggerError && (
                          <p className="mt-2 text-xs font-medium text-red-600 m-0">{retriggerError}</p>
                        )}
                      </div>
                      <div className="flex sm:flex-col gap-2 shrink-0">
                        <button
                          type="button"
                          disabled={actionBusy === 'retrigger'}
                          onClick={() => setRetriggerConfirm(false)}
                          className="px-3.5 py-1.5 rounded-lg text-xs font-semibold border border-gray-300 bg-white text-gray-700 hover:bg-gray-50 disabled:opacity-50"
                        >
                          Cancel
                        </button>
                        <button
                          type="button"
                          disabled={actionBusy === 'retrigger'}
                          onClick={() => executeRetrigger(detail.email_id)}
                          className="px-3.5 py-1.5 rounded-lg text-xs font-semibold border border-amber-700 bg-amber-600 text-white hover:bg-amber-700 disabled:opacity-60"
                        >
                          {actionBusy === 'retrigger'
                            ? 'Retriggering…'
                            : retriggerError
                              ? 'Retry'
                              : 'Confirm'}
                        </button>
                      </div>
                    </div>
                  )}

                  {retriggerSuccess && (
                    <div className="flex items-center gap-2 rounded-xl border-[1.5px] border-emerald-300 bg-emerald-50 px-3.5 py-2.5 text-sm font-semibold text-emerald-700">
                      <Check size={15} className="shrink-0" />
                      {retriggerSuccess}
                    </div>
                  )}

                  <div>
                    <p className="text-xs font-medium text-text-secondary mb-3">
                      Selected fields
                      {columns.length > TABLE_COLUMN_LIMIT
                        ? ` (${columns.length}; first ${TABLE_COLUMN_LIMIT} shown in table)`
                        : ` (${columns.length})`}
                    </p>
                    {columns.length === 0 ? (
                      <p className="text-sm text-text-secondary">
                        No columns configured. Use Edit columns to pick fields.
                      </p>
                    ) : (
                      <div className="grid sm:grid-cols-2 gap-3">
                        {columns.map((c) => (
                          <DetailFieldCard
                            key={c.id}
                            fieldId={c.id}
                            value={resolveValue(detail, c.id)}
                          />
                        ))}
                      </div>
                    )}
                  </div>
                </>
              )}
            </div>
          </div>
        </div>
      )}

      {pdfOpen && (
        <div
          className={`fixed inset-0 z-[70] flex items-center justify-center bg-black/55 ${
            pdfExpanded ? 'p-0' : 'p-3 sm:p-5'
          }`}
          onClick={closePdfModal}
        >
          <div
            className={`bg-surface flex flex-col overflow-hidden shadow-2xl border border-border ${
              pdfExpanded
                ? 'w-full h-full max-w-none rounded-none'
                : 'w-full max-w-[960px] h-[min(92vh,900px)] rounded-xl'
            }`}
            onClick={(e) => e.stopPropagation()}
            role="dialog"
            aria-modal="true"
            aria-label="Invoice PDF"
          >
            <div className="flex items-center gap-2 px-4 py-2.5 border-b border-border shrink-0 bg-surface">
              <span className="text-sm font-semibold text-text-primary truncate min-w-0 flex-1">
                {pdfTitle}
              </span>
              {pdfUrl && !pdfLoading && (
                <button
                  type="button"
                  onClick={() => setPdfExpanded((v) => !v)}
                  title={pdfExpanded ? 'Exit expand' : 'Expand'}
                  className="inline-flex items-center gap-1.5 px-2.5 py-1.5 rounded-lg text-xs font-medium border border-border hover:bg-background text-text-primary shrink-0"
                >
                  {pdfExpanded ? <Minimize2 size={14} /> : <Maximize2 size={14} />}
                  <span className="hidden sm:inline">{pdfExpanded ? 'Collapse' : 'Expand'}</span>
                </button>
              )}
              <button
                type="button"
                onClick={closePdfModal}
                className="w-8 h-8 rounded-lg flex items-center justify-center hover:bg-background text-text-secondary shrink-0"
                aria-label="Close PDF"
              >
                <X size={16} />
              </button>
            </div>
            <div className="flex-1 min-h-0 flex flex-col bg-slate-900">
              {pdfLoading && (
                <p className="text-sm text-slate-300 inline-flex items-center gap-2 p-5 m-0">
                  <Loader2 size={16} className="animate-spin" />
                  Loading PDF…
                </p>
              )}
              {pdfError && (
                <p className="text-sm text-red-200 font-medium p-4 m-0 bg-red-900/80">
                  {pdfError}
                </p>
              )}
              {pdfUrl && !pdfLoading && (
                <iframe
                  title={pdfTitle}
                  src={pdfUrl}
                  className="flex-1 w-full min-h-[70vh] border-0 bg-white"
                />
              )}
            </div>
          </div>
        </div>
      )}

      {editOpen && (
        <ObservabilityViewModal
          viewId={projectId}
          onClose={() => setEditOpen(false)}
          onSaved={() => {
            setEditOpen(false)
            load()
          }}
        />
      )}
    </div>
  )
}
