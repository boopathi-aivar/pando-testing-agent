import { useState, useEffect } from 'react'
import { useParams, useNavigate } from 'react-router-dom'
import { useProject } from '../hooks/useProjects'
import { createProject, updateProject } from '../api/client'
import StepIndicator from '../components/configure/StepIndicator'
import FileSlotRow from '../components/configure/FileSlotRow'
import RunTestModal from '../components/results/RunTestModal'

const STEPS = ['Basic Info', 'Source Files', 'Test Config', 'Review & Save']

// Slots that are auto-collected from CloudWatch — never shown to the user
const CLOUDWATCH_SLOT_IDS = new Set(['prompt-template', 'llm-response-sample'])
// Slots removed from the product — filter out if present in saved data
const REMOVED_SLOT_IDS = new Set(['carrier-mapping', 'custom-mapping-1', 'custom-mapping-2'])

const DEFAULT_FILE_SLOTS = [
  { id: 'field-mapping-sheet', label: 'Field Mapping Sheet', required: false, enabled: false, s3_bucket: '', s3_key: '', description: 'Excel/CSV mapping expected output fields to sources', isCustom: false },
  { id: 'charge-mapping',      label: 'Charge Mapping',      required: false, enabled: false, s3_bucket: '', s3_key: '', description: 'Charge code mapping stored in S3',                  isCustom: false },
  { id: 'country-code-mapping',label: 'Country Code Mapping',required: false, enabled: false, s3_bucket: '', s3_key: '', description: 'Country code lookup table',                        isCustom: false },
]

function makeCustomSlot() {
  return { id: `custom-${Date.now()}`, label: 'Custom Mapping', required: false, enabled: false, s3_bucket: '', s3_key: '', description: 'Custom mapping file', isCustom: true }
}

function slugify(str) {
  return str.toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, '')
}


export default function Configure() {
  const { projectId } = useParams()
  const { project } = useProject(projectId !== 'new' ? projectId : null)
  const navigate = useNavigate()
  const [step, setStep] = useState(0)
  const [saving, setSaving] = useState(false)
  const [showRunModal, setShowRunModal] = useState(false)
  const [savedProject, setSavedProject] = useState(null)
  const [hydrated, setHydrated] = useState(false)

  const [config, setConfig] = useState({
    project_name: '', project_id: '', cloudwatch_log_group: '',
    target_api_url: `${window.location.origin}/api`, file_slots: DEFAULT_FILE_SLOTS,
    scoring_weights: { charge_fields: 25, address_fields: 25, date_fields: 25, amount_fields: 25 },
    mandatory_fields: [],
  })

  useEffect(() => {
    if (project && !hydrated) {
      setConfig({
        project_name: project.project_name ?? '',
        project_id: project.project_id ?? '',
        cloudwatch_log_group: project.cloudwatch_log_group ?? '',
        target_api_url: project.target_api_url || `${window.location.origin}/api`,
        file_slots: (project.file_slots ?? DEFAULT_FILE_SLOTS).filter(
          (s) => !CLOUDWATCH_SLOT_IDS.has(s.id) && !REMOVED_SLOT_IDS.has(s.id)
        ),
        scoring_weights: project.scoring_weights ?? { charge_fields: 25, address_fields: 25, date_fields: 25, amount_fields: 25 },
        mandatory_fields: project.mandatory_fields ?? [],
      })
      setHydrated(true)
    }
  }, [project, hydrated])

  const update = (field, value) => setConfig((prev) => ({ ...prev, [field]: value }))
  const updateSlot = (id, updated) => setConfig((prev) => ({ ...prev, file_slots: prev.file_slots.map((s) => (s.id === id ? updated : s)) }))
  const addSlot    = () => setConfig((prev) => ({ ...prev, file_slots: [...prev.file_slots, makeCustomSlot()] }))
  const removeSlot = (id) => setConfig((prev) => ({ ...prev, file_slots: prev.file_slots.filter((s) => s.id !== id) }))
  const step1Valid = config.project_name && config.project_id && config.cloudwatch_log_group

  const saveProject = async () => {
    setSaving(true)
    try {
      const payload = { ...config, status: 'configured' }
      const saved = projectId === 'new' ? await createProject(payload) : await updateProject(projectId, payload)
      setSavedProject(saved)
      return saved
    } finally {
      setSaving(false)
    }
  }

  return (
    <div className="p-6 max-w-3xl mx-auto">
      <StepIndicator steps={STEPS} currentStep={step} />

      <div className="bg-white border border-border rounded-2xl shadow-card">
        <div className="p-8">
          {step === 0 && <Step1 config={config} update={update} onNameChange={(name) => { update('project_name', name); if (!config.project_id || config.project_id === slugify(config.project_name)) update('project_id', slugify(name)) }} />}
          {step === 1 && <Step2 config={config} updateSlot={updateSlot} addSlot={addSlot} removeSlot={removeSlot} />}
          {step === 2 && <Step3 config={config} update={update} />}
          {step === 3 && <Step4 config={config} />}
        </div>

        <div className="flex items-center justify-between px-8 py-5 border-t border-border bg-background rounded-b-2xl">
          <button
            onClick={() => setStep((s) => s - 1)}
            disabled={step === 0}
            className="px-5 py-2 rounded-xl border border-border text-text-secondary text-sm font-medium hover:border-pando-green hover:text-pando-green transition-colors disabled:opacity-30 disabled:cursor-not-allowed"
          >
            Back
          </button>
          {step < 3 ? (
            <button
              onClick={() => setStep((s) => s + 1)}
              disabled={step === 0 && !step1Valid}
              className="px-6 py-2 rounded-xl bg-pando-green hover:bg-pando-green-600 text-white text-sm font-semibold transition-colors shadow-sm disabled:opacity-40 disabled:cursor-not-allowed"
            >
              Continue
            </button>
          ) : (
            <div className="flex items-center gap-3">
              <button
                onClick={async () => { await saveProject(); navigate('/') }}
                disabled={saving}
                className="px-5 py-2 rounded-xl border-2 border-pando-green text-pando-green hover:bg-pando-green-50 text-sm font-semibold transition-colors disabled:opacity-40"
              >
                {saving ? 'Saving...' : 'Save Project'}
              </button>
              <button
                onClick={async () => { const s = await saveProject(); setSavedProject(s); setShowRunModal(true) }}
                disabled={saving}
                className="px-5 py-2 rounded-xl bg-pando-green hover:bg-pando-green-600 text-white text-sm font-semibold transition-colors shadow-sm disabled:opacity-40"
              >
                Save & Run Test
              </button>
            </div>
          )}
        </div>
      </div>

      {showRunModal && savedProject && (
        <RunTestModal project={savedProject} onClose={() => setShowRunModal(false)} onViewResults={() => navigate(`/project/${savedProject.project_id}/results`)} />
      )}
    </div>
  )
}

