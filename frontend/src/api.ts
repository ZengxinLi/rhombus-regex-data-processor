// Keep browser traffic same-origin. Vite proxies /api to the Django service in
// Docker, so a public deployment does not point visitors at their own localhost.
const API_BASE = import.meta.env.VITE_API_BASE_URL || '/api'

export type FileItem = { key: string; size: number; last_modified: string | null; file_type: string }
export type S3Connection = { connection_id: string; bucket: string; files: FileItem[] }
export type Job = {
  id: string
  status: 'QUEUED' | 'RUNNING' | 'SUCCESS' | 'FAILED' | 'CANCELLED'
  progress: number
  total_rows: number | null
  processed_rows: number
  source: { bucket: string; key: string }
  selected_columns: string[]
  requested_operation: string
  resolved_operation: string | null
  regex_pattern: string | null
  error: string | null
  result_available: boolean
}
export type ResultPage = {
  columns: string[]
  rows: Record<string, unknown>[]
  page: number
  page_size: number
  preview_rows: number
  total_rows: number | null
  has_next: boolean
  has_previous: boolean
  is_bounded_preview: boolean
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${API_BASE}${path}`, {
    headers: { 'Content-Type': 'application/json', ...(init?.headers || {}) },
    ...init,
  })
  const payload = await response.json().catch(() => ({}))
  if (!response.ok) throw new Error(payload.error || 'The request failed.')
  return payload as T
}

export const api = {
  connectS3: (body: Record<string, string>) => request<S3Connection>('/s3/connect/', { method: 'POST', body: JSON.stringify(body) }),
  inspectSource: (connection_id: string, source_key: string) => request<{ columns: string[] }>('/s3/inspect/', { method: 'POST', body: JSON.stringify({ connection_id, source_key }) }),
  createJob: (body: Record<string, unknown>) => request<Job>('/jobs/', { method: 'POST', body: JSON.stringify(body) }),
  getJob: (id: string) => request<Job>(`/jobs/${id}/`),
  cancelJob: (id: string) => request<Job>(`/jobs/${id}/cancel/`, { method: 'POST' }),
  getResults: (id: string, page: number) => request<ResultPage>(`/jobs/${id}/results/?page=${page}&page_size=25`),
}
