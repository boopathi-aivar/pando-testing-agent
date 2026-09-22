import { useEffect, useRef, useState } from 'react'
import {
  Upload, Sparkles, FileText, MessageSquare,
  XCircle, AlertTriangle, RefreshCw, Send,
  Loader2, Copy, Check, Info, ChevronDown, ChevronUp,
} from 'lucide-react'
import {
  pgExtract, pgFeedback, pgDoclingRetry, pgCorrect, pgAsk, pgHealth,
} from '../api/promptGenerator'

// ── tiny helpers ──────────────────────────────────────────────────────────────

function pct(score) { return Math.round((score ?? 0) * 100) }

function AccuracyBadge({ score }) {
  const p = pct(score)
  const cls = p >= 75
    ? 'bg-pando-green-50 text-pando-green border-pando-green-200'
    : p >= 50
      ? 'bg-warning-bg text-warning border-warning/30'
      : 'bg-danger-bg text-danger border-danger/20'
  return (
    <span className={`inline-flex items-center px-2.5 py-0.5 rounded-full text-xs font-semibold border ${cls}`}>
      {p}% accuracy
    </span>
  )
}

function StatusPill({ status }) {
  const map = {
    completed:          'bg-pando-green-50 text-pando-green border-pando-green-200',
    failed:             'bg-danger-bg text-danger border-danger/20',
    awaiting_feedback:  'bg-warning-bg text-warning border-warning/30',
  }
  const cls = map[status] ?? 'bg-[#6C5CE7]/10 text-[#6C5CE7] border-[#6C5CE7]/30'
  return (
    <span className={`inline-flex items-center px-2.5 py-0.5 rounded-full text-xs font-semibold border capitalize ${cls}`}>
      {status?.replace(/_/g, ' ')}
    </span>
  )
}

function CopyBtn({ text }) {
  const [done, setDone] = useState(false)
  return (
    <button
      onClick={() => { navigator.clipboard.writeText(text); setDone(true); setTimeout(() => setDone(false), 1500) }}
      title="Copy"
      className="p-1.5 rounded-lg text-text-muted hover:text-text-primary hover:bg-gray-100 transition-colors"
    >
      {done ? <Check size={13} /> : <Copy size={13} />}
    </button>
  )
}

// ── shared card ───────────────────────────────────────────────────────────────

function Card({ icon: Icon, title, action, children, collapsible = false, defaultOpen = true }) {
  const [open, setOpen] = useState(defaultOpen)
  const header = (
    <div
      className={`flex items-center justify-between px-5 py-3.5 border-b border-gray-100 ${collapsible ? 'cursor-pointer select-none' : ''}`}
      onClick={collapsible ? () => setOpen(v => !v) : undefined}
    >
      <div className="flex items-center gap-2.5">
        <div className="w-7 h-7 rounded-lg flex items-center justify-center bg-[#6C5CE7]/10 flex-shrink-0">
          <Icon size={14} className="text-[#6C5CE7]" />
        </div>
        <h2 className="text-sm font-bold text-text-primary">{title}</h2>
      </div>
      <div className="flex items-center gap-1.5">
        {action}
        {collapsible && (open
          ? <ChevronUp size={14} className="text-text-muted" />
          : <ChevronDown size={14} className="text-text-muted" />)}
      </div>
    </div>
  )
  return (
    <div className="bg-white border border-pando-green-100 rounded-2xl shadow-card overflow-hidden">
      {header}
      {(!collapsible || open) && <div className="px-5 py-4">{children}</div>}
    </div>
  )
}

// ── label / input helpers ─────────────────────────────────────────────────────

function Label({ children, required }) {
  return (
    <label className="block text-xs font-semibold text-text-muted uppercase tracking-wider mb-1.5">
      {children}{required && <span className="ml-0.5 text-danger">*</span>}
    </label>
  )
}

function ErrorBanner({ message }) {
  return (
    <div className="flex items-start gap-2 px-3.5 py-3 rounded-xl bg-danger-bg border border-danger/20 text-sm text-danger">
      <XCircle size={15} className="flex-shrink-0 mt-0.5" />
      {message}
    </div>
  )
}

// ── single file zone (used for field mapping .xlsx) ──────────────────────────