function FieldLabel({ children, required }) {
  return (
    <label className="block text-text-primary text-sm font-semibold mb-2">
      {children} {required && <span className="text-danger">*</span>}
    </label>
  )
}

function Step1({ config, update, onNameChange }) {
  return (
    <div className="space-y-5">
      <div className="mb-6">
        <h2 className="text-text-primary font-bold text-lg">Basic Information</h2>
        <p className="text-text-muted text-sm mt-0.5">Set up the core details for this project</p>
      </div>
      <div>
        <FieldLabel required>Project Name</FieldLabel>
        <input type="text" value={config.project_name} onChange={(e) => onNameChange(e.target.value)} placeholder="GE Freight" className="w-full" />
      </div>
      <div>
        <FieldLabel required>Project ID</FieldLabel>
        <input type="text" value={config.project_id} onChange={(e) => update('project_id', e.target.value)} placeholder="ge-freight" className="w-full font-mono bg-background" />
        <p className="text-text-muted text-xs mt-1.5">Auto-generated from project name. Lowercase letters and hyphens only.</p>
      </div>
      <div>
        <FieldLabel required>CloudWatch Log Group</FieldLabel>
        <input type="text" value={config.cloudwatch_log_group} onChange={(e) => update('cloudwatch_log_group', e.target.value)} placeholder="/aws/lambda/invoice-processor-ge-freight" className="w-full" />
      </div>
      <div>
        <label className="block text-text-primary text-sm font-semibold mb-2">Target API URL</label>
        <div className="flex items-center gap-2 px-4 py-2.5 bg-background border border-border rounded-lg">
          <span className="w-2 h-2 rounded-full bg-success flex-shrink-0" />
          <span className="font-mono text-sm text-text-secondary truncate">{config.target_api_url}</span>
          <span className="ml-auto text-xs text-success font-medium flex-shrink-0">This app</span>
        </div>
        <p className="text-text-muted text-xs mt-1.5">Results are sent to this application's own API endpoint.</p>
      </div>
    </div>
  )
}

