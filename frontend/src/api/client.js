// ─── Auth token helpers ───────────────────────────────────────────────────────
export const getToken = () => localStorage.getItem('pando_token')
export const getUser  = () => { try { return JSON.parse(localStorage.getItem('pando_user') || 'null') } catch { return null } }
export const setAuth  = (token, user) => { localStorage.setItem('pando_token', token); localStorage.setItem('pando_user', JSON.stringify(user)) }
export const clearAuth = () => { localStorage.removeItem('pando_token'); localStorage.removeItem('pando_user') }

// ─── API base URL ─────────────────────────────────────────────────────────────
// Locally:     empty string  → Vite proxy handles /api/*  → localhost:3001
// In Amplify:  VITE_API_URL  → https://xxx.execute-api.region.amazonaws.com
const API_BASE = import.meta.env.VITE_API_URL || ''

// ─── Base fetch wrapper ───────────────────────────────────────────────────────
async function request(path, options = {}) {
  const token = getToken()
  const headers = { 'Content-Type': 'application/json', ...(options.headers ?? {}) }
  if (token) headers['Authorization'] = `Bearer ${token}`

  const res = await fetch(`${API_BASE}/api${path}`, { ...options, headers })

  if (res.status === 401) {
    clearAuth()
    window.location.href = '/login'
    throw new Error('Session expired. Please sign in again.')
  }
  if (!res.ok) {
    const body = await res.json().catch(() => ({}))
    const detail = body.detail
    const msg = Array.isArray(detail)
      ? detail.map((d) => d.msg || JSON.stringify(d)).join('; ')
      : (detail ?? `Request failed: ${res.status}`)
    throw new Error(msg)
  }
  return res.json()
}

// ─── Auth ─────────────────────────────────────────────────────────────────────
export async function login(email, password) {
  const data = await request('/auth/login', {
    method: 'POST',
    body: JSON.stringify({ email, password }),
  })
  setAuth(data.access_token, data.user)
  return data
}

export async function fetchMe() {
  return request('/auth/me')
}

// ─── Projects ─────────────────────────────────────────────────────────────────
export async function getProjects() {
  return request('/projects')
}

export async function getProject(id) {
  return request(`/projects/${id}`)
}

export async function createProject(data) {
  return request('/projects', { method: 'POST', body: JSON.stringify(data) })
}

export async function updateProject(id, data) {
  return request(`/projects/${id}`, { method: 'PUT', body: JSON.stringify(data) })
}

export async function deleteProject(id) {
  const token = getToken()
  const headers = { 'Content-Type': 'application/json' }
  if (token) headers['Authorization'] = `Bearer ${token}`
  const res = await fetch(`${API_BASE}/api/projects/${id}`, { method: 'DELETE', headers })
  if (res.status === 401) { clearAuth(); window.location.href = '/login'; throw new Error('Session expired') }
  if (!res.ok) { const b = await res.json().catch(() => ({})); throw new Error(b.detail ?? `Delete failed: ${res.status}`) }
}

// ─── Documentation projects ────────────────────────────────────────────────────
export async function getDocProjects() {
  return request('/doc-projects')
}

export async function getDocProject(id) {
  return request(`/doc-projects/${id}`)
}

export async function createDocProject(data) {
  return request('/doc-projects', { method: 'POST', body: JSON.stringify(data) })
}

export async function deleteDocProject(id) {
  const token = getToken()
  const headers = { 'Content-Type': 'application/json' }
  if (token) headers['Authorization'] = `Bearer ${token}`
  const res = await fetch(`${API_BASE}/api/doc-projects/${id}`, { method: 'DELETE', headers })
  if (res.status === 401) { clearAuth(); window.location.href = '/login'; throw new Error('Session expired') }
  if (!res.ok) { const b = await res.json().catch(() => ({})); throw new Error(b.detail ?? `Delete failed: ${res.status}`) }
}

export async function getDocProjectSummary(id) {
  return request(`/doc-projects/${id}/summary`)
}

// ─── Results ──────────────────────────────────────────────────────────────────
export async function getResults(projectId, filters = {}) {
  const params = new URLSearchParams(
    Object.entries(filters).filter(([, v]) => v != null && v !== '' && v !== 'all')
  ).toString()
  return request(`/projects/${projectId}/results${params ? `?${params}` : ''}`)
}

export async function getDashboardSummary(projectId) {
  const q = projectId ? `?project_id=${encodeURIComponent(projectId)}` : ''
  return request(`/dashboard/summary${q}`)
}

export async function getCarriers(projectId) {
  return request(`/projects/${projectId}/carriers`)
}

export async function deleteResult(projectId, resultId) {
  return request(`/projects/${projectId}/results/${resultId}`, { method: 'DELETE' })
}

// ─── Jobs ─────────────────────────────────────────────────────────────────────
export async function runTest(projectId, invoiceNumber) {
  const body = invoiceNumber ? { invoice_number: invoiceNumber } : {}
  return request(`/projects/${projectId}/run-test`, { method: 'POST', body: JSON.stringify(body) })
}

