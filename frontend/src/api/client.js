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
    throw new Error(body.detail ?? `Request failed: ${res.status}`)
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

// ─── Results ──────────────────────────────────────────────────────────────────
export async function getResults(projectId, filters = {}) {
  const params = new URLSearchParams(
    Object.entries(filters).filter(([, v]) => v != null && v !== '' && v !== 'all')
  ).toString()
  return request(`/projects/${projectId}/results${params ? `?${params}` : ''}`)
}

export async function getCarriers(projectId) {
  return request(`/projects/${projectId}/carriers`)
}

// ─── Jobs ─────────────────────────────────────────────────────────────────────
export async function runTest(projectId, invoiceNumber) {
  const body = invoiceNumber ? { invoice_number: invoiceNumber } : {}
  return request(`/projects/${projectId}/run-test`, { method: 'POST', body: JSON.stringify(body) })
}

export async function getJobStatus(jobId) {
  return request(`/jobs/${jobId}/status`)
}