function Step2({ config, updateSlot, addSlot, removeSlot }) {
  return (
    <div>
      <div className="mb-6">
        <h2 className="text-text-primary font-bold text-lg">Source Files</h2>
        <p className="text-text-muted text-sm mt-0.5">
          Prompt and LLM response are collected automatically from CloudWatch.
          Toggle on any additional mapping files stored in S3.
        </p>
      </div>

      <div className="bg-background rounded-xl border border-border divide-y divide-border px-4">
        {config.file_slots.map((slot) => (
          <FileSlotRow
            key={slot.id}
            slot={slot}
            onChange={(updated) => updateSlot(slot.id, updated)}
            isCustom={slot.isCustom}
            onRemove={slot.isCustom ? () => removeSlot(slot.id) : undefined}
          />
        ))}
      </div>

      {/* Add custom mapping file */}
      <button
        type="button"
        onClick={addSlot}
        className="mt-4 flex items-center gap-2 px-4 py-2 rounded-xl border-2 border-dashed border-pando-green-200 text-pando-green hover:border-pando-green hover:bg-pando-green-50 transition-colors text-sm font-semibold w-full justify-center"
      >
        <span className="text-lg leading-none">+</span>
        Add Custom Mapping File
      </button>
    </div>
  )
}

function MandatoryFieldsInput({ fields, onChange }) {
  const [inputVal, setInputVal] = useState('')

  const addField = () => {
    const trimmed = inputVal.trim().toLowerCase().replace(/\s+/g, '_')
    if (trimmed && !fields.includes(trimmed)) {
      onChange([...fields, trimmed])
    }
    setInputVal('')
  }

  const handleKeyDown = (e) => {
    if (e.key === 'Enter' || e.key === ',') {
      e.preventDefault()
      addField()
    } else if (e.key === 'Backspace' && !inputVal && fields.length > 0) {
      onChange(fields.slice(0, -1))
    }
  }

  return (
    <div>
      <div className="flex flex-wrap gap-2 p-3 bg-white border border-border rounded-xl min-h-[48px] max-h-40 overflow-y-auto focus-within:border-pando-green transition-colors">
        {fields.map((f) => (
          <span key={f} className="inline-flex items-center gap-1.5 px-2.5 py-1 bg-pando-green text-white text-xs font-semibold rounded-lg">
            <span className="font-mono">{f}</span>
            <button
              type="button"
              onClick={() => onChange(fields.filter((x) => x !== f))}
              className="text-white/70 hover:text-white leading-none"
            >
              ×
            </button>
          </span>
        ))}
        <input
          type="text"
          value={inputVal}
          onChange={(e) => setInputVal(e.target.value)}
          onKeyDown={handleKeyDown}
          onBlur={addField}
          placeholder={fields.length === 0 ? 'Type a field name and press Enter…' : ''}
          className="flex-1 min-w-[160px] border-none outline-none bg-transparent text-sm text-text-primary p-0"
          style={{ boxShadow: 'none' }}
        />
      </div>
      <p className="text-text-muted text-xs mt-1.5">
        Press <kbd className="px-1 py-0.5 bg-border rounded text-[10px] font-mono">Enter</kbd> or <kbd className="px-1 py-0.5 bg-border rounded text-[10px] font-mono">,</kbd> after each field name. If any of these fields is missing from the payload, the test is forced to <strong>failed</strong>.
      </p>
    </div>
  )
}

