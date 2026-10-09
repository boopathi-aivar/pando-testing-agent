import { useEffect, useState } from 'react'
import { Loader2 } from 'lucide-react'
import {
  createObservabilityView,
  discoverObservabilitySchema,
  getObservabilityView,
  updateObservabilityView,
} from '../../api/client'
import CustomSelect from '../ui/CustomSelect'

const ACCOUNT_OPTIONS = [
  { value: 'ops', label: 'ops' },
  { value: 'meta', label: 'meta' },
]

const ACTION_DEFS = [
  { id: 'action_view_pdf', label: 'View PDF', key: 'action_view_pdf' },
  { id: 'action_cloudwatch', label: 'View CloudWatch log', key: 'action_cloudwatch' },
  { id: 'action_retrigger', label: 'Re-trigger', key: 'action_retrigger' },
]

export default function ObservabilityViewForm({
  viewId = null,
  onCancel,
  onSaved,
  compact = false,
}) {
  const isEdit = Boolean(viewId)

  const [name, setName] = useState('')
  const [description, setDescription] = useState('')
  const [tableName, setTableName] = useState('')
  const [account, setAccount] = useState('ops')
  const [fields, setFields] = useState([])
  const [selected, setSelected] = useState(new Set())
  const [actions, setActions] = useState({
    action_view_pdf: true,
    action_cloudwatch: true,
    action_retrigger: true,
  })
  const [sampleCount, setSampleCount] = useState(null)
  const [note, setNote] = useState('')
  const [discovered, setDiscovered] = useState(false)

  const [loading, setLoading] = useState(isEdit)
  const [discovering, setDiscovering] = useState(false)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState(null)

  useEffect(() => {
    if (!isEdit) return
    let cancelled = false
    ;(async () => {
      setLoading(true)
      setError(null)
      try {
        const view = await getObservabilityView(viewId)
        if (cancelled) return
        setName(view.name || '')
        setDescription(view.description || '')
        setTableName(view.table_name || '')
        setAccount(view.account || 'ops')
        setSelected(new Set(view.selected_fields || []))
        setActions({
          action_view_pdf: view.action_view_pdf !== false,
          action_cloudwatch: view.action_cloudwatch !== false,
          action_retrigger: view.action_retrigger !== false,
        })
        const discoveredFields = (view.discovered_fields || []).map((id) => ({
          id,
          label: id,
          kind: 'data',
          type: 'string',
        }))
        setFields(discoveredFields)
        setDiscovered(discoveredFields.length > 0)
      } catch (err) {
        if (!cancelled) setError(err.message)
      } finally {
        if (!cancelled) setLoading(false)
      }
    })()
    return () => {
      cancelled = true
    }
  }, [isEdit, viewId])

  async function handleDiscover(e) {
    e?.preventDefault()
    if (!tableName.trim()) {
      setError('Enter a DynamoDB table name')
      return
    }
    setDiscovering(true)
    setError(null)
    try {
      const result = await discoverObservabilitySchema(tableName.trim(), account)
      setFields(result.fields || [])
      setSampleCount(result.sample_count ?? 0)
      setNote(result.note || '')
      setDiscovered(true)
      const suggested = new Set(result.suggested_selected_fields || [])
      if (suggested.size === 0 && (result.fields || []).length) {
        result.fields.slice(0, 6).forEach((f) => suggested.add(f.id))
      }
      setSelected(suggested)
      const sa = result.suggested_actions || {}
      setActions({
        action_view_pdf: sa.action_view_pdf !== false,
        action_cloudwatch: sa.action_cloudwatch !== false,
        action_retrigger: sa.action_retrigger !== false,
      })
    } catch (err) {
      setError(err.message)
    } finally {
      setDiscovering(false)
    }
  }

  function toggleField(id) {
    setSelected((prev) => {
      const next = new Set(prev)
      if (next.has(id)) next.delete(id)
      else next.add(id)
      return next
    })
  }

  function toggleAction(key) {
    setActions((prev) => ({ ...prev, [key]: !prev[key] }))
  }

  async function handleSave(e) {
    e.preventDefault()
    if (!name.trim()) {
      setError('Name is required')
      return
    }
    if (!tableName.trim()) {
      setError('Table name is required')
      return
    }
    if (!discovered && !isEdit) {
      setError('Discover schema before saving')
      return
    }
    setSaving(true)
    setError(null)
    const payload = {
      name: name.trim(),
      description: description.trim(),
      table_name: tableName.trim(),
      account,
      selected_fields: [...selected],
      discovered_fields: fields.map((f) => f.id),
      ...actions,
    }
    try {
      const saved = isEdit
        ? await updateObservabilityView(viewId, payload)
        : await createObservabilityView(payload)
      onSaved?.(saved)
    } catch (err) {
      setError(err.message)
    } finally {
      setSaving(false)
    }
  }

  if (loading) {
    return (
      <div className="flex items-center gap-2 text-text-secondary text-sm py-6">
        <Loader2 size={16} className="animate-spin" />
        Loading…
      </div>
    )
  }

  return (
    <form onSubmit={handleSave} className={compact ? 'space-y-4' : 'space-y-5'}>
      {error && (
        <div className="bg-danger-bg border border-danger/20 rounded-xl p-3">
          <p className="text-danger text-sm font-medium">{error}</p>
        </div>
      )}

      <div className="grid sm:grid-cols-2 gap-4">
        <div>
          <label className="block text-sm font-medium text-text-secondary mb-1.5">Name *</label>
          <input
            type="text"
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder="e.g. Acme Logs"
            className="w-full text-sm"
            required
          />
        </div>
        <div>
          <label className="block text-sm font-medium text-text-secondary mb-1.5">Description</label>
          <input
            type="text"
            value={description}
            onChange={(e) => setDescription(e.target.value)}
            placeholder="Optional"
            className="w-full text-sm"
          />
        </div>
      </div>

      <div className="grid sm:grid-cols-3 gap-4">
        <div className="sm:col-span-2">
          <label className="block text-sm font-medium text-text-secondary mb-1.5">
            DynamoDB table *
          </label>
          <input
            type="text"
            value={tableName}
            onChange={(e) => {
              setTableName(e.target.value)
              setDiscovered(false)
            }}
            placeholder="e.g. pando-acme-invoice-logs"
            className="w-full text-sm font-mono"
            required
            disabled={isEdit}
          />
        </div>
        <CustomSelect
          label="Account"
          value={account}
          options={ACCOUNT_OPTIONS}
          disabled={isEdit}
          onChange={(v) => {
            setAccount(v)
            setDiscovered(false)
          }}
          className="w-full"
          triggerClassName="max-w-none h-[38px] text-sm font-medium"
          menuClassName="max-w-none w-full"
        />
      </div>

      <div className="flex flex-wrap items-center gap-3">
        <button
          type="button"
          onClick={handleDiscover}
          disabled={discovering || !tableName.trim()}
          className="inline-flex items-center gap-2 px-4 py-2 rounded-xl text-sm font-semibold text-white disabled:opacity-50"
          style={{ background: '#6C5CE7' }}
        >
          {discovering ? <Loader2 size={14} className="animate-spin" /> : null}
          {discovering ? 'Discovering…' : discovered ? 'Re-discover schema' : 'Discover schema'}
        </button>
        {sampleCount != null && (
          <p className="text-xs text-text-secondary">
            Sampled {sampleCount} item{sampleCount === 1 ? '' : 's'}.
            {note ? ` ${note}` : ''}
          </p>
        )}
      </div>

      {(discovered || (isEdit && fields.length > 0)) && (
        <div className="grid lg:grid-cols-3 gap-5 border border-border rounded-xl p-4 bg-background/40">
          <div className="lg:col-span-2">
            <h4 className="text-sm font-semibold text-text-primary">Table columns</h4>
            <p className="text-xs text-text-secondary mt-0.5 mb-2">
              Checked fields appear as columns in the list view.
            </p>
            <ul className="grid sm:grid-cols-2 gap-1 max-h-56 overflow-y-auto">
              {fields.length === 0 ? (
                <li className="text-sm text-text-secondary col-span-2">
                  No fields found. Re-discover after data exists, or save with actions only.
                </li>
              ) : (
                fields.map((f) => (
                  <li key={f.id}>
                    <label className="flex items-center gap-2 text-sm text-text-primary cursor-pointer rounded-lg px-2 py-1.5 hover:bg-surface">
                      <input
                        type="checkbox"
                        checked={selected.has(f.id)}
                        onChange={() => toggleField(f.id)}
                        className="rounded border-border"
                      />
                      <span className="font-mono text-xs truncate">{f.label}</span>
                      {f.type && (
                        <span className="ml-auto text-[10px] uppercase tracking-wide text-text-secondary">
                          {f.type}
                        </span>
                      )}
                    </label>
                  </li>
                ))
              )}
            </ul>
          </div>

          <div>
            <h4 className="text-sm font-semibold text-text-primary">Actions</h4>
            <p className="text-xs text-text-secondary mt-0.5 mb-2">
              On by default. Uncheck to hide.
            </p>
            <ul className="space-y-2">
              {ACTION_DEFS.map((a) => (
                <li key={a.id}>
                  <label className="flex items-center gap-2 text-sm text-text-primary cursor-pointer">
                    <input
                      type="checkbox"
                      checked={actions[a.key]}
                      onChange={() => toggleAction(a.key)}
                      className="rounded border-border"
                    />
                    {a.label}
                  </label>
                </li>
              ))}
            </ul>
          </div>
        </div>
      )}

      <div className="flex items-center gap-3 pt-1">
        <button
          type="submit"
          disabled={saving || (!discovered && !isEdit)}
          className="inline-flex items-center gap-2 px-4 py-2.5 rounded-xl text-sm font-semibold text-white disabled:opacity-50"
          style={{ background: '#6C5CE7' }}
        >
          {saving ? <Loader2 size={14} className="animate-spin" /> : null}
          {saving ? 'Saving…' : isEdit ? 'Save changes' : 'Save project'}
        </button>
        {onCancel && (
          <button
            type="button"
            onClick={onCancel}
            className="text-sm text-text-secondary hover:text-text-primary px-2 py-2"
          >
            Cancel
          </button>
        )}
      </div>
    </form>
  )
}
