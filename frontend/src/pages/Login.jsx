import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { Eye, EyeOff, LogIn, CheckCircle, Globe, BarChart2, Zap, Moon, Sun } from 'lucide-react'
import { login } from '../api/client'
import BrandLockup from '../components/layout/BrandLockup'
import { useTheme } from '../theme'

const FEATURES = [
  { icon: Zap,         text: 'AI-powered invoice field extraction testing' },
  { icon: BarChart2,   text: 'Automated prompt quality scoring & suggestions' },
  { icon: Globe,       text: 'Multi-carrier, multi-region support' },
  { icon: CheckCircle, text: 'CloudWatch log analysis & SES report delivery' },
]

const CARRIERS = [
  'Averitt', 'ABF Freight', 'Madison Logistics', 'Dayton', 'Hot Shot Freight',
  'M & M Cartage', 'Christenson', 'FedEx Freight', 'UPS Freight', 'XPO',
  'Old Dominion', 'Estes', 'Saia', 'R+L Carriers', 'Forward Air',
]

function BrandMarquee() {
  const loop = [...CARRIERS, ...CARRIERS]
  return (
    <div className="brand-marquee py-3">
      <div className="brand-marquee-track gap-3 pr-3">
        {loop.map((name, i) => (
          <span
            key={`${name}-${i}`}
            className="shrink-0 px-4 py-1.5 rounded-full text-xs font-semibold tracking-wide whitespace-nowrap text-text-secondary bg-aivar-purple-50 border border-aivar-purple-200"
          >
            {name}
          </span>
        ))}
      </div>
    </div>
  )
}