export async function retestResult(projectId, resultId) {
  return request(`/projects/${projectId}/results/${resultId}/retest`, { method: 'POST' })
}

export async function getJobStatus(jobId) {
  return request(`/jobs/${jobId}/status`)
}

// ─── Retrigger ─────────────────────────────────────────────────────────────────
export async function getRetriggerProjects() {
  return request('/retrigger/projects')
}

export async function getRetriggerProject(id) {
  return request(`/retrigger/projects/${id}`)
}

export async function createRetriggerProject(data) {
  return request('/retrigger/projects', { method: 'POST', body: JSON.stringify(data) })
}

export async function updateRetriggerProject(id, data) {
  return request(`/retrigger/projects/${id}`, { method: 'PUT', body: JSON.stringify(data) })
}

export async function deleteRetriggerProject(id) {
  const token = getToken()
  const headers = { 'Content-Type': 'application/json' }
  if (token) headers['Authorization'] = `Bearer ${token}`
  const res = await fetch(`${API_BASE}/api/retrigger/projects/${id}`, { method: 'DELETE', headers })
  if (res.status === 401) { clearAuth(); window.location.href = '/login'; throw new Error('Session expired') }
  if (!res.ok) { const b = await res.json().catch(() => ({})); throw new Error(b.detail ?? `Delete failed: ${res.status}`) }
}

export async function getRetriggerJobs(projectId) {
  const q = projectId ? `?project_id=${encodeURIComponent(projectId)}` : ''
  return request(`/retrigger/jobs${q}`)
}

export async function getRetriggerJob(jobId) {
  return request(`/retrigger/jobs/${jobId}`)
}

export async function createRetriggerJob(projectId, invoiceNumbers, folderName, action = 'retrigger') {
  return request('/retrigger/jobs', {
    method: 'POST',
    body: JSON.stringify({
      project_id: projectId,
      invoice_numbers: invoiceNumbers,
      folder_name: folderName,
      action,
    }),
  })
}

export async function pauseRetriggerJob(jobId) {
  return request(`/retrigger/jobs/${jobId}/pause`, { method: 'POST' })
}

export async function resumeRetriggerJob(jobId) {
  return request(`/retrigger/jobs/${jobId}/resume`, { method: 'POST' })
}

export async function triggerRetriggerFetchRecords(jobId) {
  return request(`/retrigger/jobs/${jobId}/fetch-records`, { method: 'POST' })
}

// ─── Observability ────────────────────────────────────────────────────────────
export async function getObservabilityProjects() {
  return request('/observability/projects')
}

export async function discoverObservabilitySchema(tableName, account = 'ops', sampleLimit = 25) {
  return request('/observability/discover-schema', {
    method: 'POST',
    body: JSON.stringify({
      table_name: tableName,
      account,
      sample_limit: sampleLimit,
    }),
  })
}

export async function getObservabilityViews() {
  return request('/observability/views')
}

export async function getObservabilityView(viewId) {
  return request(`/observability/views/${encodeURIComponent(viewId)}`)
}

export async function createObservabilityView(data) {
  return request('/observability/views', {
    method: 'POST',
    body: JSON.stringify(data),
  })
}

export async function updateObservabilityView(viewId, data) {
  return request(`/observability/views/${encodeURIComponent(viewId)}`, {
    method: 'PUT',
    body: JSON.stringify(data),
  })
}

export async function deleteObservabilityView(viewId) {
  const token = getToken()
  const headers = { 'Content-Type': 'application/json' }
  if (token) headers['Authorization'] = `Bearer ${token}`
  const res = await fetch(
    `${API_BASE}/api/observability/views/${encodeURIComponent(viewId)}`,
    { method: 'DELETE', headers },
  )
  if (res.status === 401) {
    clearAuth()
    window.location.href = '/login'
    throw new Error('Session expired')
  }
  if (!res.ok) {
    const b = await res.json().catch(() => ({}))
    throw new Error(b.detail ?? `Delete failed: ${res.status}`)
  }
  return res.json().catch(() => ({ ok: true }))
}

export async function getObservabilityInvoices(projectId, filters = {}) {
  const params = new URLSearchParams(
    Object.entries(filters).filter(([, v]) => v != null && v !== '' && v !== 'all'),
  ).toString()
  return request(
    `/observability/${encodeURIComponent(projectId)}/invoices${params ? `?${params}` : ''}`,
  )
}

export async function getObservabilityInvoiceDetail(projectId, emailId, attachmentId) {
  return request(
    `/observability/${encodeURIComponent(projectId)}/invoices/${encodeURIComponent(emailId)}/${encodeURIComponent(attachmentId)}`,
  )
}

export async function getObservabilityCloudwatchLink(projectId, emailId, attachmentId) {
  return request(
    `/observability/${encodeURIComponent(projectId)}/invoices/${encodeURIComponent(emailId)}/${encodeURIComponent(attachmentId)}/cloudwatch-link`,
  )
}

export async function retriggerObservabilityInvoice(projectId, emailId) {
  return request(
    `/observability/${encodeURIComponent(projectId)}/invoices/${encodeURIComponent(emailId)}/retrigger`,
    { method: 'POST' },
  )
}