function FileZone({ label, required, file, onFile, accept, hint }) {
  const ref = useRef()
  return (
    <div>
      <Label required={required}>{label}</Label>
      <div
        onClick={() => ref.current?.click()}
        className="flex items-center gap-2.5 px-3.5 py-2.5 rounded-xl border-2 border-dashed border-gray-200 hover:border-[#6C5CE7] cursor-pointer transition-colors bg-gray-50 hover:bg-[#6C5CE7]/5"
      >
        <Upload size={13} className="text-[#6C5CE7] flex-shrink-0" />
        <span className={`text-sm truncate flex-1 ${file ? 'text-text-primary font-medium' : 'text-text-muted'}`}>
          {file ? file.name : `Click to upload ${accept}`}
        </span>
        {file && (
          <button type="button" onClick={e => { e.stopPropagation(); onFile(null) }}
            className="text-text-muted hover:text-danger transition-colors flex-shrink-0">
            <XCircle size={14} />
          </button>
        )}
      </div>
      {hint && <p className="mt-1 text-xs text-text-muted">{hint}</p>}
      <input ref={ref} type="file" accept={accept} className="hidden"
        onChange={e => onFile(e.target.files?.[0] ?? null)} />
    </div>
  )
}

const MAX_PDFS = 3

// ── multi-pdf picker ──────────────────────────────────────────────────────────

function MultiPdfZone({ files, onChange }) {
  const ref = useRef()

  const addFiles = (incoming) => {
    const valid = Array.from(incoming).filter(f => f.name.toLowerCase().endsWith('.pdf'))
    if (!valid.length) return
    onChange(prev => {
      const combined = [...prev, ...valid]
      // deduplicate by name, cap at MAX_PDFS
      const seen = new Set()
      return combined.filter(f => {
        if (seen.has(f.name)) return false
        seen.add(f.name)
        return true
      }).slice(0, MAX_PDFS)
    })
  }

  const remove = (name) => onChange(prev => prev.filter(f => f.name !== name))

  const onDrop = (e) => {
    e.preventDefault()
    addFiles(e.dataTransfer.files)
  }

  return (
    <div>
      <Label required>Invoice PDF{MAX_PDFS > 1 ? 's' : ''}</Label>

      {/* drop zone — only shown when below limit */}
      {files.length < MAX_PDFS && (
        <div
          onClick={() => ref.current?.click()}
          onDragOver={e => e.preventDefault()}
          onDrop={onDrop}
          className="flex items-center gap-2.5 px-3.5 py-2.5 rounded-xl border-2 border-dashed border-gray-200 hover:border-[#6C5CE7] cursor-pointer transition-colors bg-gray-50 hover:bg-[#6C5CE7]/5"
        >
          <Upload size={13} className="text-[#6C5CE7] flex-shrink-0" />
          <span className="text-sm text-text-muted flex-1">
            Click or drag to upload PDF{files.length > 0 ? ' (add more)' : ''}
          </span>
          <span className="text-xs text-text-muted flex-shrink-0">
            {files.length}/{MAX_PDFS}
          </span>
        </div>
      )}
      <input
        ref={ref}
        type="file"
        accept=".pdf"
        multiple
        className="hidden"
        onChange={e => { addFiles(e.target.files); e.target.value = '' }}
      />

      {/* file list */}
      {files.length > 0 && (
        <div className="mt-2 flex flex-col gap-1.5">
          {files.map((f, i) => (
            <div key={f.name}
              className="flex items-center gap-2 px-3 py-2 rounded-lg bg-gray-50 border border-gray-200">
              <FileText size={13} className="text-[#6C5CE7] flex-shrink-0" />
              <span className="text-xs font-medium text-text-primary truncate flex-1">{f.name}</span>
              <span className="text-xs text-text-muted flex-shrink-0 mr-1">
                {(f.size / 1024).toFixed(0)} KB
              </span>
              <button type="button" onClick={() => remove(f.name)}
                className="text-text-muted hover:text-danger transition-colors flex-shrink-0">
                <XCircle size={13} />
              </button>
            </div>
          ))}
        </div>
      )}

      {files.length >= MAX_PDFS && (
        <p className="mt-1 text-xs text-text-muted">Maximum {MAX_PDFS} PDFs reached. Remove one to add another.</p>
      )}
    </div>
  )
}

// ── left panel: upload form ───────────────────────────────────────────────────

