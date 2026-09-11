function Wordmark({ size = 'md', onDark = true }) {
  const line = size === 'lg'
    ? 'text-[22px] tracking-[0.14em]'
    : size === 'sm'
      ? 'text-[11px] tracking-[0.1em]'
      : 'text-[16px] tracking-[0.12em]'

  return (
    <span className={`font-wordmark font-semibold uppercase whitespace-nowrap ${line}`}>
      <span className={onDark ? 'text-white' : 'text-text-primary'}>Testing</span>
      {' '}
      <span className="text-[#A29BFE]">Agent</span>
    </span>
  )
}

export default function BrandLockup({ size = 'md', onDark = true }) {
  const logoClass = size === 'lg' ? 'h-12' : size === 'sm' ? 'h-7' : 'h-9'
  const barSize = size === 'lg' ? 24 : size === 'sm' ? 14 : 18
  const gap = size === 'lg' ? 'gap-4' : size === 'sm' ? 'gap-2.5' : 'gap-3'

  return (
    <div className={`flex items-center ${gap} min-w-0`}>
      <img
        src="/aivar-logo-white.webp"
        alt="Aivar"
        className={`${logoClass} w-auto object-contain flex-shrink-0 ${onDark ? '' : 'brightness-0'}`}
      />
      <span
        className={`${onDark ? 'text-white/35' : 'text-text-muted'} font-light select-none flex-shrink-0`}
        style={{ fontSize: barSize, lineHeight: 1 }}
      >
        |
      </span>
      <Wordmark size={size} onDark={onDark} />
    </div>
  )
}
