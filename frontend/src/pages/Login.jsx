import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { Eye, EyeOff, LogIn, CheckCircle, Globe, BarChart2, Zap } from 'lucide-react'
import { login } from '../api/client'

const FEATURES = [
  { icon: Zap,         text: 'AI-powered invoice field extraction testing' },
  { icon: BarChart2,   text: 'Automated prompt quality scoring & suggestions' },
  { icon: Globe,       text: 'Multi-carrier, multi-region support' },
  { icon: CheckCircle, text: 'CloudWatch log analysis & SES report delivery' },
]

function AivarIcon({ size = 36 }) {
  return (
    <svg width={size} height={size} viewBox="0 0 28 28" fill="none" xmlns="http://www.w3.org/2000/svg">
      <path d="M14 2L25.5 8.5V19.5L14 26L2.5 19.5V8.5L14 2Z" stroke="white" strokeWidth="1.8" fill="none" strokeLinejoin="round"/>
      <path d="M9 14.5L12.5 18L19 11" stroke="#A29BFE" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round"/>
    </svg>
  )
}

export default function Login() {
  const navigate = useNavigate()
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

  return (
    <div className="min-h-screen flex" style={{ fontFamily: 'Inter, sans-serif' }}>

      {/* ── Left brand panel ────────────────────────────────────────── */}
      <div
        className="hidden lg:flex lg:w-[45%] flex-col justify-between px-12 py-10 relative overflow-hidden"
        style={{ background: '#0D1117' }}
      >
        {/* Background decoration */}
        <div className="absolute top-0 right-0 w-96 h-96 rounded-full opacity-10" style={{ background: '#6C5CE7', transform: 'translate(40%, -40%)' }} />
        <div className="absolute bottom-0 left-0 w-64 h-64 rounded-full opacity-10" style={{ background: '#6C5CE7', transform: 'translate(-40%, 40%)' }} />
        <div className="absolute top-1/2 left-1/2 w-[500px] h-[500px] rounded-full opacity-[0.04]" style={{ background: '#A29BFE', transform: 'translate(-50%, -50%)' }} />

        {/* Logo */}
        <div className="relative z-10">
          <div className="flex items-center gap-3">
            <AivarIcon size={40} />
            <span className="text-white font-bold text-3xl tracking-widest uppercase">Aivar</span>
          </div>
          <p className="text-white/40 text-sm mt-2 tracking-widest uppercase font-medium">Pando Testing Agent</p>
        </div>

        {/* Hero copy */}
        <div className="relative z-10">
          <h1 className="text-white font-bold leading-tight mb-4" style={{ fontSize: 36 }}>
            Automate invoice<br />
            <span style={{ color: '#A29BFE' }}>quality assurance</span><br />
            at scale.
          </h1>
          <p className="text-white/60 text-base leading-relaxed mb-10">
            Validate LLM extraction accuracy, score prompt quality,
            and track field-level regressions — all in one platform.
          </p>

          <div className="space-y-4">
            {FEATURES.map(({ icon: Icon, text }) => (
              <div key={text} className="flex items-center gap-3">
                <div className="w-8 h-8 rounded-lg flex items-center justify-center flex-shrink-0" style={{ background: 'rgba(108,92,231,0.20)' }}>
                  <Icon size={15} style={{ color: '#A29BFE' }} />
                </div>
                <span className="text-white/70 text-sm">{text}</span>
              </div>
            ))}
          </div>
        </div>

        {/* Bottom trust line */}
        <div className="relative z-10 flex items-center gap-3">
          <div className="flex -space-x-2">
            {['GE', 'DH', 'MR', 'FX'].map((initials) => (
              <div key={initials} className="w-7 h-7 rounded-full border-2 flex items-center justify-center text-[9px] font-bold"
                style={{ background: '#1C1F2E', borderColor: '#0D1117', color: '#A29BFE' }}>
                {initials}
              </div>
            ))}
          </div>
          <p className="text-white/40 text-xs">Trusted by 6+ logistics teams</p>
        </div>
      </div>

      {/* ── Right login form ─────────────────────────────────────────── */}
      <div className="flex-1 flex flex-col items-center justify-center bg-background px-6 py-12">
        {/* Mobile logo */}
        <div className="flex items-center gap-2.5 mb-10 lg:hidden">
          <svg width="28" height="28" viewBox="0 0 28 28" fill="none">
            <path d="M14 2L25.5 8.5V19.5L14 26L2.5 19.5V8.5L14 2Z" stroke="#6C5CE7" strokeWidth="1.8" fill="none" strokeLinejoin="round"/>
            <path d="M9 14.5L12.5 18L19 11" stroke="#A29BFE" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round"/>
          </svg>
          <span className="font-bold text-2xl tracking-widest uppercase" style={{ color: '#6C5CE7' }}>Aivar</span>
        </div>

        <div className="w-full max-w-md">
          {/* Heading */}
          <div className="mb-8">
            <h2 className="text-text-primary font-bold text-2xl mb-1">Welcome back</h2>
            <p className="text-text-muted text-sm">Sign in to Pando Testing Agent</p>
          </div>

          {/* Form */}
          <form onSubmit={handleSubmit} className="space-y-5">
            <div>
              <label className="block text-text-primary text-sm font-semibold mb-2">Email address</label>
              <input
                type="email"
                value={email}
                onChange={(e) => { setEmail(e.target.value); setError('') }}
                placeholder="you@example.com"
                className="w-full py-3 px-4"
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
                  className="w-full py-3 px-4 pr-11"
                  autoComplete="current-password"
                />
                <button
                  type="button"
                  onClick={() => setShowPwd((v) => !v)}
                  className="absolute right-3 top-1/2 -translate-y-1/2 text-text-muted hover:text-text-secondary transition-colors"
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

          <div className="mt-8 p-4 bg-pando-green-50 border border-pando-green-100 rounded-xl">
            <p className="text-pando-green text-xs font-semibold mb-2 uppercase tracking-wider">Demo credentials</p>
            <div className="space-y-1 font-mono text-xs text-pando-green-600">
              <div className="flex justify-between">
                <span className="text-text-muted">Email</span>
                <span className="font-semibold">pando@aivar.tech</span>
              </div>
              <div className="flex justify-between">
                <span className="text-text-muted">Password</span>
                <span className="font-semibold">pando@123</span>
              </div>
            </div>
          </div>
        </div>

        <p className="mt-10 text-text-muted text-xs text-center">
          © 2024 Aivar · All rights reserved
        </p>
      </div>
    </div>
  )
}