export default function Login() {
  const navigate = useNavigate()
  const { isDark, toggleTheme } = useTheme()
  const [email, setEmail]       = useState('pando@aivar.tech')
  const [password, setPassword] = useState('pando@123')
  const [showPwd, setShowPwd]   = useState(false)
  const [loading, setLoading]   = useState(false)
  const [error, setError]       = useState('')

  const handleSubmit = async (e) => {
    e.preventDefault()
    if (!email || !password) { setError('Please enter both email and password.'); return }
    setLoading(true)
    setError('')
    try {
      await login(email, password)
      navigate('/', { replace: true })
    } catch (err) {
      setError(err.message || 'Sign in failed. Please try again.')
    } finally {
      setLoading(false)
    }
  }

  const gridLine = isDark ? 'rgba(255,255,255,0.08)' : 'rgba(17,24,39,0.06)'

  return (
    <div className="min-h-screen flex bg-background" style={{ fontFamily: 'Inter, sans-serif' }}>

      <div className="hidden lg:flex lg:w-[54%] flex-col justify-between relative overflow-hidden px-12 py-10">
        <div
          className="absolute -top-24 -right-16 w-[420px] h-[420px] rounded-full opacity-40"
          style={{ background: 'radial-gradient(circle, #6C5CE7 0%, transparent 70%)', animation: 'float-orb 12s ease-in-out infinite' }}
        />
        <div
          className="absolute bottom-10 -left-20 w-[340px] h-[340px] rounded-full opacity-30"
          style={{ background: 'radial-gradient(circle, #A29BFE 0%, transparent 70%)', animation: 'float-orb 16s ease-in-out infinite reverse' }}
        />
        <div
          className="absolute inset-0 opacity-[0.12]"
          style={{
            backgroundImage: `linear-gradient(${gridLine} 1px, transparent 1px), linear-gradient(90deg, ${gridLine} 1px, transparent 1px)`,
            backgroundSize: '48px 48px',
          }}
        />

        <div className="relative z-10">
          <BrandLockup size="lg" onDark={isDark} />
        </div>

        <div className="relative z-10 max-w-xl">
          <p className="text-[#A29BFE] text-[11px] font-bold uppercase tracking-[0.22em] mb-4">Governed agentic QA</p>
          <h1 className="text-text-primary font-bold leading-[1.08] mb-5 font-display" style={{ fontSize: 46 }}>
            Invoice QA that<br />
            finally earns its<br />
            place in <span style={{ color: '#A29BFE' }}>production.</span>
          </h1>
          <p className="text-text-secondary text-base leading-relaxed mb-8 max-w-md">
            Score LLM extraction against the invoice PDF. Catch field regressions
            across carriers — before they hit Pando.
          </p>

          <div className="space-y-3 mb-10">
            {FEATURES.map(({ icon: Icon, text }) => (
              <div key={text} className="flex items-center gap-3">
                <div className="w-8 h-8 rounded-lg flex items-center justify-center flex-shrink-0 bg-aivar-purple-100">
                  <Icon size={15} style={{ color: '#A29BFE' }} />
                </div>
                <span className="text-text-secondary text-sm">{text}</span>
              </div>
            ))}
          </div>
        </div>

        <div className="relative z-10 -mx-12">
          <p className="px-12 text-[10px] font-bold uppercase tracking-[0.2em] text-text-muted mb-3">Trusted carriers</p>
          <BrandMarquee />
        </div>
      </div>

      <div className="flex-1 flex flex-col items-center justify-center px-6 py-12 relative bg-background">
        <button
          type="button"
          onClick={toggleTheme}
          title={isDark ? 'Switch to light theme' : 'Switch to dark theme'}
          className="absolute top-6 right-6 z-20 w-9 h-9 flex items-center justify-center rounded-lg text-text-muted hover:text-text-primary hover:bg-aivar-purple-50 transition-colors"
          aria-label="Toggle theme"
        >
          {isDark ? <Sun size={16} /> : <Moon size={16} />}
        </button>

        <div
          className="absolute inset-0 lg:hidden"
          style={{
            background: isDark
              ? 'radial-gradient(circle at top, rgba(108,92,231,0.25), #07080C 55%)'
              : 'radial-gradient(circle at top, rgba(108,92,231,0.12), #F4F5F8 55%)',
          }}
        />

        <div className="relative z-10 mb-8 lg:hidden flex justify-center">
          <BrandLockup size="md" onDark={isDark} />
        </div>

        <div className="relative z-10 w-full max-w-md rounded-2xl p-8 border bg-surface border-border shadow-card">
          <div className="mb-8 flex flex-col items-center text-center">
            <BrandLockup size="md" onDark={isDark} />
            <h2 className="text-text-primary font-bold text-2xl mt-6 mb-1 font-display">Welcome back</h2>
            <p className="text-text-muted text-sm">Sign in to Testing Agent</p>
          </div>

          <form onSubmit={handleSubmit} className="space-y-5">
            <div>
              <label className="block text-text-primary text-sm font-semibold mb-2">Email address</label>
              <input
                type="email"
                value={email}
                onChange={(e) => { setEmail(e.target.value); setError('') }}
                placeholder="you@example.com"
                className="login-field w-full py-3 px-4"
                autoComplete="email"
                autoFocus
              />
            </div>

            <div>
              <label className="block text-text-primary text-sm font-semibold mb-2">Password</label>
              <div className="relative">
                <input
                  type={showPwd ? 'text' : 'password'}
                  value={password}
                  onChange={(e) => { setPassword(e.target.value); setError('') }}
                  placeholder="••••••••"
                  className="login-field w-full py-3 px-4 pr-11"
                  autoComplete="current-password"
                />
                <button
                  type="button"
                  onClick={() => setShowPwd((v) => !v)}
                  className="absolute right-3 top-1/2 -translate-y-1/2 text-text-muted hover:text-text-primary transition-colors"
                >
                  {showPwd ? <EyeOff size={16} /> : <Eye size={16} />}
                </button>
              </div>
            </div>

            {error && (
              <div className="flex items-center gap-2 px-4 py-3 bg-danger-bg border border-danger/20 rounded-xl text-danger text-sm">
                <span className="w-4 h-4 rounded-full border border-danger/40 flex items-center justify-center text-[10px] font-bold flex-shrink-0">!</span>
                {error}
              </div>
            )}

            <button
              type="submit"
              disabled={loading}
              className="w-full flex items-center justify-center gap-2 py-3 rounded-xl text-white text-sm font-bold transition-colors shadow-sm disabled:opacity-60 disabled:cursor-not-allowed"
              style={{ background: loading ? '#5A4BD1' : '#6C5CE7' }}
              onMouseEnter={(e) => { if (!loading) e.currentTarget.style.background = '#5A4BD1' }}
              onMouseLeave={(e) => { if (!loading) e.currentTarget.style.background = '#6C5CE7' }}
            >
              {loading ? (
                <svg className="animate-spin w-4 h-4" viewBox="0 0 24 24" fill="none">
                  <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
                  <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8v8H4z" />
                </svg>
              ) : (
                <LogIn size={16} />
              )}
              {loading ? 'Signing in...' : 'Sign in'}
            </button>
          </form>
        </div>

        <div className="relative z-10 mt-8 w-full max-w-md lg:hidden">
          <BrandMarquee />
        </div>

        <p className="relative z-10 mt-8 text-text-muted text-xs text-center">
          © 2026 Aivar · All rights reserved
        </p>
      </div>
    </div>
  )
}
