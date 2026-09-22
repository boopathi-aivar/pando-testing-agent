/** Shared iframe UI for all Observability log dashboards. */
export const OBSERVABILITY_UI = '/observability/delicato/index.html'

/**
 * Hub projects shown on /observability.
 * Unilever uses `tabs` (option B): one card, in-page subnav for two tables.
 * Carrier lists: Delicato only for now; others hide the filter (same as Meta).
 */
export const OBSERVABILITY_PROJECTS = [
  {
    id: 'delicato',
    name: 'Delicato',
    description: 'Invoice processing pipeline logs and status',
    src: OBSERVABILITY_UI,
  },
  {
    id: 'ge',
    name: 'GE',
    description: 'General Electronics invoice processing logs',
    src: OBSERVABILITY_UI,
  },
  {
    id: 'jnj',
    name: 'JnJ',
    description: 'JnJ invoice processing logs',
    src: OBSERVABILITY_UI,
  },
  {
    id: 'otter',
    name: 'Otter',
    description: 'Otter invoice processing logs',
    src: OBSERVABILITY_UI,
  },
  {
    id: 'ghent',
    name: 'Ghent',
    description: 'Ghent invoice processing logs',
    src: OBSERVABILITY_UI,
  },
  {
    id: 'west-marine',
    name: 'West Marine',
    description: 'West Marine invoice processing logs',
    src: OBSERVABILITY_UI,
  },
  {
    id: 'viking',
    name: 'Viking',
    description: 'Viking invoice processing logs',
    src: OBSERVABILITY_UI,
  },
  {
    id: 'unilever',
    name: 'Unilever',
    description: 'Unilever invoice processing — Logs and Excel Demo',
    src: OBSERVABILITY_UI,
    tabs: [
      { id: 'unilever', label: 'Logs' },
      { id: 'unilever-excel', label: 'Excel Demo' },
    ],
  },
  {
    id: 'meta',
    name: 'Meta',
    description: 'Invoice processing pipeline logs and status',
    src: OBSERVABILITY_UI,
  },
]

/** Resolve a route id to a hub project + active API project id (for Unilever tabs). */
export function resolveObservabilityProject(projectId) {
  const direct = OBSERVABILITY_PROJECTS.find((p) => p.id === projectId)
  if (direct) {
    const activeId = direct.tabs?.[0]?.id || direct.id
    return { hub: direct, activeId }
  }
  for (const hub of OBSERVABILITY_PROJECTS) {
    const tab = hub.tabs?.find((t) => t.id === projectId)
    if (tab) return { hub, activeId: tab.id }
  }
  return null
}
