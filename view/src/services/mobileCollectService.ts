import { apiFetch } from './http'
import { fetchMobileScreenshotBlob } from './mobileService'

// ── 类型 ────────────────────────────────────────────────

export type FieldType = 'string' | 'number' | 'boolean' | 'list'
export type NotifyOn = 'new' | 'changed' | 'both' | 'none'
export type AppInstance = 'primary' | 'clone'

export interface ExtractField {
  name: string
  description?: string
  type: FieldType
}

export interface CollectTaskDef {
  task_def_id: string
  name: string
  project_id?: string | null
  target_id?: string | null
  target_name?: string | null
  target_type?: string
  device_id: string
  app_name: string
  app_instance?: AppInstance
  keywords: string[]
  use_target_keyword_library?: boolean
  include_direct_children?: boolean
  max_resolved_keywords?: number
  swipe_times: number
  swipe_interval: number
  extract_fields: ExtractField[]
  dedup_key_fields: string[]
  notify_on: NotifyOn
  search_hint?: string
  deep_collect?: boolean
  source_link_strategy?: string
  detail_max_items?: number
  detail_max_swipes?: number
  min_score_to_detail?: number
  min_subject_match?: number
  min_score_to_persist?: number
  status?: string
  last_run_task_id?: string | null
  last_run_at?: string | null
  created_at?: string
  updated_at?: string
}

export interface CollectTaskInput {
  name: string
  project_id?: string | null
  target_id?: string | null
  target_name?: string | null
  target_type?: string
  device_id: string
  app_name: string
  app_instance?: AppInstance
  keywords: string[]
  use_target_keyword_library?: boolean
  include_direct_children?: boolean
  max_resolved_keywords?: number
  swipe_times: number
  swipe_interval: number
  extract_fields: ExtractField[]
  dedup_key_fields: string[]
  notify_on: NotifyOn
  search_hint?: string
  deep_collect?: boolean
  source_link_strategy?: string
  detail_max_items?: number
  detail_max_swipes?: number
  min_score_to_detail?: number
  min_subject_match?: number
  min_score_to_persist?: number
}

export interface CollectRecord {
  record_id: string
  task_def_id: string
  project_id?: string | null
  fields: Record<string, unknown>
  keyword?: string
  score?: number | null
  subject_match?: number | null
  screenshot_ids?: string[]
  screenshot_urls?: string[]
  content_hash?: string
  source_url?: string | null
  source_document_id?: string
  source_document_version_id?: string
  target_id?: string
  target_name?: string
  browser_screenshot_ids?: string[]
  browser_screenshot_urls?: string[]
  discovery_screenshot_ids?: string[]
  discovery_screenshot_urls?: string[]
  is_new?: boolean
  is_changed?: boolean
  first_seen?: string
  last_seen?: string
}

export interface DryRunPreviewItem {
  fields: Record<string, unknown>
  score?: number | null
  subject_match?: number | null
  score_reason?: string
  source_url?: string | null
  source_document_id?: string
  source_document_version_id?: string
  target_id?: string
  target_name?: string
  browser_screenshot_urls?: string[]
  contacts_count?: number
  detail?: boolean
  keyword?: string
  screenshot_id?: string
  screenshot_url?: string
}

export interface DryRunResult {
  run_task_id: string
  task_def_id?: string
  stopped: boolean
  preview: DryRunPreviewItem[]
  total: number
  new: number
  changed: number
}

export interface ResolvedTaskKeywords {
  channel: string
  keywords: string[]
  target_ids: string[]
  sources: string[]
  keyword_targets: Record<string, { target_id: string; target_name: string }>
}

export interface TriggerDef {
  type: 'interval' | 'cron'
  interval_seconds?: number | null
  cron?: string | null
}

export interface ScheduleDef {
  schedule_id: string
  name: string
  target_type: string
  target_id: string
  trigger: TriggerDef
  enabled: boolean
  last_run?: string | null
  next_run?: string | null
  created_at?: string
  updated_at?: string
}

export interface CollectPreset {
  preset_id: string
  title: string
  description: string
  task: Partial<CollectTaskInput>
  suggested_trigger: TriggerDef
}

export interface SourceLinkStrategyOption {
  strategy: string
  label: string
  description: string
}

const BASE = '/v1/mobile-collect'

// ── 任务定义 ────────────────────────────────────────────

export function listTaskDefs(projectId?: string) {
  const q = projectId ? `?project_id=${encodeURIComponent(projectId)}` : ''
  return apiFetch<{ items: CollectTaskDef[]; total: number }>(`${BASE}/tasks${q}`)
}

