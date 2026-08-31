import { useRef, useState, useEffect } from 'react'
import { useParams, Link } from 'react-router-dom'
import {
  ChevronDown, Copy, Check, Clock, Zap, AlertTriangle,
  XCircle, CheckCircle, Settings, FileText, Truck,
} from 'lucide-react'
import { LineChart, Line, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer } from 'recharts'
import { useProject } from '../hooks/useProjects'
import { useResults } from '../hooks/useResults'
import { getCarriers } from '../api/client'
import ScoreBadge from '../components/results/ScoreBadge'
import JsonViewer from '../components/results/JsonViewer'

const STATUS_PILL = {
  passed:  'bg-pando-green-50 text-pando-green-600 border-pando-green-200',
  warning: 'bg-warning-bg text-warning border-warning/30',
  failed:  'bg-danger-bg text-danger border-danger/20',
}

const STATUS_TABS = ['all', 'passed', 'warning', 'failed']
const DATE_RANGES = ['Last 24h', 'Last 7d', 'Last 30d', 'All time']

// ── Field table ───────────────────────────────────────────────────────────────
function FieldStatusIcon({ status }) {
  if (status === 'correct') return <CheckCircle size={14} className="text-success flex-shrink-0" />
  if (status === 'wrong')   return <XCircle     size={14} className="text-danger  flex-shrink-0" />
  return <AlertTriangle size={14} className="text-warning flex-shrink-0" />
}

function ApiStatusBadge({ status }) {
  if (!status) return null
  const cls =
    status < 300 ? 'bg-pando-green-50 text-pando-green-700 border-pando-green-200'
    : status < 500 ? 'bg-warning-bg text-warning border-warning/30'
    : 'bg-danger-bg text-danger border-danger/20'
  return (
    <span className={`inline-flex items-center px-2 py-0.5 rounded-full text-[10px] font-bold border font-mono flex-shrink-0 ${cls}`}>
      {status}
    </span>
  )
}

function MandatoryFieldsBar({ mandatoryResult }) {
  if (!mandatoryResult || mandatoryResult.total === 0) return null
  const allPassed = mandatoryResult.failed === 0
  return (
    <div className={`flex items-start gap-3 rounded-xl p-3.5 border mb-4 ${
      allPassed
        ? 'bg-pando-green-50 border-pando-green-200'
        : 'bg-danger-bg border-danger/20'
    }`}>
      <div className="flex-shrink-0 mt-0.5">
        {allPassed
          ? <CheckCircle size={15} className="text-success" />
          : <XCircle    size={15} className="text-danger"  />}
      </div>
      <div className="flex-1 min-w-0">
        <p className={`text-xs font-bold ${allPassed ? 'text-pando-green-700' : 'text-danger'}`}>
          Mandatory Fields — {mandatoryResult.passed}/{mandatoryResult.total} present
        </p>
        {!allPassed && mandatoryResult.failed_fields.length > 0 && (
          <p className="text-danger text-xs mt-1">
            Missing: {mandatoryResult.failed_fields.map((f) => (
              <span key={f} className="font-mono font-semibold bg-danger/10 px-1 rounded mr-1">{f}</span>
            ))}
          </p>
        )}
      </div>
    </div>
  )
}

