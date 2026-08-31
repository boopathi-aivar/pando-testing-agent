export default function Toggle({ checked, onChange, disabled }) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={checked}
      disabled={disabled}
      onClick={() => !disabled && onChange(!checked)}
      className={`relative inline-flex items-center rounded-full transition-colors duration-200 focus:outline-none flex-shrink-0
        ${checked ? 'bg-pando-green' : 'bg-border-dark'}
        ${disabled ? 'opacity-50 cursor-not-allowed' : 'cursor-pointer'}`}
      style={{ width: 44, height: 24 }}
    >
      <span
        className="inline-block rounded-full bg-white shadow-sm transition-transform duration-200"
        style={{
          width: 18,
          height: 18,
          transform: checked ? 'translateX(22px)' : 'translateX(3px)',
        }}
      />
    </button>
  )
}
