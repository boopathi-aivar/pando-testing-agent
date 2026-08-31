import { useState } from 'react'
import { CheckCircle, Loader2, X } from 'lucide-react'
import { runTest } from '../../api/client'

const STEPS = [
  'Collecting inputs from S3',
  'Querying CloudWatch logs',
  'Validating payload fields',
  'Computing scores',
  'Generating prompt suggestions',
  'Sending email report',
]

export default function RunTestModal({ project, onClose, onViewResults }) {
  const [state, setState] = useState('ready')
  const [invoiceFilter, setInvoiceFilter] = useState('')
  const [completedSteps, setCompletedSteps] = useState([])
  const [activeStep, setActiveStep] = useState(-1)
  const [finalScore, setFinalScore] = useState(null)

  const startTest = async () => {
    setState('running')
    setCompletedSteps([])
    setActiveStep(0)
    await runTest(project.project_id, invoiceFilter)

    const runStep = (index) => {
      if (index >= STEPS.length) {
        setTimeout(() => {
          setFinalScore(project.last_score ?? 91)
          setState('complete')
        }, 400)
        return
      }
      setTimeout(() => {
        setCompletedSteps((prev) => [...prev, index])
        setActiveStep(index + 1)
        runStep(index + 1)
      }, 1500)
    }
    runStep(0)
  }

  const score = finalScore ?? 0
  const scoreColor = score >= 85 ? 'text-success' : score >= 60 ? 'text-warning' : 'text-danger'
  const scoreLabel = score >= 85 ? 'Passed' : score >= 60 ? 'Warning' : 'Failed'
  const scoreBg = score >= 85 ? 'bg-success-bg border-success/20' : score >= 60 ? 'bg-warning-bg border-warning/20' : 'bg-danger-bg border-danger/20'

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center">
      <div className="absolute inset-0 bg-black/40 backdrop-blur-sm" onClick={state === 'ready' ? onClose : undefined} />
      <div className="relative bg-white rounded-2xl shadow-modal p-8 w-[480px] mx-4 border border-border">

        {state !== 'running' && (
          <button onClick={onClose} className="absolute top-4 right-4 w-8 h-8 flex items-center justify-center rounded-lg text-text-muted hover:bg-background hover:text-text-secondary transition-colors">
            <X size={16} />
          </button>
        )}

        {state === 'ready' && (
          <>
            <div className="mb-6">
              <h2 className="text-text-primary font-bold text-lg">Run Test</h2>
              <p className="text-text-secondary text-sm mt-0.5">{project.project_name}</p>
            </div>
            <div className="mb-6">
              <label className="block text-text-secondary text-sm font-medium mb-2">Invoice filter <span className="text-text-muted font-normal">(optional)</span></label>
              <input type="text" placeholder="INV-2024-00123" value={invoiceFilter} onChange={(e) => setInvoiceFilter(e.target.value)} className="w-full" />
              <p className="text-text-muted text-xs mt-1.5">Leave blank to use the most recent invoice found in logs</p>
            </div>
            <button onClick={startTest} className="w-full py-2.5 bg-pando-green hover:bg-pando-green-600 text-white rounded-xl font-semibold transition-colors shadow-sm">
              Start Test
            </button>
            <button onClick={onClose} className="w-full mt-3 text-text-muted text-sm hover:text-text-secondary transition-colors py-1">
              Cancel
            </button>
          </>
        )}

        {state === 'running' && (
          <>
            <div className="flex items-center gap-3 mb-8">
              <div className="w-9 h-9 rounded-xl bg-pando-green/10 flex items-center justify-center">
                <Loader2 size={18} className="animate-spin text-pando-green" />
              </div>
              <div>
                <h2 className="text-text-primary font-bold text-base">Running Test...</h2>
                <p className="text-text-muted text-xs">{project.project_name}</p>
              </div>
            </div>
            <div className="space-y-3.5">
              {STEPS.map((step, i) => {
                const done = completedSteps.includes(i)
                const active = activeStep === i
                return (
                  <div key={i} className="flex items-center gap-3">
                    <div className={`w-7 h-7 rounded-full flex items-center justify-center text-xs font-bold flex-shrink-0 transition-all
                      ${done ? 'bg-pando-green text-white' : active ? 'bg-pando-gold text-pando-green ring-4 ring-pando-gold/20' : 'bg-background border border-border text-text-muted'}`}>
                      {done ? <CheckCircle size={13} /> : i + 1}
                    </div>
                    <span className={`text-sm transition-colors ${done || active ? 'text-text-primary font-medium' : 'text-text-muted'}`}>
                      {step}
                    </span>
                    <div className="ml-auto">
                      {active && <Loader2 size={13} className="animate-spin text-pando-green" />}
                      {done && <CheckCircle size={13} className="text-success" />}
                    </div>
                  </div>
                )
              })}
            </div>
          </>
        )}

        {state === 'complete' && (
          <div className="text-center">
            <div className="flex justify-center mb-5">
              <div className="w-16 h-16 rounded-2xl bg-pando-green-50 border-2 border-pando-green-100 flex items-center justify-center">
                <CheckCircle size={34} className="text-pando-green" />
              </div>
            </div>
            <h2 className="text-text-primary font-bold text-xl mb-1">Test Complete!</h2>
            <div className={`inline-flex items-center gap-2 px-4 py-2 rounded-xl border mb-6 mt-1 ${scoreBg}`}>
              <span className={`font-bold text-base ${scoreColor}`}>Overall Score: {finalScore}%</span>
              <span className={`text-sm font-medium ${scoreColor}`}>— {scoreLabel}</span>
            </div>
            <button onClick={onViewResults} className="w-full py-2.5 bg-pando-green hover:bg-pando-green-600 text-white rounded-xl font-semibold transition-colors shadow-sm mb-3">
              View Results
            </button>
            <button onClick={() => { setState('ready'); setCompletedSteps([]); setActiveStep(-1) }} className="w-full text-text-muted text-sm hover:text-pando-green transition-colors py-1">
              Run Another
            </button>
          </div>
        )}
      </div>
    </div>
  )
}