function FieldTable({ validations }) {
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-sm">
        <thead>
          <tr className="border-b-2 border-border">
            {['Field Name', 'Expected', 'Actual (LLM)', 'Status', 'Source Used'].map((h) => (
              <th key={h} className="text-left text-text-muted font-semibold pb-3 pr-4 text-xs uppercase tracking-wider">{h}</th>
            ))}
          </tr>
        </thead>
        <tbody className="divide-y divide-border">
          {validations.map((v, i) => (
            <tr key={i} className={`border-l-[3px] ${
              v.status === 'correct' ? 'border-l-success bg-pando-green-50/30'
              : v.status === 'wrong' ? 'border-l-danger bg-danger-bg/50'
              : 'border-l-warning bg-warning-bg/50'
            }`}>
              <td className="py-2.5 pr-4 pl-2">
                <div className="flex items-center gap-1.5 flex-wrap">
                  <span className="font-mono text-text-primary text-xs font-semibold">{v.field_name}</span>
                  {v.is_mandatory && (
                    <span className="px-1.5 py-0.5 bg-pando-green text-white text-[9px] font-bold rounded uppercase tracking-wide leading-none">
                      required
                    </span>
                  )}
                </div>
              </td>
              <td className="py-2.5 pr-4 font-mono text-text-secondary text-xs">{v.expected_value ?? '—'}</td>
              <td className="py-2.5 pr-4 font-mono text-xs font-medium" style={{ color: v.status === 'correct' ? '#16A34A' : v.status === 'wrong' ? '#DC2626' : '#D97706' }}>
                {v.actual_value ?? <span className="text-warning italic">missing</span>}
              </td>
              <td className="py-2.5 pr-4">
                <div className="flex items-center gap-1.5">
                  <FieldStatusIcon status={v.status} />
                  <span className="text-xs capitalize text-text-secondary font-medium">{v.status}</span>
                </div>
              </td>
              <td className="py-2.5 text-text-muted text-xs">{v.source_used}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

function CopyableText({ text }) {
  const [copied, setCopied] = useState(false)
  return (
    <button
      onClick={() => { navigator.clipboard.writeText(text); setCopied(true); setTimeout(() => setCopied(false), 2000) }}
      className="flex items-center gap-1 px-2 py-0.5 rounded-lg text-text-muted hover:text-pando-green border border-transparent hover:border-pando-green-200 hover:bg-pando-green-50 transition-colors text-xs flex-shrink-0"
    >
      {copied ? <Check size={10} className="text-success" /> : <Copy size={10} />}
      {copied ? 'Copied!' : 'Copy'}
    </button>
  )
}

// ── Expandable result row ─────────────────────────────────────────────────────
function ResultRow({ result }) {
  const [expanded, setExpanded] = useState(false)
  const [activeTab, setActiveTab] = useState('fields')
  const contentRef = useRef(null)

  const handleToggle = () => {
    const el = contentRef.current
    if (el) el.style.maxHeight = !expanded ? el.scrollHeight + 'px' : '0px'
    setExpanded((v) => !v)
  }

  const wrongCount   = result.field_validations.filter((f) => f.status === 'wrong').length
  const missingCount = result.field_validations.filter((f) => f.status === 'missing').length
  const summary = [wrongCount && `${wrongCount} wrong`, missingCount && `${missingCount} missing`]
    .filter(Boolean).join(' · ') || 'All correct'

  const mandatoryFailed = result.mandatory_fields_result?.failed > 0

  const TABS = [
    { id: 'fields',      label: 'Field Validation' },
    { id: 'suggestions', label: 'Prompt Suggestions' },
    { id: 'logs',        label: 'Log Summary' },
    { id: 'raw',         label: 'Raw Payload' },
  ]

  return (
    <div className="bg-white border border-border rounded-2xl overflow-hidden mb-3 shadow-card hover:shadow-card-hover transition-shadow">
      <div
        className="flex items-center gap-4 px-5 py-4 cursor-pointer hover:bg-background/60 transition-colors"
        onClick={handleToggle}
      >
        <div className="min-w-0 flex-1">
          <p className="font-mono font-bold text-text-primary text-sm">{result.invoice_number}</p>
          <p className="text-text-muted text-xs mt-0.5 flex items-center gap-1">
            <Clock size={10} /> {result.timestamp}
          </p>
          {result.vendor_name && (
            <p className="text-text-muted text-[10px] mt-0.5 flex items-center gap-1">
              <Truck size={9} /> {result.vendor_name}
            </p>
          )}
        </div>

        <ScoreBadge score={result.overall_score} size={48} />

        <span className={`px-2.5 py-0.5 rounded-full text-xs font-semibold border capitalize ${STATUS_PILL[result.status]}`}>
          {result.status}
        </span>

        <p className="text-text-muted text-xs hidden md:block min-w-[100px]">{summary}</p>

        <ApiStatusBadge status={result.api_status} />

        {mandatoryFailed && (
          <span className="hidden md:inline-flex items-center gap-1 px-2 py-0.5 bg-danger-bg border border-danger/20 text-danger text-[10px] font-bold rounded-full flex-shrink-0">
            <XCircle size={9} /> mandatory
          </span>
        )}

        <ChevronDown size={16} className={`text-text-muted transition-transform duration-200 flex-shrink-0 ${expanded ? 'rotate-180' : ''}`} />
      </div>

      <div ref={contentRef} style={{ maxHeight: 0, overflow: 'hidden', transition: 'max-height 0.3s ease' }}>
        <div className="border-t border-border">
          <div className="flex gap-1 px-4 py-3 bg-background border-b border-border overflow-x-auto">
            {TABS.map((t) => (
              <button
                key={t.id}
                onClick={(e) => { e.stopPropagation(); setActiveTab(t.id) }}
                className={`px-3 py-1.5 rounded-lg text-xs font-semibold transition-colors whitespace-nowrap ${
                  activeTab === t.id
                    ? 'bg-pando-green text-white shadow-sm'
                    : 'text-text-secondary hover:bg-white hover:text-pando-green border border-transparent hover:border-border'
                }`}
              >
                {t.label}
              </button>
            ))}
          </div>

          <div className="p-5">
            {activeTab === 'fields' && (
              <>
                {result.api_status >= 400 ? (
                  <div className="flex items-start gap-3 bg-danger-bg border border-danger/20 rounded-xl p-4 mb-4">
                    <XCircle size={18} className="text-danger flex-shrink-0 mt-0.5" />
                    <div>
                      <p className="text-danger font-bold text-sm">
                        Lambda API Error — HTTP {result.api_status}
                      </p>
                      <p className="text-danger/80 text-xs mt-1">
                        The Lambda returned an error status. Field validation was skipped.
                        Fix the API error first, then re-test.
                      </p>
                      {result.log_summary?.errors?.length > 0 && (
                        <div className="mt-3 space-y-1.5">
                          {result.log_summary.errors.map((e, i) => (
                            <div key={i} className="flex items-start gap-2 text-xs text-danger/90 bg-danger/5 rounded-lg p-2 border border-danger/10">
                              <XCircle size={11} className="mt-0.5 flex-shrink-0" />{e}
                            </div>
                          ))}
                        </div>
                      )}
                    </div>
                  </div>
                ) : (
                  <>
                    <MandatoryFieldsBar mandatoryResult={result.mandatory_fields_result} />
                    <FieldTable validations={result.field_validations} />
                  </>
                )}
              </>
            )}

            {activeTab === 'suggestions' && (
              <div className="space-y-3">
                {result.prompt_suggestions.length === 0
                  ? <p className="text-text-muted text-sm italic">No suggestions.</p>
                  : result.prompt_suggestions.map((s, i) => (
                    <div key={i} className="flex items-start gap-3 bg-pando-green-50 rounded-xl p-3.5 border border-pando-green-100 border-l-[3px] border-l-pando-green">
                      <span className="w-5 h-5 rounded-full bg-pando-green text-white text-[10px] font-bold flex items-center justify-center flex-shrink-0 mt-0.5">{i + 1}</span>
                      <p className="text-text-secondary text-sm flex-1 leading-relaxed">{s}</p>
                      <CopyableText text={s} />
                    </div>
                  ))}
              </div>
            )}

            {activeTab === 'logs' && (
              <div>
                <div className="grid grid-cols-2 gap-4 mb-4">
                  <div>
                    <span className="px-2.5 py-0.5 bg-danger-bg text-danger border border-danger/20 rounded-full text-xs font-bold inline-block mb-3">
                      {result.log_summary.errors.length} Errors
                    </span>
                    <div className="space-y-2">
                      {result.log_summary.errors.length === 0
                        ? <p className="text-text-muted text-xs italic">No errors</p>
                        : result.log_summary.errors.map((e, i) => (
                          <div key={i} className="flex items-start gap-2 text-xs text-text-secondary bg-danger-bg rounded-lg p-2.5 border border-danger/10">
                            <XCircle size={11} className="text-danger mt-0.5 flex-shrink-0" />{e}
                          </div>
                        ))}
                    </div>
                  </div>
                  <div>
                    <span className="px-2.5 py-0.5 bg-warning-bg text-warning border border-warning/20 rounded-full text-xs font-bold inline-block mb-3">
                      {result.log_summary.warnings.length} Warnings
                    </span>
                    <div className="space-y-2">
                      {result.log_summary.warnings.length === 0
                        ? <p className="text-text-muted text-xs italic">No warnings</p>
                        : result.log_summary.warnings.map((w, i) => (
                          <div key={i} className="flex items-start gap-2 text-xs text-text-secondary bg-warning-bg rounded-lg p-2.5 border border-warning/10">
                            <AlertTriangle size={11} className="text-warning mt-0.5 flex-shrink-0" />{w}
                          </div>
                        ))}
                    </div>
                  </div>
                </div>
                <div className="flex items-center gap-5 pt-4 border-t border-border">
                  <div className="flex items-center gap-1.5 text-text-secondary text-xs">
                    <Clock size={12} className="text-pando-green" />
                    Duration: <strong className="text-text-primary ml-0.5">{result.log_summary.execution_duration_ms}ms</strong>
                  </div>
                  <div className="flex items-center gap-2 text-text-secondary text-xs">
                    <Zap size={12} className="text-pando-green" />
                    Cold Start:
                    <span className={`ml-1 px-2 py-0.5 rounded-full text-xs font-semibold border ${result.log_summary.cold_start ? 'bg-warning-bg text-warning border-warning/20' : 'bg-pando-green-50 text-pando-green-600 border-pando-green-200'}`}>
                      {result.log_summary.cold_start ? 'Yes' : 'No'}
                    </span>
                  </div>
                </div>
              </div>
            )}

            {activeTab === 'raw' && <JsonViewer data={result.raw_payload} />}
          </div>
        </div>
      </div>
    </div>
  )
}

function SkeletonResult() {
  return (
    <div className="bg-white border border-border rounded-2xl px-5 py-4 mb-3 shadow-card animate-pulse">
      <div className="flex items-center gap-4">
        <div className="flex-1">
          <div className="h-4 w-36 bg-border rounded-lg mb-2" />
          <div className="h-3 w-20 bg-background rounded-lg" />
        </div>
        <div className="w-12 h-12 rounded-full bg-border" />
        <div className="h-6 w-16 bg-border rounded-full" />
        <div className="h-3 w-20 bg-background rounded-lg" />
      </div>
    </div>
  )
}

// ── Main page ─────────────────────────────────────────────────────────────────
export default function Results() {
  const { projectId } = useParams()
  const { project } = useProject(projectId)
  const [statusFilter,  setStatusFilter]  = useState('all')
  const [carrierFilter, setCarrierFilter] = useState('all')
  const [invoiceSearch, setInvoiceSearch] = useState('')
  const [dateRange,     setDateRange]     = useState('Last 7d')
  const [carriers, setCarriers] = useState([])

  // Load available carriers for this project
  useEffect(() => {
    if (!projectId) return
    getCarriers(projectId)
      .then((data) => setCarriers(data.carriers ?? []))
      .catch(() => setCarriers([]))
  }, [projectId])

  const { results, loading, error, refetch } = useResults(projectId, {
    status:  statusFilter  !== 'all' ? statusFilter  : undefined,
    carrier: carrierFilter !== 'all' ? carrierFilter : undefined,
    invoice: invoiceSearch || undefined,
  })

  const chartData = [...results].reverse().map((r) => ({
    time: r.timestamp,
    score: r.overall_score,
    status: r.status,
  }))

  const scoreStatus = project?.last_score != null
    ? project.last_score >= 85 ? 'passed' : project.last_score >= 60 ? 'warning' : 'failed'
    : null

  const CustomDot = (props) => {
    const { cx, cy, payload } = props
    const color = payload.score >= 85 ? '#16A34A' : payload.score >= 60 ? '#D97706' : '#DC2626'
    return <circle key={`dot-${cx}-${cy}`} cx={cx} cy={cy} r={5} fill={color} stroke="white" strokeWidth={2} />
  }

  return (
    <div className="p-6 max-w-[1200px]">
      {/* Header card */}
      <div className="bg-white border border-border rounded-2xl shadow-card px-6 py-5 mb-6">
        <div className="flex items-start justify-between gap-4">
          <div>
            <h1 className="text-text-primary font-bold text-2xl tracking-tight">{project?.project_name ?? projectId}</h1>
            <div className="flex items-center gap-3 mt-2 flex-wrap">
              {project?.last_tested && (
                <span className="text-text-muted text-sm flex items-center gap-1.5">
                  <Clock size={13} /> Last tested: {project.last_tested}
                </span>
              )}
              {scoreStatus && (
                <span className={`px-2.5 py-0.5 rounded-full text-xs font-semibold border capitalize ${STATUS_PILL[scoreStatus]}`}>
                  {scoreStatus}
                </span>
              )}
            </div>
          </div>
          <div className="flex items-center gap-2 flex-shrink-0">
            <Link
              to={`/project/${projectId}/configure`}
              className="flex items-center gap-1.5 px-3.5 py-2 rounded-xl border border-border text-text-secondary text-sm font-medium hover:border-pando-green hover:text-pando-green transition-colors"
            >
              <Settings size={13} /> Configure
            </Link>
          </div>
        </div>
      </div>

      {/* Score trend chart */}
      {chartData.length > 0 && (
        <div className="bg-white border border-border rounded-2xl shadow-card px-6 py-5 mb-6">
          <p className="text-text-primary text-sm font-bold mb-4">Score Trend</p>
          <ResponsiveContainer width="100%" height={170}>
            <LineChart data={chartData} margin={{ top: 5, right: 10, bottom: 5, left: 0 }}>
              <CartesianGrid strokeDasharray="3 3" stroke="#E8EAED" vertical={false} />
              <XAxis dataKey="time" stroke="#E8EAED" tick={{ fill: '#9CA3AF', fontSize: 11 }} axisLine={false} tickLine={false} />
              <YAxis domain={[0, 100]} stroke="#E8EAED" tick={{ fill: '#9CA3AF', fontSize: 11 }} axisLine={false} tickLine={false} width={28} />
              <Tooltip
                contentStyle={{ background: '#fff', border: '1px solid #E8EAED', borderRadius: 12, fontSize: 12, boxShadow: '0 4px 16px rgba(0,0,0,0.10)' }}
                labelStyle={{ color: '#4B5563', fontWeight: 600 }}
                itemStyle={{ color: '#6C5CE7' }}
              />
              <Line type="monotone" dataKey="score" stroke="#6C5CE7" strokeWidth={2.5} dot={<CustomDot />} activeDot={{ r: 6, fill: '#6C5CE7', stroke: 'white', strokeWidth: 2 }} />
            </LineChart>
          </ResponsiveContainer>
        </div>
      )}

      {/* Filter bar */}
      <div className="sticky top-14 z-10 bg-background pb-4 pt-0.5 space-y-3">
        {/* Row 1: search + status tabs + date range */}
        <div className="flex items-center gap-3 flex-wrap">
          <input
            type="text"
            placeholder="Search by invoice number..."
            value={invoiceSearch}
            onChange={(e) => setInvoiceSearch(e.target.value)}
            className="w-60 text-sm"
          />
          <div className="flex items-center gap-0.5 bg-white border border-border rounded-xl p-1 shadow-sm">
            {STATUS_TABS.map((s) => (
              <button
                key={s}
                onClick={() => setStatusFilter(s)}
                className={`px-3 py-1.5 rounded-lg text-xs font-semibold transition-colors capitalize ${
                  statusFilter === s ? 'bg-pando-green text-white shadow-sm' : 'text-text-secondary hover:text-pando-green'
                }`}
              >
                {s}
              </button>
            ))}
          </div>
          <select value={dateRange} onChange={(e) => setDateRange(e.target.value)} className="text-sm py-1.5">
            {DATE_RANGES.map((d) => <option key={d} value={d}>{d}</option>)}
          </select>
          <button
            onClick={refetch}
            className="ml-auto text-xs text-text-muted hover:text-pando-green font-medium underline underline-offset-2 transition-colors"
          >
            Refresh
          </button>
        </div>

        {/* Row 2: carrier pills (only when there are carriers) */}
        {carriers.length > 0 && (
          <div className="flex items-center gap-2 flex-wrap">
            <span className="flex items-center gap-1 text-text-muted text-xs font-medium">
              <Truck size={12} /> Carrier:
            </span>
            {['all', ...carriers].map((c) => (
              <button
                key={c}
                onClick={() => setCarrierFilter(c)}
                className={`px-3 py-1 rounded-full text-xs font-semibold border transition-colors ${
                  carrierFilter === c
                    ? 'bg-pando-green text-white border-pando-green shadow-sm'
                    : 'bg-white text-text-secondary border-border hover:border-pando-green hover:text-pando-green'
                }`}
              >
                {c === 'all' ? 'All Carriers' : c}
              </button>
            ))}
          </div>
        )}
      </div>

      {error && (
        <div className="bg-danger-bg border border-danger/20 rounded-xl p-4 mb-4 flex items-center justify-between">
          <p className="text-danger text-sm font-medium">{error}</p>
          <button onClick={refetch} className="text-danger text-sm underline font-medium">Retry</button>
        </div>
      )}

      {loading ? (
        <div>{Array.from({ length: 3 }).map((_, i) => <SkeletonResult key={i} />)}</div>
      ) : results.length === 0 ? (
        <div className="flex flex-col items-center justify-center py-20 text-text-muted bg-white border border-border rounded-2xl shadow-card">
          <div className="w-14 h-14 rounded-2xl bg-pando-green-50 border-2 border-pando-green-100 flex items-center justify-center mb-4">
            <FileText size={24} className="text-pando-green" />
          </div>
          <p className="text-base font-semibold text-text-secondary">No results found</p>
          <p className="text-sm mt-1">
            {carrierFilter !== 'all' ? `No results for carrier "${carrierFilter}". Try "All Carriers".` : 'Run a test to see results here.'}
          </p>
        </div>
      ) : (
        <div>{results.map((r) => <ResultRow key={r.result_id} result={r} />)}</div>
      )}

    </div>
  )
}
