import { API_ENDPOINTS } from '../config/api'
import { apiFetch } from './http'

export interface IncrementalNotice {
  event_id: string
  project_id: string
  target_id: string
  target_name: string
  task_def_id: string
  run_task_id: string
  record_id: string
  kind: 'new' | 'changed'
  title: string
  summary: string
  source_url: string
  published_label: string
  detected_at: string
  window_since?: string | null
  window_until?: string | null
  delivery_status: string
}

export interface IncrementalFeed {
  items: IncrementalNotice[]
  new_count: number
  changed_count: number
  unread_count: number
  generated_at: string
  days: number
}

export function getIncrementalFeed(projectId = '', after = ''): Promise<IncrementalFeed> {
  const query = new URLSearchParams({ limit: '50' })
  if (projectId) query.set('project_id', projectId)
  if (after) query.set('after', after)
  return apiFetch(`${API_ENDPOINTS.MOBILE_INCREMENTAL_EVENTS}?${query}`)
}
