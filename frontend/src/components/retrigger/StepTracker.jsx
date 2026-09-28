import { CheckCircle2, Circle, Loader2, XCircle, Database, Trash2, Upload } from 'lucide-react'

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

export default function StepTracker({ steps }) {
  const stepNames = ['fetch_pks', 'delete_records', 'reingest_files']

  return (
    <div className="space-y-4">
      {stepNames.map((stepName, idx) => {
        const step = steps?.[stepName] ?? { status: 'pending' }
        const config = STEP_CONFIG[stepName]
        const isLast = idx === stepNames.length - 1

        return (
          <div key={stepName} className="relative">
            {/* Connector Line */}
            {!isLast && (
              <div
                className="absolute left-[10px] top-[28px] w-0.5 h-12 -mb-4"
                style={{
                  background:
                    step.status === 'completed'
                      ? '#10B981'
                      : step.status === 'failed'
                      ? '#E53E3E'
                      : '#E0E0E5',
                }}
              />
            )}

            {/* Step Card */}
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
                <p className="text-text-muted text-xs mb-2">{config.description}</p>

                {/* Step Metadata */}
                {step.status !== 'pending' && (
                  <div className="flex flex-wrap gap-3 text-xs">
                    {stepName === 'fetch_pks' && step.found !== undefined && (
                      <>
                        <span className="text-text-secondary">
                          <span className="font-semibold">{step.found}</span> found
                        </span>
                        {step.missing !== undefined && (
                          <span className="text-text-secondary">
                            <span className="font-semibold">{step.missing}</span> missing
                          </span>
                        )}
                      </>
                    )}

                    {stepName === 'delete_records' && step.deleted !== undefined && (
                      <span className="text-text-secondary">
                        <span className="font-semibold">{step.deleted}</span> deleted
                      </span>
                    )}

                    {stepName === 'reingest_files' && (
                      <>
                        {step.triggered !== undefined && (
                          <span className="text-text-secondary">
                            <span className="font-semibold">{step.triggered}</span> triggered
                          </span>
                        )}
                        {step.failed !== undefined && step.failed > 0 && (
                          <span className="text-danger">
                            <span className="font-semibold">{step.failed}</span> failed
                          </span>
                        )}
                      </>
                    )}

                    {step.updated_at && (
                      <span className="text-text-muted ml-auto">
                        {new Date(step.updated_at).toLocaleTimeString()}
                      </span>
                    )}
                  </div>
                )}

                {/* Error Message */}
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
