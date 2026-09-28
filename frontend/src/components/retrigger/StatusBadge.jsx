const CONFIG = {
  pending: {
    label: 'Pending',
    cls: 'bg-background text-text-muted border-border',
    dot: 'bg-text-muted',
  },
  running: {
    label: 'Running',
    cls: 'bg-aivar-purple-50 text-[#6C5CE7] border-aivar-purple-200',
    dot: 'bg-[#6C5CE7]',
  },
  completed: {
    label: 'Completed',
    cls: 'bg-pando-green-50 text-pando-green-600 border-pando-green-200',
    dot: 'bg-pando-green-600',
  },
  failed: {
    label: 'Failed',
    cls: 'bg-danger-bg text-danger border-danger/20',
    dot: 'bg-danger',
  },
}

export default function StatusBadge({ status, size = 'md' }) {
  const { label, cls, dot } = CONFIG[status] ?? CONFIG.pending
  const isRunning = status === 'running'
  const padding = size === 'sm' ? 'px-2 py-0.5 text-[11px]' : 'px-2.5 py-1 text-xs'
  const dotSize = size === 'sm' ? 'w-1.5 h-1.5' : 'w-2 h-2'

  return (
    <span
      className={`inline-flex items-center gap-1.5 font-semibold rounded-full border ${cls} ${padding}`}
    >
      <span
        className={`inline-block rounded-full flex-shrink-0 ${dot} ${dotSize} ${
          isRunning ? 'animate-pulse' : ''
        }`}
      />
      {label}
    </span>
  )
}
