import { useEffect, useId, useRef, useState } from 'react'
import { Check, ChevronDown } from 'lucide-react'

/**
 * Light custom dropdown (Delicato-style) — avoids native OS select menus.
 * options: [{ value: string, label: string }]
 */
export default function CustomSelect({
  id,
  label,
  value,
  options = [],
  onChange,
  placeholder = 'Select…',
  disabled = false,
  className = '',
  triggerClassName = '',
  menuClassName = '',
}) {
  const autoId = useId()
  const selectId = id || autoId
  const [open, setOpen] = useState(false)
  const rootRef = useRef(null)

  const selected = options.find((o) => String(o.value) === String(value))
  const display = selected?.label ?? placeholder

  useEffect(() => {
    if (!open) return undefined
    function onDoc(e) {
      if (rootRef.current && !rootRef.current.contains(e.target)) setOpen(false)
    }
    function onKey(e) {
      if (e.key === 'Escape') setOpen(false)
    }
    document.addEventListener('mousedown', onDoc)
    document.addEventListener('keydown', onKey)
    return () => {
      document.removeEventListener('mousedown', onDoc)
      document.removeEventListener('keydown', onKey)
    }
  }, [open])

  return (
    <div ref={rootRef} className={`relative flex flex-col gap-1.5 min-w-[10rem] ${className}`}>
      {label && (
        <label
          htmlFor={selectId}
          className="text-[11px] font-semibold uppercase tracking-wide text-text-secondary"
        >
          {label}
        </label>
      )}
      <button
        id={selectId}
        type="button"
        disabled={disabled}
        aria-haspopup="listbox"
        aria-expanded={open}
        onClick={() => !disabled && setOpen((v) => !v)}
        className={`flex items-center justify-between gap-2.5 w-full min-w-[12.5rem] h-8 px-2.5 rounded-md border text-left text-xs font-semibold transition-colors disabled:opacity-50 disabled:cursor-not-allowed ${
          open
            ? 'border-[#1570ef] shadow-[0_0_0_3px_rgba(21,112,239,0.12)]'
            : 'border-[#d0d5dd] hover:bg-[#f8fafc]'
        } bg-surface text-text-primary max-w-[17.5rem] ${triggerClassName}`}
      >
        <span className="truncate">{display}</span>
        <ChevronDown
          size={14}
          className={`shrink-0 text-[#667085] transition-transform ${open ? 'rotate-180' : ''}`}
          aria-hidden
        />
      </button>

      {open && (
        <div
          role="listbox"
          className={`absolute top-[calc(100%+6px)] left-0 z-50 min-w-full max-w-[22.5rem] max-h-[280px] overflow-y-auto rounded-lg border border-[#e4e7ec] bg-white p-1 shadow-[0_12px_32px_rgba(16,24,40,0.12)] ${menuClassName}`}
        >
          {options.map((opt) => {
            const isSelected = String(opt.value) === String(value)
            return (
              <button
                key={String(opt.value)}
                type="button"
                role="option"
                aria-selected={isSelected}
                onClick={() => {
                  onChange?.(opt.value)
                  setOpen(false)
                }}
                className={`flex w-full items-center gap-2 text-left rounded-md px-2.5 py-2 text-xs cursor-pointer transition-colors ${
                  isSelected
                    ? 'bg-[#eff8ff] text-[#1570ef] font-semibold'
                    : 'text-[#101828] font-medium hover:bg-[#f2f4f7]'
                }`}
              >
                <span className="w-3.5 shrink-0 flex justify-center">
                  {isSelected ? <Check size={12} strokeWidth={2.5} /> : null}
                </span>
                <span className="truncate">{opt.label}</span>
              </button>
            )
          })}
        </div>
      )}
    </div>
  )
}