function UploadPanel({ onResult, loading, setLoading }) {
  const [pdfs, setPdfs]     = useState([])
  const [schema, setSchema] = useState('')
  const [sample, setSample] = useState('')
  const [mapping, setMapping] = useState(null)
  const [error, setError]   = useState('')

  const submit = async e => {
    e.preventDefault()
    setError('')
    if (!pdfs.length)   return setError('Please upload at least one PDF invoice.')
    if (!schema.trim()) return setError('JSON schema is required.')
    if (!sample.trim()) return setError('Sample extraction prompt is required.')
    try {
      const p = JSON.parse(schema)
      if (typeof p !== 'object' || Array.isArray(p)) throw new Error()
    } catch { return setError('Schema must be a valid JSON object.') }
    setLoading(true)
    try { onResult(await pgExtract({ pdfFiles: pdfs, schemaJson: schema, samplePrompt: sample, fieldMapping: mapping })) }
    catch (err) { setError(err.message) }
    finally { setLoading(false) }
  }

  const ta = 'w-full rounded-xl text-sm p-3 resize-y outline-none font-mono'

  return (
    <Card icon={Upload} title="Upload & Configure">
      <form onSubmit={submit} className="flex flex-col gap-4">

        <MultiPdfZone files={pdfs} onChange={setPdfs} />

        <div>
          <Label required>JSON Schema</Label>
          <textarea rows={8} className={ta} spellCheck={false}
            placeholder={'{\n  "type": "object",\n  "properties": {\n    "invoice_number": { "type": "string" }\n  }\n}'}
            value={schema} onChange={e => setSchema(e.target.value)} />
        </div>

        <div>
          <Label required>Sample Extraction Prompt</Label>
          <textarea rows={6} className={ta} spellCheck={false}
            placeholder="Paste one existing carrier extraction prompt as a style reference…"
            value={sample} onChange={e => setSample(e.target.value)} />
          <p className="mt-1 text-xs text-text-muted">One prompt, not an entire library.</p>
        </div>

        <FileZone label="Field Mapping (optional)" file={mapping} onFile={setMapping} accept=".xlsx"
          hint="Columns: Table Name, Column Name, Field Location in Actual Invoice, Remarks" />

        {error && <ErrorBanner message={error} />}

        <button type="submit" disabled={loading}
          className="flex items-center justify-center gap-2 py-2.5 rounded-xl text-sm font-semibold text-white disabled:opacity-60 transition-opacity"
          style={{ background: '#6C5CE7' }}>
          {loading ? <Loader2 size={14} className="animate-spin" /> : <Sparkles size={14} />}
          {loading ? 'Processing…' : 'Generate Prompt & Extract'}
        </button>
      </form>
    </Card>
  )
}

// ── right panel: generated prompt ────────────────────────────────────────────

function PromptPanel({ result }) {
  const { session_prompt, prompt_coverage, mapping_match, prompt_diff } = result
  return (
    <Card icon={Sparkles} title="Generated Prompt" collapsible defaultOpen
      action={<CopyBtn text={session_prompt} />}>
      <div className="flex flex-col gap-4">

        <pre className="text-xs leading-relaxed bg-gray-50 border border-gray-200 rounded-xl p-3.5 overflow-auto whitespace-pre-wrap text-text-primary"
          style={{ maxHeight: 260 }}>
          {session_prompt || '—'}
        </pre>

        {prompt_coverage && (
          <div>
            <div className="flex items-center justify-between mb-1.5">
              <p className="text-xs font-semibold text-text-muted uppercase tracking-wider">
                Schema Coverage
              </p>
              <span className="text-xs font-bold text-text-primary">
                {pct(prompt_coverage.coverage_ratio)}%
              </span>
            </div>
            <div className="w-full h-1.5 rounded-full bg-gray-100 mb-2.5">
              <div className="h-1.5 rounded-full transition-all"
                style={{
                  width: `${pct(prompt_coverage.coverage_ratio)}%`,
                  background: pct(prompt_coverage.coverage_ratio) >= 75 ? '#00b894' : '#fdcb6e',
                }} />
            </div>
            <div className="flex flex-wrap gap-1.5">
              {(prompt_coverage.covered ?? []).map(f => (
                <span key={f} className="px-2 py-0.5 rounded-full text-xs font-medium bg-pando-green-50 text-pando-green border border-pando-green-200">{f}</span>
              ))}
              {(prompt_coverage.missing ?? []).map(f => (
                <span key={f} className="px-2 py-0.5 rounded-full text-xs font-medium bg-danger-bg text-danger border border-danger/20">{f}</span>
              ))}
            </div>
          </div>
        )}

        {mapping_match?.has_mapping && (
          <div className="px-3.5 py-3 rounded-xl bg-gray-50 border border-gray-200 text-xs text-text-muted">
            <span className="font-semibold text-text-primary">Field Mapping: </span>
            {mapping_match.summary}
          </div>
        )}

        {prompt_diff?.changed && (
          <div>
            <p className="text-xs font-semibold text-text-muted uppercase tracking-wider mb-1.5">
              Prompt Diff (+{prompt_diff.stats?.lines_added} / −{prompt_diff.stats?.lines_removed})
            </p>
            <pre className="text-xs bg-gray-50 border border-gray-200 rounded-xl p-3 overflow-auto"
              style={{ maxHeight: 180 }}>
              {prompt_diff.unified_diff}
            </pre>
          </div>
        )}
      </div>
    </Card>
  )
}

