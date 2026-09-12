import { useState, useEffect, useCallback } from 'react'
import { useParams, useNavigate } from 'react-router-dom'
import {
  ArrowLeft, RefreshCw, Truck, Layers, FileText, Map, AlertTriangle, Boxes, X,
} from 'lucide-react'
import { getDocProjectSummary } from '../api/client'

function SectionCard({ icon: Icon, title, subtitle, children }) {
  return (
    <section className="bg-surface border border-border rounded-2xl shadow-card overflow-hidden">
      <div className="flex items-center gap-3 px-5 py-4 border-b border-border">
        <div
          className="w-9 h-9 rounded-xl flex items-center justify-center flex-shrink-0"
          style={{ background: '#EEF2FF', border: '1px solid #E0DEFF' }}
        >
          <Icon size={17} style={{ color: '#6C5CE7' }} />
        </div>
        <div>
          <h3 className="text-text-primary font-semibold text-sm">{title}</h3>
          {subtitle && <p className="text-text-muted text-[11px] mt-0.5">{subtitle}</p>}
        </div>
      </div>
      <div className="px-5 py-4">{children}</div>
    </section>
  )
}

function RuleList({ rules, empty }) {
  if (!rules || rules.length === 0) {
    return <p className="text-text-muted text-xs italic">{empty}</p>
  }
  return (
    <ul className="flex flex-col gap-2">
      {rules.map((r, i) => (
        <li key={i} className="flex gap-2 text-text-secondary text-[13px] leading-relaxed">
          <span className="text-[#6C5CE7] mt-0.5 flex-shrink-0">•</span>
          <span className="min-w-0 break-words">{r}</span>
        </li>
      ))}
    </ul>
  )
}

function StatPill({ label, value }) {
  return (
    <div className="bg-background border border-border rounded-xl px-4 py-3 flex-1 min-w-[120px]">
      <p className="text-text-muted text-[11px] font-medium">{label}</p>
      <p className="text-text-primary text-xl font-bold mt-0.5">{value}</p>
    </div>
  )
}

function CarrierFieldsModal({ carrier, onClose }) {
  useEffect(() => {
    function onKey(e) { if (e.key === 'Escape') onClose() }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onClose])

  if (!carrier) return null
  const fields = carrier.fields || []

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center p-4"
      style={{ background: 'rgba(13,17,23,0.55)' }}
      onClick={onClose}
    >
      <div
        className="bg-surface border border-border rounded-2xl shadow-card w-full max-w-lg max-h-[80vh] flex flex-col"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-start justify-between px-6 py-4 border-b border-border">
          <div className="min-w-0">
            <h3 className="text-text-primary font-bold text-base truncate">{carrier.name}</h3>
            <p className="text-text-muted text-[11px] mt-0.5">
              {fields.length} field{fields.length !== 1 ? 's' : ''} required for this carrier
              {carrier.key !== carrier.name ? ` · key: ${carrier.key}` : ''}
            </p>
          </div>
          <button
            onClick={onClose}
            className="w-8 h-8 flex items-center justify-center rounded-lg text-text-muted hover:bg-background flex-shrink-0"
            aria-label="Close"
          >
            <X size={18} />
          </button>
        </div>

        <div className="px-6 py-4 overflow-y-auto">
          {fields.length === 0 ? (
            <p className="text-text-muted text-sm italic">No fields were parsed for this carrier.</p>
          ) : (
            <div className="flex flex-wrap gap-1.5">
              {fields.map((f, i) => (
                <span
                  key={i}
                  className="text-[11px] text-text-secondary bg-background border border-border rounded-md px-2 py-1 font-mono"
                >
                  {f}
                </span>
              ))}
            </div>
          )}
        </div>
      </div>
    </div>
  )
}