function Step3({ config, update }) {
  const weights = config.scoring_weights
  const categories = [
    { key: 'charge_fields', label: 'Charge Fields' },
    { key: 'address_fields', label: 'Address Fields' },
    { key: 'date_fields', label: 'Date Fields' },
    { key: 'amount_fields', label: 'Amount Fields' },
  ]

  return (
    <div className="space-y-8">
      <div className="mb-2">
        <h2 className="text-text-primary font-bold text-lg">Test Configuration</h2>
        <p className="text-text-muted text-sm mt-0.5">Configure how tests are run for this project</p>
      </div>

      <div className="bg-background rounded-xl border border-border p-5">
        <FieldLabel>Mandatory Fields</FieldLabel>
        <p className="text-text-muted text-xs mb-3">The agent will check that these fields exist in every invoice payload. A missing mandatory field overrides the overall result to <strong>failed</strong>.</p>
        <MandatoryFieldsInput
          fields={config.mandatory_fields}
          onChange={(val) => update('mandatory_fields', val)}
        />
      </div>

      <div className="bg-background rounded-xl border border-border p-5">
        <div className="mb-4">
          <p className="text-text-primary text-sm font-semibold">Field scoring weights</p>
          <p className="text-text-muted text-xs mt-0.5">Relative weights — don't need to sum to 100</p>
        </div>
        <div className="space-y-4">
          {categories.map(({ key, label }) => (
            <div key={key} className="flex items-center gap-4">
              <span className="text-text-secondary text-sm w-32 flex-shrink-0 font-medium">{label}</span>
              <input type="range" min={0} max={100} value={weights[key]} onChange={(e) => update('scoring_weights', { ...weights, [key]: Number(e.target.value) })} className="flex-1 accent-[#6C5CE7]" style={{ background: 'transparent', border: 'none', padding: 0, boxShadow: 'none' }} />
              <span className="text-pando-green font-bold text-sm w-8 text-right">{weights[key]}</span>
            </div>
          ))}
        </div>
      </div>

    </div>
  )
}

function ReviewCard({ title, children }) {
  return (
    <div className="bg-background border border-border rounded-xl overflow-hidden">
      <div className="px-5 py-3 border-b border-border bg-pando-green-50">
        <h3 className="text-pando-green font-semibold text-sm">{title}</h3>
      </div>
      <div className="px-5 py-4 space-y-3">{children}</div>
    </div>
  )
}

function ReviewRow({ label, value }) {
  return (
    <div className="flex items-start justify-between gap-4">
      <span className="text-text-muted text-sm flex-shrink-0">{label}</span>
      <span className="text-text-primary text-sm text-right break-all font-medium">{value || <span className="text-text-muted font-normal italic">Not set</span>}</span>
    </div>
  )
}

function Step4({ config }) {
  const enabledSlots = config.file_slots.filter((s) => s.enabled)
  return (
    <div className="space-y-5">
      <div className="mb-2">
        <h2 className="text-text-primary font-bold text-lg">Review & Save</h2>
        <p className="text-text-muted text-sm mt-0.5">Confirm your configuration before saving</p>
      </div>
      <ReviewCard title="Basic Info">
        <ReviewRow label="Project Name" value={config.project_name} />
        <ReviewRow label="Project ID" value={config.project_id} />
        <ReviewRow label="Log Group" value={config.cloudwatch_log_group} />
        <div className="flex items-start justify-between gap-4">
          <span className="text-text-muted text-sm flex-shrink-0">Target API</span>
          <div className="flex items-center gap-1.5">
            <span className="w-1.5 h-1.5 rounded-full bg-success flex-shrink-0" />
            <span className="font-mono text-xs text-pando-green-600 break-all">{config.target_api_url}</span>
          </div>
        </div>
      </ReviewCard>
      <ReviewCard title="Enabled Source Files">
        {enabledSlots.length === 0 ? (
          <p className="text-text-muted text-sm italic">No additional sources enabled</p>
        ) : enabledSlots.map((slot) => (
          <div key={slot.id} className="flex items-center justify-between gap-4">
            <span className="text-text-secondary text-sm font-medium">{slot.label}</span>
            <span className="text-text-muted text-xs font-mono">{slot.s3_bucket}/{slot.s3_key}</span>
          </div>
        ))}
      </ReviewCard>
      <ReviewCard title="Test Configuration">
        <div>
          <p className="text-text-muted text-sm mb-2">Mandatory Fields</p>
          {config.mandatory_fields.length === 0 ? (
            <p className="text-text-muted text-xs italic">None — all fields optional</p>
          ) : (
            <div className="flex flex-wrap gap-1.5">
              {config.mandatory_fields.map((f) => (
                <span key={f} className="px-2 py-0.5 bg-pando-green text-white text-xs font-mono font-semibold rounded-md">{f}</span>
              ))}
            </div>
          )}
        </div>
        <div>
          <p className="text-text-muted text-sm mb-2">Scoring Weights</p>
          <div className="grid grid-cols-2 gap-2">
            {Object.entries(config.scoring_weights).map(([key, val]) => (
              <div key={key} className="flex items-center justify-between bg-white rounded-lg px-3 py-1.5 border border-border">
                <span className="text-text-secondary text-xs capitalize">{key.replace('_', ' ')}</span>
                <span className="text-pando-green text-xs font-bold">{val}</span>
              </div>
            ))}
          </div>
        </div>
      </ReviewCard>
    </div>
  )
}