// ── right panel: extracted fields ─────────────────────────────────────────────

function FieldsPanel({ result, onUpdated, loading, setLoading }) {
  const { fields = [], thread_id, status, feedback, accuracy_score } = result
  const [checked, setChecked] = useState({})
  const [genericNote, setGenericNote] = useState('')
  const [error, setError] = useState('')

  const toggle = path => setChecked(prev => {
    const next = { ...prev }
    next[path] !== undefined ? delete next[path] : (next[path] = '')
    return next
  })

  const doCorrect = async () => {
    setError('')
    const corrections = Object.entries(checked).map(([path, note]) => ({
      path, note, current_value: fields.find(f => f.path === path)?.value ?? null,
    }))
    if (!corrections.length && !genericNote.trim()) return setError('Mark at least one field or add a note.')
    setLoading(true)
    try { setChecked({}); setGenericNote(''); onUpdated(await pgCorrect({ threadId: thread_id, corrections, genericNote })) }
    catch (err) { setError(err.message) }
    finally { setLoading(false) }
  }

  const doDocling = async () => {
    setLoading(true); setError('')
    try { onUpdated(await pgDoclingRetry({ threadId: thread_id })) }
    catch (err) { setError(err.message) }
    finally { setLoading(false) }
  }

  const doFeedback = async retry => {
    setLoading(true); setError('')
    try { onUpdated(await pgFeedback({ threadId: thread_id, retry })) }
    catch (err) { setError(err.message) }
    finally { setLoading(false) }
  }

  return (
    <Card icon={FileText} title="Extracted Fields" collapsible defaultOpen>
      <div className="flex flex-col gap-3.5">

        {/* status row */}
        <div className="flex items-center gap-2 flex-wrap">
          <StatusPill status={status} />
          <AccuracyBadge score={accuracy_score} />
          {result.used_docling && (
            <span className="px-2.5 py-0.5 rounded-full text-xs font-semibold border bg-[#6C5CE7]/10 text-[#6C5CE7] border-[#6C5CE7]/30">
              Docling OCR
            </span>
          )}
          {result.model && <span className="text-xs text-text-muted ml-auto">{result.model}</span>}
        </div>

        {/* HITL interrupt */}
        {status === 'awaiting_feedback' && feedback && (
          <div className="rounded-xl p-4 bg-warning-bg border border-warning/30">
            <div className="flex items-start gap-2 mb-3">
              <AlertTriangle size={14} className="text-warning flex-shrink-0 mt-0.5" />
              <p className="text-sm text-warning font-medium">
                {feedback.message} — accuracy {pct(feedback.accuracy)}%
              </p>
            </div>
            <div className="flex gap-2">
              <button onClick={() => doFeedback(true)} disabled={loading}
                className="px-4 py-1.5 rounded-lg text-sm font-semibold text-white disabled:opacity-60"
                style={{ background: '#6C5CE7' }}>
                Retry with Docling
              </button>
              <button onClick={() => doFeedback(false)} disabled={loading}
                className="px-4 py-1.5 rounded-lg text-sm font-semibold text-text-primary bg-white border border-gray-200 hover:bg-gray-50 disabled:opacity-60 transition-colors">
                Accept as-is
              </button>
            </div>
          </div>
        )}

        {result.fatal_error && <ErrorBanner message={result.fatal_error} />}

        {(result.validation_errors ?? []).length > 0 && (
          <div className="rounded-xl p-3.5 bg-danger-bg border border-danger/20">
            <p className="text-xs font-semibold text-danger mb-1.5">Validation issues</p>
            <ul className="text-xs space-y-0.5 text-text-muted">
              {result.validation_errors.map((e, i) => <li key={i}>• {e}</li>)}
            </ul>
          </div>
        )}

        {/* KV table */}
        {fields.length > 0 && (
          <div className="rounded-xl border border-gray-200 overflow-hidden">
            <div className="grid px-4 py-2 bg-gray-50 border-b border-gray-200 text-xs font-semibold uppercase tracking-wider text-text-muted"
              style={{ gridTemplateColumns: '28px 1fr 1fr' }}>
              <span />
              <span>Field</span>
              <span>Value</span>
            </div>
            <div className="divide-y divide-gray-100 overflow-y-auto" style={{ maxHeight: 340 }}>
              {fields.map(f => {
                const wrong = checked[f.path] !== undefined
                return (
                  <div key={f.path}
                    className={`grid items-start gap-2 px-4 py-2.5 transition-colors ${wrong ? 'bg-danger-bg' : 'hover:bg-gray-50'}`}
                    style={{ gridTemplateColumns: '28px 1fr 1fr' }}>
                    <input type="checkbox" checked={wrong} onChange={() => toggle(f.path)}
                      className="mt-0.5 w-3.5 h-3.5 cursor-pointer accent-red-500" />
                    {/* break-all prevents path truncation */}
                    <span className="text-xs font-mono text-text-muted break-all leading-snug">{f.path}</span>
                    <span className={`text-xs break-words leading-snug ${wrong ? 'text-danger' : 'text-text-primary'}`}>
                      {f.value === null || f.value === undefined
                        ? <span className="italic text-text-muted">null</span>
                        : typeof f.value === 'object'
                          ? <span className="text-text-muted text-xs">{JSON.stringify(f.value)}</span>
                          : String(f.value)}
                    </span>
                    {wrong && (
                      <div className="col-start-2 col-span-2 mt-1">
                        <input type="text" placeholder="Note about this field (optional)"
                          value={checked[f.path]}
                          onChange={e => setChecked(prev => ({ ...prev, [f.path]: e.target.value }))}
                          className="w-full text-xs" />
                      </div>
                    )}
                  </div>
                )
              })}
            </div>
          </div>
        )}

        {/* actions */}
        {fields.length > 0 && status !== 'awaiting_feedback' && (
          <div className="flex flex-col gap-2.5 pt-0.5">
            <textarea rows={2} placeholder="Generic note for all fields (e.g. 'normalize all nulls to empty string')"
              value={genericNote} onChange={e => setGenericNote(e.target.value)}
              className="w-full text-sm resize-none" />
            {error && <p className="text-xs text-danger">{error}</p>}
            <div className="flex flex-wrap gap-2">
              <button onClick={doCorrect}
                disabled={loading || (!Object.keys(checked).length && !genericNote.trim())}
                className="flex items-center gap-1.5 px-4 py-2 rounded-xl text-sm font-semibold text-white disabled:opacity-50 transition-opacity"
                style={{ background: '#6C5CE7' }}>
                {loading ? <Loader2 size={13} className="animate-spin" /> : <Sparkles size={13} />}
                Refine Prompt & Remap
              </button>
              <button onClick={doDocling} disabled={loading}
                className="flex items-center gap-1.5 px-4 py-2 rounded-xl text-sm font-semibold text-text-primary bg-white border border-gray-200 hover:bg-gray-50 disabled:opacity-50 transition-colors">
                {loading ? <Loader2 size={13} className="animate-spin" /> : <RefreshCw size={13} />}
                Retry with Docling
              </button>
            </div>
          </div>
        )}
      </div>
    </Card>
  )
}

