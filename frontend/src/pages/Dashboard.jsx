import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { FolderOpen, PlayCircle, BarChart2, AlertCircle, Plus, TrendingUp, TrendingDown } from 'lucide-react'
import { useProjects } from '../hooks/useProjects'
import ProjectCard from '../components/dashboard/ProjectCard'
import { deleteProject } from '../api/client'

const STAT_ICON_COLORS = {
  green:  { bg: 'bg-pando-green-50',  icon: 'text-pando-green' },
  gold:   { bg: 'bg-pando-gold-100',  icon: 'text-pando-green-600' },
  amber:  { bg: 'bg-warning-bg',      icon: 'text-warning' },
  red:    { bg: 'bg-danger-bg',       icon: 'text-danger' },
}

function StatCard({ label, value, icon: Icon, variant, trend, up }) {
  const { bg, icon } = STAT_ICON_COLORS[variant]
  return (
    <div className="bg-white border border-border rounded-2xl p-5 shadow-card">
      <div className="flex items-center justify-between mb-4">
        <span className="text-text-muted text-sm font-medium">{label}</span>
        <div className={`w-9 h-9 rounded-xl flex items-center justify-center ${bg}`}>
          <Icon size={18} className={icon} />
        </div>
      </div>
      <p className="text-3xl font-bold text-text-primary mb-1.5 tracking-tight">{value}</p>
      <div className={`flex items-center gap-1 text-xs font-medium ${up ? 'text-success' : 'text-danger'}`}>
        {up ? <TrendingUp size={12} /> : <TrendingDown size={12} />}
        {trend}
      </div>
    </div>
  )
}

function SkeletonCard() {
  return (
    <div className="bg-white border border-border rounded-2xl p-5 shadow-card animate-pulse">
      <div className="flex items-start justify-between mb-4">
        <div>
          <div className="h-4 w-32 bg-border rounded-lg mb-2" />
          <div className="h-3 w-20 bg-background rounded-lg" />
        </div>
        <div className="h-6 w-20 bg-border rounded-full" />
      </div>
      <div className="flex items-center gap-4 py-3 border-y border-border mb-4">
        <div className="w-[52px] h-[52px] rounded-full bg-border" />
        <div>
          <div className="h-2.5 w-14 bg-background rounded mb-2" />
          <div className="h-4 w-24 bg-border rounded" />
        </div>
      </div>
      <div className="flex gap-2">
        <div className="flex-1 h-9 bg-background rounded-xl" />
        <div className="flex-1 h-9 bg-border rounded-xl" />
      </div>
    </div>
  )
}

export default function Dashboard() {
  const { projects, loading, error, refetch } = useProjects()
  const [search, setSearch] = useState('')
  const [deletedIds, setDeletedIds] = useState(new Set())
  const navigate = useNavigate()

  async function handleDelete(projectId) {
    await deleteProject(projectId)
    setDeletedIds(prev => new Set([...prev, projectId]))
  }

  const filtered = projects.filter((p) =>
    !deletedIds.has(p.project_id) &&
    (p.project_name.toLowerCase().includes(search.toLowerCase()) ||
     p.project_id.toLowerCase().includes(search.toLowerCase()))
  )

  const stats = [
    { label: 'Total Projects', value: projects.length || 6, icon: FolderOpen, variant: 'green', trend: '+1 this week', up: true },
    { label: 'Tests Run Today', value: 12, icon: PlayCircle, variant: 'gold', trend: '+3 from yesterday', up: true },
    { label: 'Average Score', value: '83%', icon: BarChart2, variant: 'amber', trend: '-2% from last week', up: false },
    { label: 'Failed Tests', value: 2, icon: AlertCircle, variant: 'red', trend: '+1 from yesterday', up: false },
  ]

  return (
    <div className="p-6 max-w-[1400px]">
      {/* Stats */}
      <div className="grid grid-cols-4 gap-4 mb-8">
        {stats.map((s) => <StatCard key={s.label} {...s} />)}
      </div>

      {/* Section header */}
      <div className="flex items-center justify-between mb-5">
        <div>
          <h2 className="text-text-primary font-bold text-base">All Projects</h2>
          <p className="text-text-muted text-xs mt-0.5">{projects.length} projects configured</p>
        </div>
        <div className="flex items-center gap-3">
          <input
            type="text"
            placeholder="Filter by name or ID..."
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            className="w-56 text-sm"
          />
          <button
            onClick={() => navigate('/project/new/configure')}
            className="flex items-center gap-2 px-4 py-2 bg-pando-green hover:bg-pando-green-600 text-white rounded-xl text-sm font-semibold transition-colors shadow-sm"
          >
            <Plus size={14} />
            New Project
          </button>
        </div>
      </div>

      {error && (
        <div className="bg-danger-bg border border-danger/20 rounded-xl p-4 mb-6 flex items-center justify-between">
          <p className="text-danger text-sm font-medium">Failed to load projects: {error}</p>
          <button onClick={refetch} className="text-danger text-sm underline hover:no-underline font-medium">Retry</button>
        </div>
      )}

      {loading ? (
        <div className="grid grid-cols-3 gap-4">
          {Array.from({ length: 6 }).map((_, i) => <SkeletonCard key={i} />)}
        </div>
      ) : filtered.length === 0 ? (
        <div className="flex flex-col items-center justify-center py-20 text-text-muted">
          <div className="w-16 h-16 rounded-2xl bg-pando-green-50 border-2 border-pando-green-100 flex items-center justify-center mb-4">
            <FolderOpen size={28} className="text-pando-green" />
          </div>
          <p className="text-base font-semibold text-text-secondary">No projects found</p>
          <p className="text-sm mt-1">Try adjusting your search or create a new project.</p>
        </div>
      ) : (
        <div className="grid grid-cols-3 gap-4">
          {filtered.map((p) => (
            <ProjectCard key={p.project_id} project={p} onDelete={handleDelete} />
          ))}
        </div>
      )}

    </div>
  )
}