export default function DocumentationDetail() {
  const { docId } = useParams()
  const navigate = useNavigate()
  const [data, setData] = useState(null)
  const [loading, setLoading] = useState(true)
  const [refreshing, setRefreshing] = useState(false)
  const [error, setError] = useState(null)
  const [activeCarrier, setActiveCarrier] = useState(null)

  const load = useCallback(async (isRefresh = false) => {
    isRefresh ? setRefreshing(true) : setLoading(true)
    setError(null)
    try {
      const summary = await getDocProjectSummary(docId)
      setData(summary)
    } catch (err) {
      setError(err.message)
    } finally {
      isRefresh ? setRefreshing(false) : setLoading(false)
    }
  }, [docId])

  useEffect(() => { load(false) }, [load])

  const carriers = data?.carriers || []
  const cc = data?.carrier_classification || {}
  const pc = data?.page_classification || {}
  const fm = data?.field_mapping || {}

  return (
    <div>
      <div className="flex items-center justify-between mb-6 gap-4 flex-wrap">
        <div className="flex items-center gap-3 min-w-0">
          <button
            onClick={() => navigate('/documentation')}
            className="w-9 h-9 flex items-center justify-center rounded-xl border border-border text-text-secondary hover:bg-surface flex-shrink-0"
            aria-label="Back"
          >
            <ArrowLeft size={17} />
          </button>
          <div className="min-w-0">
            <h2 className="text-text-primary font-bold text-base truncate">
              {data?.project_name || 'Documentation'}
            </h2>
            {data?.s3_path && (
              <p className="text-text-muted text-[11px] truncate font-mono">{data.s3_path}</p>
            )}
          </div>
        </div>
        <button
          onClick={() => load(true)}
          disabled={refreshing || loading}
          className="flex items-center gap-2 px-4 py-2 text-sm font-semibold rounded-xl border border-border text-text-secondary hover:bg-surface disabled:opacity-50"
        >
          <RefreshCw size={14} className={refreshing ? 'animate-spin' : ''} />
          {refreshing ? 'Refreshing…' : 'Refresh from S3'}
        </button>
      </div>

      {loading ? (
        <div className="flex flex-col gap-5">
          {Array.from({ length: 3 }).map((_, i) => (
            <div key={i} className="bg-surface border border-border rounded-2xl shadow-card animate-pulse h-40" />
          ))}
        </div>
      ) : error ? (
        <div className="bg-danger-bg border border-danger/20 rounded-xl p-5 flex items-start gap-3">
          <AlertTriangle size={18} className="text-danger flex-shrink-0 mt-0.5" />
          <div className="flex-1">
            <p className="text-danger text-sm font-semibold">Could not load the documentation summary</p>
            <p className="text-danger/80 text-xs mt-1">{error}</p>
            <button onClick={() => load(false)} className="text-danger text-xs underline hover:no-underline font-medium mt-2">
              Try again
            </button>
          </div>
        </div>
      ) : (
        <div className="flex flex-col gap-5">
          {data && data.parsed === false && (
            <div className="bg-warning-bg border border-warning/20 rounded-xl p-4 flex items-start gap-3">
              <AlertTriangle size={18} className="text-warning flex-shrink-0 mt-0.5" />
              <p className="text-text-secondary text-sm">{data.message || 'The prompt template could not be parsed.'}</p>
            </div>
          )}

          {/* Overview stats */}
          <div className="flex flex-wrap gap-3">
            <StatPill label="Carriers" value={data?.carrier_count ?? 0} />
            <StatPill label="Mapped Fields" value={fm?.field_count ?? (fm?.fields?.length || 0)} />
            <StatPill
              label="Page Classification"
              value={(pc?.carriers_with_page_logic?.length || 0) > 0 ? 'Present' : 'None'}
            />
            {data?.fetched_at && (
              <div className="bg-background border border-border rounded-xl px-4 py-3 flex-1 min-w-[160px]">
                <p className="text-text-muted text-[11px] font-medium">Last synced from S3</p>
                <p className="text-text-secondary text-xs font-semibold mt-1">
                  {new Date(data.fetched_at).toLocaleString()}
                </p>
              </div>
            )}
          </div>

          {/* Carriers */}
          <SectionCard
            icon={Truck}
            title="Carriers"
            subtitle={`${data?.carrier_count ?? 0} carrier(s) defined in this prompt template`}
          >
            {carriers.length === 0 ? (
              <p className="text-text-muted text-xs italic">No carriers found in the template.</p>
            ) : (
              <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
                {carriers.map((c) => (
                  <div key={c.key} className="border border-border rounded-xl p-3.5 bg-background">
                    <div className="flex items-center justify-between gap-2 mb-1.5">
                      <p className="text-text-primary font-semibold text-[13px] truncate">{c.name}</p>
                      {c.has_page_logic && (
                        <span className="text-[10px] font-semibold text-[#6C5CE7] bg-[#EEF2FF] border border-[#E0DEFF] rounded-full px-2 py-0.5 flex-shrink-0">
                          page logic
                        </span>
                      )}
                    </div>
                    {c.key !== c.name && (
                      <p className="text-text-muted text-[11px] font-mono truncate mb-1.5">key: {c.key}</p>
                    )}
                    <div className="flex items-center gap-3 text-[11px] text-text-muted">
                      <span className="inline-flex items-center gap-1">
                        <Boxes size={12} /> {c.field_count} fields
                      </span>
                    </div>
                    {c.fields && c.fields.length > 0 && (
                      <div className="flex flex-wrap gap-1 mt-2">
                        {c.fields.slice(0, 10).map((f, i) => (
                          <span key={i} className="text-[10px] text-text-secondary bg-surface border border-border rounded px-1.5 py-0.5 font-mono">
                            {f}
                          </span>
                        ))}
                        {c.fields.length > 10 && (
                          <button
                            type="button"
                            onClick={() => setActiveCarrier(c)}
                            className="text-[10px] font-semibold text-[#6C5CE7] hover:text-[#5A4BD1] hover:underline px-1 py-0.5"
                          >
                            +{c.fields.length - 10} more
                          </button>
                        )}
                      </div>
                    )}
                  </div>
                ))}
              </div>
            )}
          </SectionCard>

          {/* Carrier classification */}
          <SectionCard icon={Layers} title="Carrier Classification" subtitle="Which value each carrier is matched on">
            <p className="text-text-secondary text-[13px] leading-relaxed mb-4">{cc?.summary}</p>
            {cc?.mappings && cc.mappings.length > 0 ? (
              <div className="border border-border rounded-xl overflow-hidden">
                <table className="w-full text-left text-[13px]">
                  <thead>
                    <tr className="bg-background border-b border-border">
                      <th className="px-4 py-2.5 text-text-muted text-[11px] font-semibold uppercase tracking-wide">Carrier</th>
                      <th className="px-4 py-2.5 text-text-muted text-[11px] font-semibold uppercase tracking-wide">Matched on</th>
                      <th className="px-4 py-2.5 text-text-muted text-[11px] font-semibold uppercase tracking-wide">Value</th>
                    </tr>
                  </thead>
                  <tbody>
                    {cc.mappings.map((m, i) => (
                      <tr key={m.key || i} className="border-b border-border last:border-0">
                        <td className="px-4 py-2.5 text-text-primary font-medium">{m.carrier}</td>
                        <td className="px-4 py-2.5 text-text-secondary">
                          {m.signal_type === 'vendor_ref_id'
                            ? 'Vendor Reference ID'
                            : m.signal_type === 'carrier_name'
                              ? 'Carrier Name'
                              : 'Carrier name on invoice'}
                        </td>
                        <td className="px-4 py-2.5">
                          {m.values && m.values.length > 0 ? (
                            <span className="flex flex-wrap gap-1">
                              {m.values.map((v, j) => (
                                <span key={j} className="text-[12px] text-[#6C5CE7] bg-[#EEF2FF] border border-[#E0DEFF] rounded-md px-2 py-0.5 font-mono">
                                  {v}
                                </span>
                              ))}
                            </span>
                          ) : (
                            <span className="text-text-muted text-[12px] italic">by name</span>
                          )}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            ) : (
              <p className="text-text-muted text-xs italic">No carrier-classification mappings could be parsed.</p>
            )}
          </SectionCard>

          {/* Page classification */}
          <SectionCard icon={FileText} title="Page Classification" subtitle="How pages of an invoice are identified">
            <p className="text-text-secondary text-[13px] leading-relaxed mb-3">{pc?.summary}</p>
            {pc?.carriers_with_page_logic?.length > 0 && (
              <p className="text-text-muted text-[11px] mb-2">
                Carriers with page logic: <span className="text-text-secondary">{pc.carriers_with_page_logic.join(', ')}</span>
              </p>
            )}
            <RuleList rules={pc?.rules} empty="No page-classification rules were detected in the template text." />
          </SectionCard>

          {/* Field mapping */}
          <SectionCard icon={Map} title="Field Mapping Logic" subtitle="How invoice fields are extracted and mapped">
            <p className="text-text-secondary text-[13px] leading-relaxed mb-3">{fm?.summary}</p>
            {fm?.rules && fm.rules.length > 0 && (
              <div className="mb-3">
                <RuleList rules={fm.rules} empty="" />
              </div>
            )}
            {fm?.fields && fm.fields.length > 0 && (
              <div>
                <p className="text-text-muted text-[11px] font-semibold mb-2">Mapped fields ({fm.fields.length})</p>
                <div className="flex flex-wrap gap-1.5">
                  {fm.fields.map((f, i) => (
                    <span key={i} className="text-[11px] text-text-secondary bg-background border border-border rounded-md px-2 py-0.5 font-mono">
                      {f}
                    </span>
                  ))}
                </div>
              </div>
            )}
          </SectionCard>
        </div>
      )}

      <CarrierFieldsModal carrier={activeCarrier} onClose={() => setActiveCarrier(null)} />
    </div>
  )
}
