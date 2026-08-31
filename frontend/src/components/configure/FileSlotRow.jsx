import { useRef, useState } from 'react'
import { Pencil, Check, X } from 'lucide-react'
import Toggle from '../results/Toggle'

export default function FileSlotRow({ slot, onChange, isCustom, onRemove }) {
  const [editingLabel, setEditingLabel] = useState(false)
  const [labelDraft, setLabelDraft] = useState(slot.label)
  const contentRef = useRef(null)

  const handleToggle = (enabled) => {
    const el = contentRef.current
    if (el) {
      el.style.maxHeight = enabled ? el.scrollHeight + 80 + 'px' : '0px'
    }
    onChange({ ...slot, enabled })
  }

  const confirmLabel = () => {
    setEditingLabel(false)
    onChange({ ...slot, label: labelDraft })
  }

  return (
    <div className="border-b border-border last:border-b-0 py-4">
      <div className="flex items-start justify-between gap-4">
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2 mb-0.5">
            {editingLabel ? (
              <div className="flex items-center gap-2">
                <input
                  type="text"
                  value={labelDraft}
                  onChange={(e) => setLabelDraft(e.target.value)}
                  onKeyDown={(e) => e.key === 'Enter' && confirmLabel()}
                  autoFocus
                  className="text-sm font-medium py-0.5 px-2 h-7"
                  style={{ width: 200 }}
                />
                <button type="button" onClick={confirmLabel} className="text-success">
                  <Check size={14} />
                </button>
              </div>
            ) : (
              <div className="flex items-center gap-2">
                <span className="text-text-primary text-sm font-medium">{slot.label}</span>
                {isCustom && (
                  <button type="button" onClick={() => setEditingLabel(true)} className="text-text-muted hover:text-pando-green transition-colors">
                    <Pencil size={12} />
                  </button>
                )}
                {slot.required && (
                  <span className="px-2 py-0.5 bg-pando-green-50 text-pando-green border border-pando-green-200 rounded-full text-[10px] font-semibold">
                    Required
                  </span>
                )}
              </div>
            )}
          </div>
          <p className="text-text-muted text-xs">{slot.description}</p>
        </div>
        <div className="flex items-center gap-2 flex-shrink-0">
          <Toggle checked={slot.enabled} onChange={handleToggle} disabled={slot.required} />
          {onRemove && (
            <button
              type="button"
              onClick={onRemove}
              title="Remove this mapping file"
              className="w-6 h-6 flex items-center justify-center rounded-lg text-text-muted hover:bg-danger-bg hover:text-danger transition-colors"
            >
              <X size={13} />
            </button>
          )}
        </div>
      </div>

      <div
        ref={contentRef}
        style={{ maxHeight: slot.enabled ? 1000 : 0, overflow: 'hidden', transition: 'max-height 0.3s ease' }}
      >
        <div className="mt-3 pt-3 border-t border-border bg-background rounded-xl p-3">
          <div className="grid grid-cols-2 gap-3">
            <div>
              <label className="block text-text-muted text-xs font-medium mb-1.5">S3 Bucket Name</label>
              <input type="text" placeholder="my-invoice-bucket" value={slot.s3_bucket} onChange={(e) => onChange({ ...slot, s3_bucket: e.target.value })} className="w-full text-sm" />
            </div>
            <div>
              <label className="block text-text-muted text-xs font-medium mb-1.5">S3 File Key</label>
              <input type="text" placeholder="projects/ge/prompt-template.txt" value={slot.s3_key} onChange={(e) => onChange({ ...slot, s3_key: e.target.value })} className="w-full text-sm" />
            </div>
          </div>
          <p className="text-text-muted text-xs mt-2 flex items-center gap-1">
            <span className="w-1 h-1 rounded-full bg-pando-green inline-block" />
            File will be read at test runtime via GetObject
          </p>
        </div>
      </div>
    </div>
  )
}
