import { Check } from 'lucide-react'

export default function StepIndicator({ steps, currentStep }) {
  return (
    <div className="flex items-center mb-8">
      {steps.map((step, i) => (
        <div key={step} className="flex items-center flex-1 last:flex-none">
          <div className="flex items-center gap-2.5 flex-shrink-0">
            <div
              className="w-8 h-8 rounded-full flex items-center justify-center text-sm font-bold transition-all duration-200"
              style={
                i < currentStep
                  ? { background: '#6C5CE7', color: 'white', boxShadow: '0 1px 3px rgba(0,0,0,0.12)' }
                  : i === currentStep
                  ? { background: '#6C5CE7', color: 'white', boxShadow: '0 0 0 4px rgba(108,92,231,0.20)' }
                  : { background: 'white', border: '2px solid #E8EAED', color: '#9CA3AF' }
              }
            >
              {i < currentStep ? <Check size={14} /> : i + 1}
            </div>
            <span
              className={`text-sm font-medium whitespace-nowrap ${
                i < currentStep ? 'text-text-secondary' : i !== currentStep ? 'text-text-muted' : ''
              }`}
              style={i === currentStep ? { color: '#6C5CE7', fontWeight: 600 } : {}}
            >
              {step}
            </span>
          </div>
          {i < steps.length - 1 && (
            <div className={`flex-1 h-px mx-4 ${i < currentStep ? 'bg-pando-green' : 'bg-border'}`} />
          )}
        </div>
      ))}
    </div>
  )
}
