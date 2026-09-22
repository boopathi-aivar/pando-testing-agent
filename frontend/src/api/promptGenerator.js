/**
 * API client for the Prompt Generator (/api/prompt-generator/*)
 * All calls go through the same Vite proxy as the main backend (port 3001).
 * No Authorization header needed — endpoints are internal to the backend.
 */

const BASE = '/api/prompt-generator'

async function pgRequest(path, options = {}) {
  const res = await fetch(`${BASE}${path}`, options)
  if (!res.ok) {
    const body = await res.json().catch(() => ({}))
    throw new Error(body.detail ?? `Request failed: ${res.status}`)
  }
  return res.json()
}

/** GET /health — returns { ok, provider, model } */
export async function pgHealth() {
  return pgRequest('/health')
}

/**
 * POST /extract — multipart form
 * @param {File[]}    pdfFiles      - 1–3 PDF invoices
 * @param {string}    schemaJson    - JSON schema as string
 * @param {string}    samplePrompt  - existing carrier prompt (style reference)
 * @param {File|null} fieldMapping  - optional .xlsx field mapping
 * @param {string|null} threadId   - resume existing session
 */
export async function pgExtract({ pdfFiles, schemaJson, samplePrompt, fieldMapping = null, threadId = null }) {
  const form = new FormData()
  // Append each PDF — backend receives them as a list named "files"
  ;(pdfFiles || []).forEach(f => form.append('files', f))
  form.append('schema', schemaJson)
  form.append('sample_prompt', samplePrompt)
  if (fieldMapping) form.append('field_mapping', fieldMapping)
  if (threadId) form.append('thread_id', threadId)
  return pgRequest('/extract', { method: 'POST', body: form })
}

/**
 * POST /feedback — HITL decision after low-accuracy interrupt
 * @param {string}  threadId
 * @param {boolean} retry - true = use Docling, false = accept as-is
 */
export async function pgFeedback({ threadId, retry }) {
  return pgRequest('/feedback', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ thread_id: threadId, retry }),
  })
}

/** POST /docling-retry — opt-in Docling re-OCR on any completed session */
export async function pgDoclingRetry({ threadId }) {
  return pgRequest('/docling-retry', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ thread_id: threadId }),
  })
}

/**
 * POST /correct — mark wrong fields and/or send a generic note
 * @param {string}   threadId
 * @param {Array}    corrections - [{ path, current_value, note }]
 * @param {string}   genericNote
 * @param {boolean}  refinePrompt
 */
export async function pgCorrect({ threadId, corrections = [], genericNote = '', refinePrompt = true }) {
  return pgRequest('/correct', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      thread_id: threadId,
      corrections,
      generic_note: genericNote,
      refine_prompt: refinePrompt,
    }),
  })
}

/**
 * POST /ask — Q&A grounded in extracted result
 * @param {string} threadId
 * @param {string} prompt
 */
export async function pgAsk({ threadId, prompt }) {
  return pgRequest('/ask', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ thread_id: threadId, prompt }),
  })
}

/** GET /field-mapping/headers — Excel upload rules */
export async function pgFieldMappingHeaders() {
  return pgRequest('/field-mapping/headers')
}