export function getTaskDef(taskDefId: string) {
  return apiFetch<CollectTaskDef>(`${BASE}/tasks/${encodeURIComponent(taskDefId)}`)
}

export function getResolvedTaskKeywords(taskDefId: string) {
  return apiFetch<ResolvedTaskKeywords>(
    `${BASE}/tasks/${encodeURIComponent(taskDefId)}/resolved-keywords`,
  )
}

export function listSourceLinkStrategies() {
  return apiFetch<{ items: SourceLinkStrategyOption[] }>(`${BASE}/source-link-strategies`)
}

export function createTaskDef(payload: CollectTaskInput) {
  return apiFetch<CollectTaskDef>(`${BASE}/tasks`, {
    method: 'POST',
    body: JSON.stringify(payload),
  })
}

export function updateTaskDef(taskDefId: string, payload: Partial<CollectTaskInput>) {
  return apiFetch<CollectTaskDef>(`${BASE}/tasks/${encodeURIComponent(taskDefId)}`, {
    method: 'PATCH',
    body: JSON.stringify(payload),
  })
}

export function deleteTaskDef(taskDefId: string) {
  return apiFetch<{ ok: boolean }>(`${BASE}/tasks/${encodeURIComponent(taskDefId)}`, {
    method: 'DELETE',
  })
}

export function runTaskDef(taskDefId: string) {
  return apiFetch<{ task_id: string; task_def_id: string; status: string }>(
    `${BASE}/tasks/${encodeURIComponent(taskDefId)}/run`,
    { method: 'POST' },
  )
}

export function stopTaskDef(taskDefId: string, runTaskId?: string) {
  return apiFetch<{ ok: boolean; run_task_id: string }>(
    `${BASE}/tasks/${encodeURIComponent(taskDefId)}/stop`,
    { method: 'POST', body: JSON.stringify({ run_task_id: runTaskId ?? null }) },
  )
}

/** 试跑预览:导航+截屏+结构化,但不入库、不通知,返回预览结果(同步长请求)。 */
export function dryRunTaskDef(taskDefId: string, previewLimit = 50) {
  return apiFetch<DryRunResult>(
    `${BASE}/tasks/${encodeURIComponent(taskDefId)}/dry-run`,
    { method: 'POST', body: JSON.stringify({ preview_limit: previewLimit }) },
  )
}

// ── 采集记录 ────────────────────────────────────────────

export function listRecords(params: {
  task_def_id?: string
  project_id?: string
  target_id?: string
  only_incremental?: boolean
  skip?: number
  limit?: number
}) {
  return apiFetch<{ items: CollectRecord[]; total: number; skip: number; limit: number }>(
    `${BASE}/records/list`,
    { method: 'POST', body: JSON.stringify(params) },
  )
}

// ── 定时调度 ────────────────────────────────────────────

export function listSchedules(targetId?: string) {
  const q = targetId ? `?target_id=${encodeURIComponent(targetId)}` : ''
  return apiFetch<{ items: ScheduleDef[]; total: number }>(`${BASE}/schedules${q}`)
}

export function createSchedule(payload: {
  name: string
  target_id: string
  trigger: TriggerDef
  enabled: boolean
}) {
  return apiFetch<ScheduleDef>(`${BASE}/schedules`, {
    method: 'POST',
    body: JSON.stringify(payload),
  })
}

export function updateSchedule(
  scheduleId: string,
  payload: { name?: string; trigger?: TriggerDef; enabled?: boolean },
) {
  return apiFetch<ScheduleDef>(`${BASE}/schedules/${encodeURIComponent(scheduleId)}`, {
    method: 'PATCH',
    body: JSON.stringify(payload),
  })
}

export function deleteSchedule(scheduleId: string) {
  return apiFetch<{ ok: boolean }>(`${BASE}/schedules/${encodeURIComponent(scheduleId)}`, {
    method: 'DELETE',
  })
}

// ── 预设模板 ────────────────────────────────────────────

export function listPresets() {
  return apiFetch<{ items: CollectPreset[] }>(`${BASE}/presets`)
}

// ── 截图(鉴权) ──────────────────────────────────────────

/** 拉取鉴权截图并返回 ObjectURL(调用方负责 revoke)。复用手机模块的鉴权 blob 取图。 */
export async function fetchScreenshotObjectUrl(pathOrUrl: string, signal?: AbortSignal): Promise<string> {
  const blob = await fetchMobileScreenshotBlob(pathOrUrl, signal)
  return URL.createObjectURL(blob)
}
