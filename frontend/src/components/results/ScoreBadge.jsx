export default function ScoreBadge({ score, size = 52 }) {
  const strokeWidth = 4
  const r = size / 2 - strokeWidth - 2
  const circ = 2 * Math.PI * r
  const offset = score != null ? circ * (1 - score / 100) : circ

  const color =
    score == null ? '#D1D5DB'
    : score >= 85 ? '#16A34A'
    : score >= 60 ? '#D97706'
    : '#DC2626'

  const textColor = score == null ? '#9CA3AF' : color

  // Always show integer — circle is too small for decimals
  const label = score != null ? `${Math.round(score)}%` : '—'

  // fontSize scales with circle size
  const fontSize = Math.max(9, Math.round(size * 0.24))

  return (
    <div className="relative flex-shrink-0" style={{ width: size, height: size }}>
      <svg width={size} height={size} style={{ transform: 'rotate(-90deg)', display: 'block' }}>
        <circle
          cx={size / 2}
          cy={size / 2}
          r={r}
          fill="none"
          stroke="#E8EAED"
          strokeWidth={strokeWidth}
        />
        {score != null && (
          <circle
            cx={size / 2}
            cy={size / 2}
            r={r}
            fill="none"
            stroke={color}
            strokeWidth={strokeWidth}
            strokeDasharray={circ}
            strokeDashoffset={offset}
            strokeLinecap="round"
          />
        )}
      </svg>
      <div
        style={{
          position: 'absolute',
          top: 0, left: 0, right: 0, bottom: 0,
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
        }}
      >
        <span style={{ color: textColor, fontSize, fontWeight: 700, lineHeight: 1, whiteSpace: 'nowrap' }}>
          {label}
        </span>
      </div>
    </div>
  )
}