// ── right panel: chat ─────────────────────────────────────────────────────────

function ChatPanel({ threadId }) {
  const [messages, setMessages] = useState([])
  const [input, setInput]       = useState('')
  const [loading, setLoading]   = useState(false)
  const [error, setError]       = useState('')
  const bottomRef = useRef()

  useEffect(() => { bottomRef.current?.scrollIntoView({ behavior: 'smooth' }) }, [messages])

  const send = async () => {
    const q = input.trim()
    if (!q || loading) return
    setInput(''); setError('')
    setMessages(prev => [...prev, { role: 'user', content: q }])
    setLoading(true)
    try {
      const res = await pgAsk({ threadId, prompt: q })
      setMessages(prev => [...prev, { role: 'assistant', content: res.response ?? '(no response)' }])
    } catch (err) { setError(err.message) }
    finally { setLoading(false) }
  }

  return (
    <Card icon={MessageSquare} title="Ask About the Invoice" collapsible defaultOpen>
      <div className="flex flex-col gap-3">
        <div className="flex flex-col gap-3 overflow-y-auto" style={{ minHeight: 80, maxHeight: 280 }}>
          {messages.length === 0 && (
            <p className="text-sm text-text-muted text-center py-5">
              Ask anything about the extracted invoice data
            </p>
          )}
          {messages.map((m, i) => (
            <div key={i} className={`flex ${m.role === 'user' ? 'justify-end' : 'justify-start'}`}>
              <div className={`px-3.5 py-2.5 rounded-2xl text-sm max-w-[80%] whitespace-pre-wrap leading-relaxed ${
                m.role === 'user'
                  ? 'text-white rounded-br-sm'
                  : 'bg-gray-50 border border-gray-200 text-text-primary rounded-bl-sm'
              }`} style={m.role === 'user' ? { background: '#6C5CE7' } : {}}>
                {m.content}
              </div>
            </div>
          ))}
          {loading && (
            <div className="flex justify-start">
              <div className="px-3.5 py-2.5 rounded-2xl rounded-bl-sm bg-gray-50 border border-gray-200">
                <Loader2 size={14} className="animate-spin text-[#6C5CE7]" />
              </div>
            </div>
          )}
          <div ref={bottomRef} />
        </div>
        {error && <p className="text-xs text-danger">{error}</p>}
        <div className="flex gap-2 items-end">
          <textarea rows={2} placeholder="Ask about the invoice… (Enter to send)"
            value={input} onChange={e => setInput(e.target.value)}
            onKeyDown={e => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); send() } }}
            className="flex-1 text-sm resize-none" />
          <button onClick={send} disabled={loading || !input.trim()}
            className="self-end w-9 h-9 flex items-center justify-center rounded-xl text-white disabled:opacity-50 transition-opacity flex-shrink-0"
            style={{ background: '#6C5CE7' }}>
            <Send size={14} />
          </button>
        </div>
      </div>
    </Card>
  )
}

