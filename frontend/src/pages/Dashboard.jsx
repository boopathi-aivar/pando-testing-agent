import { useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import {
  FolderOpen, PlayCircle, BarChart2, AlertCircle, FileText, Truck,
} from 'lucide-react'
import {
  ResponsiveContainer, LineChart, Line, BarChart, Bar, PieChart, Pie, Cell,
  XAxis, YAxis, CartesianGrid, Tooltip, Legend,
} from 'recharts'
import { getDashboardSummary } from '../api/client'
import { useTheme } from '../theme'

const AIVAR = {
  purple:  '#6C5CE7',
  purple600: '#6256DC',
  accent:  '#A29BFE',
  mid:     '#C4BEFA',
}

const STATUS_PILL = {
  passed:   'bg-pando-green-50 text-pando-green-600 border-pando-green-200',
  warning:  'bg-warning-bg text-warning border-warning/30',
  failed:   'bg-danger-bg text-danger border-danger/20',
  unscored: 'bg-[#6C5CE7]/10 text-[#6C5CE7] border-[#6C5CE7]/30',
}

function chartTheme(isDark) {
  return {
    tooltip: {
      background: isDark ? '#1C1F2A' : '#fff',
      border: isDark ? '1px solid rgba(255,255,255,0.16)' : '1px solid #C4BEFA',
      borderRadius: 12,
      fontSize: 12,
      color: isDark ? '#F5F5F7' : '#111827',
      boxShadow: '0 8px 24px rgba(0,0,0,0.45)',
    },
    tooltipLabel: { color: isDark ? '#F5F5F7' : '#111827', fontWeight: 600 },
    tooltipItem: { color: isDark ? '#F5F5F7' : '#111827' },
    legend: { fontSize: 12, color: isDark ? '#E6E8EE' : '#4B5563' },
    grid: isDark ? 'rgba(255,255,255,0.08)' : '#E0DEFF',
    tick: isDark ? '#8B8F9A' : '#9CA3AF',
    tickStrong: isDark ? '#C5C8D0' : '#4B5563',
  }
}

function StatCard({ label, value, icon: Icon, hint }) {
  return (
    <div className="bg-surface border border-pando-green-100 rounded-2xl p-5 shadow-card">
      <div className="flex items-center justify-between mb-4">
        <span className="text-text-muted text-sm font-medium">{label}</span>
        <div className="w-9 h-9 rounded-xl flex items-center justify-center bg-pando-green-50">
          <Icon size={18} className="text-pando-green" />
        </div>
      </div>
      <p className="text-3xl font-bold text-text-primary mb-1.5 tracking-tight">{value}</p>
      {hint && <p className="text-xs text-text-muted">{hint}</p>}
    </div>
  )
}

function ChartCard({ title, subtitle, children }) {
  return (
    <div className="bg-surface border border-pando-green-100 rounded-2xl shadow-card p-5">
      <div className="mb-4">
        <p className="text-text-primary text-sm font-bold">{title}</p>
        {subtitle && <p className="text-text-muted text-xs mt-0.5">{subtitle}</p>}
      </div>
      {children}
    </div>
  )
}

function EmptyChart({ message = 'No test data yet' }) {
  return (
    <div className="h-[220px] flex items-center justify-center text-text-muted text-sm">
      {message}
    </div>
  )
}

function SkeletonDashboard() {
  return (
    <div className="animate-pulse">
      <div className="grid grid-cols-1 sm:grid-cols-2 xl:grid-cols-4 gap-4 mb-6">
        {Array.from({ length: 4 }).map((_, i) => (
          <div key={i} className="bg-surface border border-pando-green-100 rounded-2xl p-5 h-[118px]" />
        ))}
      </div>
      <div className="grid grid-cols-1 xl:grid-cols-2 gap-4">
        {Array.from({ length: 4 }).map((_, i) => (
          <div key={i} className="bg-surface border border-pando-green-100 rounded-2xl h-[300px]" />
        ))}
      </div>
    </div>
  )
}

function formatTs(ts) {
  if (!ts) return ''
  const d = new Date(ts)
  if (Number.isNaN(d.getTime())) return ts
  return d.toLocaleString(undefined, { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' })
}

export default function Dashboard() {
  const navigate = useNavigate()
  const { isDark } = useTheme()
  const charts = chartTheme(isDark)
  const [data, setData] = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)
  const [projectFilter, setProjectFilter] = useState('all')

  useEffect(() => {
    let cancelled = false
    setLoading(true)
    getDashboardSummary(projectFilter === 'all' ? undefined : projectFilter)
      .then((d) => { if (!cancelled) setData(d) })
      .catch((err) => { if (!cancelled) setError(err.message) })
      .finally(() => { if (!cancelled) setLoading(false) })
    return () => { cancelled = true }
  }, [projectFilter])

  if (loading && !data) return <SkeletonDashboard />

  if (error && !data) {
    return (
      <div className="bg-pando-green-50 border border-pando-green-200 rounded-xl p-4">
        <p className="text-pando-green-700 text-sm font-medium">Failed to load dashboard: {error}</p>
      </div>
    )
  }

  const stats = data?.stats || {}
  const total = stats.total_tests || 0
  const todayDelta = (stats.tests_today || 0) - (stats.tests_yesterday || 0)
  const todayHint = todayDelta === 0
    ? 'Same as yesterday'
    : `${todayDelta > 0 ? '+' : ''}${todayDelta} vs yesterday`

  const scoreByDay = data?.score_by_day || []
  const hasScores = scoreByDay.some((d) => d.avg_score != null)

  const pieData = [
    { name: 'Passed',     value: stats.passed || 0,    color: AIVAR.purple },
    { name: 'Warning',    value: stats.warning || 0,   color: '#D97706' },
    { name: 'Failed',     value: stats.failed || 0,   color: '#DC2626' },
    { name: 'Not scored', value: stats.unscored || 0, color: AIVAR.mid },
  ].filter((d) => d.value > 0)

  const fieldData = [
    { name: 'Wrong',      value: data?.field_issues?.wrong || 0,      fill: AIVAR.purple600 },
    { name: 'Missing',    value: data?.field_issues?.missing || 0,    fill: AIVAR.accent },
    { name: 'Unverified', value: data?.field_issues?.unverified || 0, fill: AIVAR.mid },
  ]
  const hasFieldIssues = fieldData.some((d) => d.value > 0)

  const carriers = data?.carriers || []
  const recent = data?.recent || []
  const projectOptions = data?.project_options || []
  const selected = projectFilter === 'all'
    ? null
    : projectOptions.find((p) => p.project_id === projectFilter)

  return (
    <div>
      <div className="flex items-start justify-between gap-4 mb-5">
        <div>
          <h1 className="text-text-primary font-bold text-2xl tracking-tight">Insights</h1>
          <p className="text-text-muted text-sm mt-1">
            {selected
              ? `Required-field scores for ${selected.project_name}`
              : `Required-field scores across ${data?.project_count || 0} project${(data?.project_count || 0) === 1 ? '' : 's'}.`}
          </p>
        </div>
      </div>

      {projectOptions.length > 0 && (
        <div className="flex items-center gap-2 flex-wrap mb-6">
          <span className="flex items-center gap-1 text-text-muted text-xs font-medium shrink-0">
            <FolderOpen size={12} /> Project
          </span>
          <button
            type="button"
            onClick={() => setProjectFilter('all')}
            className={`px-3 py-1 rounded-full text-xs font-semibold border transition-colors ${
              projectFilter === 'all'
                ? 'bg-pando-green text-white border-pando-green shadow-sm'
                : 'bg-surface text-text-secondary border-border hover:border-pando-green hover:text-pando-green'
            }`}
          >
            All projects
          </button>
          {projectOptions.map((p) => (
            <button
              key={p.project_id}
              type="button"
              onClick={() => setProjectFilter(p.project_id)}
              className={`px-3 py-1 rounded-full text-xs font-semibold border transition-colors ${
                projectFilter === p.project_id
                  ? 'bg-pando-green text-white border-pando-green shadow-sm'
                  : 'bg-surface text-text-secondary border-border hover:border-pando-green hover:text-pando-green'
              }`}
            >
              {p.project_name}
            </button>
          ))}
        </div>
      )}

      {loading && data && (
        <p className="text-xs text-pando-green mb-3">Updating insights…</p>
      )}

      <div className="grid grid-cols-1 sm:grid-cols-2 xl:grid-cols-4 gap-4 mb-6">
        <StatCard
          label="Total tests"
          value={total}
          icon={PlayCircle}
          hint={`${stats.passed || 0} passed · ${stats.warning || 0} warning · ${stats.failed || 0} failed · ${stats.unscored || 0} not scored`}
        />
        <StatCard
          label="Tests today"
          value={stats.tests_today || 0}
          icon={FileText}
          hint={todayHint}
        />
        <StatCard
          label="Average score"
          value={stats.avg_score != null ? `${Math.round(stats.avg_score)}%` : '—'}
          icon={BarChart2}
          hint="Required fields only"
        />
        <StatCard
          label="Failed tests"
          value={stats.failed || 0}
          icon={AlertCircle}
          hint={total ? `${stats.failed_pct}% of all tests` : 'No tests yet'}
        />
      </div>

      <div className="grid grid-cols-1 xl:grid-cols-2 gap-4 mb-4">
        <ChartCard title="Average score by day" subtitle="Required fields · last 14 days">
          {hasScores ? (
            <ResponsiveContainer width="100%" height={240}>
              <LineChart data={scoreByDay} margin={{ top: 8, right: 8, bottom: 0, left: 0 }}>
                <CartesianGrid strokeDasharray="3 3" stroke={charts.grid} vertical={false} />
                <XAxis dataKey="date" tick={{ fill: charts.tick, fontSize: 11 }} axisLine={false} tickLine={false} />
                <YAxis domain={[0, 100]} tick={{ fill: charts.tick, fontSize: 11 }} axisLine={false} tickLine={false} width={32} />
                <Tooltip contentStyle={charts.tooltip} labelStyle={charts.tooltipLabel} itemStyle={charts.tooltipItem} formatter={(v) => [`${v}%`, 'Avg score']} />
                <Line type="monotone" dataKey="avg_score" stroke={AIVAR.purple} strokeWidth={2.5} connectNulls={false} dot={{ r: 3, fill: AIVAR.purple }} />
              </LineChart>
            </ResponsiveContainer>
          ) : <EmptyChart />}
        </ChartCard>

        <ChartCard title="Outcome mix" subtitle="Passed / warning / failed / not scored">
          {pieData.length ? (
            <ResponsiveContainer width="100%" height={240}>
              <PieChart>
                <Pie data={pieData} dataKey="value" nameKey="name" innerRadius={58} outerRadius={88} paddingAngle={3}>
                  {pieData.map((d) => <Cell key={d.name} fill={d.color} />)}
                </Pie>
                <Tooltip contentStyle={charts.tooltip} labelStyle={charts.tooltipLabel} itemStyle={charts.tooltipItem} />
                <Legend iconType="circle" wrapperStyle={charts.legend} />
              </PieChart>
            </ResponsiveContainer>
          ) : <EmptyChart />}
        </ChartCard>
      </div>

      <div className="grid grid-cols-1 xl:grid-cols-2 gap-4 mb-4">
        <ChartCard title="Tests per day" subtitle="Volume over the last 14 days">
          {total ? (
            <ResponsiveContainer width="100%" height={240}>
              <BarChart data={scoreByDay} margin={{ top: 8, right: 8, bottom: 0, left: 0 }}>
                <CartesianGrid strokeDasharray="3 3" stroke={charts.grid} vertical={false} />
                <XAxis dataKey="date" tick={{ fill: charts.tick, fontSize: 11 }} axisLine={false} tickLine={false} />
                <YAxis allowDecimals={false} tick={{ fill: charts.tick, fontSize: 11 }} axisLine={false} tickLine={false} width={28} />
                <Tooltip contentStyle={charts.tooltip} labelStyle={charts.tooltipLabel} itemStyle={charts.tooltipItem} formatter={(v) => [v, 'Tests']} />
                <Bar dataKey="tests" fill={AIVAR.purple} radius={[6, 6, 0, 0]} />
              </BarChart>
            </ResponsiveContainer>
          ) : <EmptyChart />}
        </ChartCard>

        <ChartCard title="Average score by carrier" subtitle="Highest-volume carriers first">
          {carriers.length ? (
            <ResponsiveContainer width="100%" height={240}>
              <BarChart data={carriers} layout="vertical" margin={{ top: 8, right: 12, bottom: 0, left: 8 }}>
                <CartesianGrid strokeDasharray="3 3" stroke={charts.grid} horizontal={false} />
                <XAxis type="number" domain={[0, 100]} tick={{ fill: charts.tick, fontSize: 11 }} axisLine={false} tickLine={false} />
                <YAxis type="category" dataKey="carrier" width={120} tick={{ fill: charts.tickStrong, fontSize: 11 }} axisLine={false} tickLine={false} />
                <Tooltip contentStyle={charts.tooltip} labelStyle={charts.tooltipLabel} itemStyle={charts.tooltipItem} formatter={(v, name) => [name === 'avg_score' ? `${v}%` : v, name === 'avg_score' ? 'Avg score' : 'Tests']} />
                <Bar dataKey="avg_score" fill={AIVAR.purple} radius={[0, 6, 6, 0]} />
              </BarChart>
            </ResponsiveContainer>
          ) : <EmptyChart />}
        </ChartCard>
      </div>

      <div className="grid grid-cols-1 xl:grid-cols-2 gap-4 mb-4">
        <ChartCard title="Required-field issues" subtitle="Wrong, missing, and unverified among required fields">
          {hasFieldIssues ? (
            <ResponsiveContainer width="100%" height={240}>
              <BarChart data={fieldData} margin={{ top: 8, right: 8, bottom: 0, left: 0 }}>
                <CartesianGrid strokeDasharray="3 3" stroke={charts.grid} vertical={false} />
                <XAxis dataKey="name" tick={{ fill: charts.tick, fontSize: 11 }} axisLine={false} tickLine={false} />
                <YAxis allowDecimals={false} tick={{ fill: charts.tick, fontSize: 11 }} axisLine={false} tickLine={false} width={32} />
                <Tooltip contentStyle={charts.tooltip} labelStyle={charts.tooltipLabel} itemStyle={charts.tooltipItem} />
                <Bar dataKey="value" radius={[6, 6, 0, 0]}>
                  {fieldData.map((d) => <Cell key={d.name} fill={d.fill} />)}
                </Bar>
              </BarChart>
            </ResponsiveContainer>
          ) : <EmptyChart />}
        </ChartCard>

        <ChartCard title="Recent invoices" subtitle="Latest stored test results">
          {recent.length === 0 ? (
            <EmptyChart message="Run a test to see recent invoices here." />
          ) : (
            <div className="overflow-x-auto -mx-1">
              <table className="w-full text-sm">
                <thead>
                  <tr className="text-[11px] uppercase tracking-wider text-text-muted">
                    <th className="text-left font-semibold pb-2 pr-3">Invoice</th>
                    <th className="text-left font-semibold pb-2 pr-3">Carrier</th>
                    <th className="text-left font-semibold pb-2 pr-3">Score</th>
                    <th className="text-left font-semibold pb-2">Status</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-border">
                  {recent.map((r) => (
                    <tr
                      key={r.result_id}
                      className="cursor-pointer result-row-hover"
                      onClick={() => r.project_id && navigate(`/project/${r.project_id}/results`)}
                    >
                      <td className="py-2 pr-3">
                        <p className="font-mono font-semibold text-xs text-text-primary">{r.invoice_number}</p>
                        <p className="text-[11px] text-text-muted">{formatTs(r.timestamp)}</p>
                      </td>
                      <td className="py-2 pr-3 text-xs text-text-secondary truncate max-w-[140px]">
                        <span className="inline-flex items-center gap-1">
                          <Truck size={10} className="text-pando-green flex-shrink-0" />
                          {r.vendor_name || '—'}
                        </span>
                      </td>
                      <td className="py-2 pr-3 font-semibold text-xs text-pando-green">{r.overall_score != null ? `${Math.round(r.overall_score)}%` : '—'}</td>
                      <td className="py-2">
                        <span className={`inline-flex px-2 py-0.5 rounded-full text-[11px] font-semibold border capitalize ${STATUS_PILL[r.status] || STATUS_PILL.failed}`}>
                          {r.status}
                        </span>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </ChartCard>
      </div>

      {total === 0 && (
        <div className="flex flex-col items-center justify-center py-12 text-text-muted bg-surface border border-pando-green-100 rounded-2xl shadow-card">
          <div className="w-14 h-14 rounded-2xl bg-pando-green-50 border-2 border-pando-green-100 flex items-center justify-center mb-4">
            <FolderOpen size={24} className="text-pando-green" />
          </div>
          <p className="text-base font-semibold text-text-secondary">No tests to chart yet</p>
          <p className="text-sm mt-1">Run a test from a project to populate these insights.</p>
        </div>
      )}
    </div>
  )
}
