import { CheckCircle2, Circle, Loader2, XCircle, Database, Trash2, Upload, Search, FileText, Download, ScrollText } from 'lucide-react'

const STEP_CONFIG = {
  fetch_pks: {
    label: 'Fetch PKs',
    icon: Database,
    description: 'Scan DynamoDB for invoice partition keys',
  },
  delete_records: {
    label: 'Delete Records',
    icon: Trash2,
    description: 'Remove stale records from DynamoDB',
  },
  reingest_files: {
    label: 'Reingest Files',
    icon: Upload,
    description: 'Trigger S3 events via CopyObject',
  },
  resolve_rows: {
    label: 'Resolve Rows',
    icon: Search,
    description: 'Map invoice numbers to DynamoDB rows and attachments',
  },
  download_payloads: {
    label: 'Download Payloads',
    icon: FileText,
    description: 'Fetch JSON payloads from S3',
  },
  download_pdfs: {
    label: 'Download PDFs',
    icon: Download,
    description: 'Fetch PDF attachments from S3',
  },
  download_logs: {
    label: 'Download Logs',
    icon: ScrollText,
    description: 'Pull CloudWatch logs for each invoice',
  },
  save_files: {
    label: 'Save Files',
    icon: Upload,
    description: 'Write artifacts under the folder name',
  },
}

const RETRIGGER_STEPS = ['fetch_pks', 'delete_records', 'reingest_files']
const FETCH_STEPS_ORDER = [
  'resolve_rows',
  'download_payloads',
  'download_pdfs',
  'download_logs',
  'save_files',
]

function resolveStepNames(steps) {
  if (!steps || typeof steps !== 'object') return RETRIGGER_STEPS
  const keys = Object.keys(steps)
  if (keys.some((k) => FETCH_STEPS_ORDER.includes(k))) {
    return FETCH_STEPS_ORDER.filter((k) => keys.includes(k))
  }
  if (keys.some((k) => RETRIGGER_STEPS.includes(k))) {
    return RETRIGGER_STEPS.filter((k) => keys.includes(k)).length
      ? RETRIGGER_STEPS.filter((k) => keys.includes(k))
      : RETRIGGER_STEPS
  }
  return RETRIGGER_STEPS
}

function StepIcon({ status, icon: Icon }) {
  if (status === 'completed') {
    return <CheckCircle2 size={20} className="text-pando-green-600" />
  }
  if (status === 'running') {
    return <Loader2 size={20} className="text-[#6C5CE7] animate-spin" />
  }
  if (status === 'failed') {
    return <XCircle size={20} className="text-danger" />
  }
  return <Circle size={20} className="text-text-muted" />
}

function StepStatus({ status }) {
  const config = {
    pending: { label: 'Pending', cls: 'text-text-muted' },
    running: { label: 'Running', cls: 'text-[#6C5CE7] font-semibold' },
    completed: { label: 'Completed', cls: 'text-pando-green-600 font-semibold' },
    failed: { label: 'Failed', cls: 'text-danger font-semibold' },
  }
  const { label, cls } = config[status] ?? config.pending
  return <span className={`text-xs ${cls}`}>{label}</span>
}

function StepMeta({ stepName, step }) {
  if (step.status === 'pending') return null

  return (
    <div className="flex flex-wrap gap-3 text-xs">
      {step.found !== undefined && (
        <span className="text-text-secondary">
          <span className="font-semibold">{step.found}</span> found
        </span>
      )}
      {step.missing !== undefined && (
        <span className="text-text-secondary">
          <span className="font-semibold">{step.missing}</span> missing
        </span>
      )}
      {step.deleted !== undefined && (
        <span className="text-text-secondary">
          <span className="font-semibold">{step.deleted}</span> deleted
        </span>
      )}
      {step.triggered !== undefined && (
        <span className="text-text-secondary">
          <span className="font-semibold">{step.triggered}</span> triggered
        </span>
      )}
      {step.downloaded !== undefined && (
        <span className="text-text-secondary">
          <span className="font-semibold">{step.downloaded}</span> downloaded
        </span>
      )}
      {step.saved !== undefined && (
        <span className="text-text-secondary">
          <span className="font-semibold">{step.saved}</span> saved
        </span>
      )}
      {step.failed !== undefined && step.failed > 0 && (
        <span className="text-danger">
          <span className="font-semibold">{step.failed}</span> failed
        </span>
      )}
      {step.updated_at && (
        <span className="text-text-muted ml-auto">
          {new Date(step.updated_at).toLocaleTimeString()}
        </span>
      )}
    </div>
  )
}

export default function StepTracker({ steps }) {
  const stepNames = resolveStepNames(steps)

  return (
    <div className="space-y-4">
      {stepNames.map((stepName, idx) => {
        const step = steps?.[stepName] ?? { status: 'pending' }
        const config = STEP_CONFIG[stepName] ?? {
          label: stepName.replace(/_/g, ' '),
          icon: Circle,
          description: '',
        }
        const isLast = idx === stepNames.length - 1

        return (
          <div key={stepName} className="relative">
            {!isLast && (
              <div
                className={`absolute left-[10px] top-[28px] w-0.5 h-12 -mb-4 ${
                  step.status === 'completed'
                    ? 'bg-success'
                    : step.status === 'failed'
                    ? 'bg-danger'
                    : 'bg-border'
                }`}
              />
            )}

            <div
              className={`flex items-start gap-4 p-4 rounded-xl border transition-all ${
                step.status === 'running'
                  ? 'border-aivar-purple-200 bg-aivar-purple-50/30'
                  : step.status === 'completed'
                  ? 'border-pando-green-200 bg-pando-green-50/30'
                  : step.status === 'failed'
                  ? 'border-danger/20 bg-danger-bg'
                  : 'border-border bg-background'
              }`}
            >
              <div className="flex-shrink-0 mt-0.5">
                <StepIcon status={step.status} icon={config.icon} />
              </div>

              <div className="flex-1 min-w-0">
                <div className="flex items-center justify-between mb-1">
                  <h4 className="text-text-primary font-semibold text-sm">{config.label}</h4>
                  <StepStatus status={step.status} />
                </div>
                {config.description && (
                  <p className="text-text-muted text-xs mb-2">{config.description}</p>
                )}

                <StepMeta stepName={stepName} step={step} />

                {step.error && (
                  <div className="mt-2 p-2 rounded bg-danger-bg border border-danger/20">
                    <p className="text-danger text-xs font-mono">{step.error}</p>
                  </div>
                )}
              </div>
            </div>
          </div>
        )
      })}
    </div>
  )
}