// ── page ──────────────────────────────────────────────────────────────────────

export default function PromptGenerator() {
  const [result, setResult]     = useState(null)
  const [loading, setLoading]   = useState(false)
  const [modelInfo, setModelInfo] = useState(null)

  useEffect(() => { pgHealth().then(setModelInfo).catch(() => {}) }, [])

  return (
    <div className="flex flex-col gap-5">

      {/* page heading */}
      <div className="flex items-start justify-between gap-4 flex-wrap">
        <div>
          <h1 className="text-2xl font-bold text-text-primary tracking-tight">Prompt Generator</h1>
          <p className="text-sm text-text-muted mt-1">
            Upload a PDF invoice, JSON schema and a sample carrier prompt to auto-generate an extraction prompt and structured output.
          </p>
        </div>
        {modelInfo && (
          <span className="flex items-center gap-1.5 px-3 py-1.5 rounded-xl text-xs font-semibold bg-[#6C5CE7]/10 text-[#6C5CE7] border border-[#6C5CE7]/20 whitespace-nowrap self-start">
            <Sparkles size={11} />
            {modelInfo.model}
          </span>
        )}
      </div>

      {/* two-column layout */}
      <div className="grid grid-cols-1 xl:grid-cols-2 gap-5 items-start">

        {/* LEFT — upload form (sticky so it stays in view while scrolling results) */}
        <div className="xl:sticky xl:top-20">
          <UploadPanel onResult={setResult} loading={loading} setLoading={setLoading} />
          {!result && (
            <p className="mt-3 flex items-start gap-1.5 text-xs text-text-muted">
              <Info size={12} className="flex-shrink-0 mt-0.5" />
              The field mapping (.xlsx) is optional but improves prompt quality by providing exact field locations.
            </p>
          )}
        </div>

        {/* RIGHT — results stack (only after extraction) */}
        {result ? (
          <div className="flex flex-col gap-5">
            <PromptPanel result={result} />
            <FieldsPanel result={result} onUpdated={setResult} loading={loading} setLoading={setLoading} />
            <ChatPanel threadId={result.thread_id} />
          </div>
        ) : (
          /* placeholder so layout doesn't collapse on initial load */
          <div className="hidden xl:flex items-center justify-center rounded-2xl border-2 border-dashed border-gray-200 text-sm text-text-muted"
            style={{ minHeight: 300 }}>
            Results will appear here after extraction
          </div>
        )}
      </div>
    </div>
  )
}
