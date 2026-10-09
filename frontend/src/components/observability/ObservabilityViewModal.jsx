import { X } from 'lucide-react'
import ObservabilityViewForm from './ObservabilityViewForm'

/** Modal wrapper — used from the dynamic project view (Edit columns). */
export default function ObservabilityViewModal({ viewId = null, onClose, onSaved }) {
  return (
    <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50 p-4">
      <div className="bg-surface rounded-2xl shadow-2xl max-w-2xl w-full max-h-[90vh] overflow-y-auto">
        <div className="flex items-center justify-between p-6 border-b border-border sticky top-0 bg-surface z-10">
          <div>
            <h3 className="text-text-primary font-bold text-lg">
              {viewId ? 'Edit Observability project' : 'Add Observability project'}
            </h3>
            <p className="text-text-muted text-sm mt-1">
              Table name → discover fields → pick columns and actions
            </p>
          </div>
          <button
            type="button"
            onClick={onClose}
            className="w-8 h-8 rounded-lg flex items-center justify-center hover:bg-background transition-colors"
          >
            <X size={18} className="text-text-muted" />
          </button>
        </div>
        <div className="p-6">
          <ObservabilityViewForm
            viewId={viewId}
            onCancel={onClose}
            onSaved={(saved) => {
              onSaved?.(saved)
              onClose?.()
            }}
            compact
          />
        </div>
      </div>
    </div>
  )
}
