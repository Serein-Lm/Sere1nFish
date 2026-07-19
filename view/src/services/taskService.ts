import { apiFetch } from './http'
import type { PaginatedResponse } from './projectService'

// ============================================
// 任务
// ============================================

export type TaskType = 'url_scan' | 'xhs_search' | 'douyin_search' | 'web_tagging' | 'company_scan' | 'fofa_collect' | 'scholar_contact'
export type TaskStatus = 'pending' | 'running' | 'completed' | 'error' | 'failed' | 'paused' | 'cancelled'

export interface TaskProgress {
  stage?: string
  message?: string
  last_activity_at?: string
  total_urls?: number
  alive_urls?: number
  scanned_urls?: number
  total_findings?: number
  total_copywritings?: number
  sources?: Record<string, TaskSourceProgress>
}

export interface TaskSourceProgress {
  source: string
  status: string
  processed?: number
  total?: number
  succeeded?: number
  failed?: number
  skipped?: number
  remaining?: number
  message?: string
  updated_at?: string
  current_keyword?: string
}

export type XhsTargetSelectionMode = 'auto' | 'manual'
export type XhsTargetSelectionStatus = 'pending' | 'completed' | 'fallback' | 'disabled' | 'restored'
export type WechatTargetSelectionMode = 'auto' | 'all'
export type WechatTargetSelectionStatus = 'pending' | 'completed' | 'fallback' | 'disabled' | 'restored'

export interface XhsTargetDecision {
  target_id: string
  target_name: string
  target_category: string
  should_collect_xhs: boolean
  reason: string
  confidence: number
  source: 'ai' | 'manual' | 'fallback'
}

export interface XhsTargetSelectionResult {
  mode: XhsTargetSelectionMode
  status: XhsTargetSelectionStatus
  prompt_slug?: string | null
  manual_targets: string[]
  matched_manual_targets: string[]
  unmatched_manual_targets: string[]
  decisions: XhsTargetDecision[]
  selected_count: number
  skipped_count: number
  error?: string | null
}

export interface WechatTargetDecision {
  target_id: string
  target_name: string
  target_category: string
  should_collect_wechat: boolean
  collection_priority?: 'high' | 'normal' | 'low' | 'skip'
  reason: string
  confidence: number
  source: 'ai' | 'all' | 'fallback'
}

export interface WechatTargetSelectionResult {
  mode: WechatTargetSelectionMode
  status: WechatTargetSelectionStatus
  prompt_slug?: string | null
  decisions: WechatTargetDecision[]
  selected_count: number
  skipped_count: number
  error?: string | null
}

export interface TaskResult {
  [key: string]: unknown
  xhs?: {
    enabled?: boolean
    root_selected?: boolean
    selection?: XhsTargetSelectionResult
  }
  wechat?: {
    enabled?: boolean
    selected?: boolean
    priority?: 'high' | 'normal' | 'low' | 'skip' | null
    selection?: WechatTargetSelectionResult
  }
  scholar?: {
    enabled?: boolean
    direction?: string
    direction_source?: string
    direction_terms?: string[]
  }
}

export interface Task {
  task_id: string
  project_id: string
  task_type: TaskType
  params: Record<string, unknown>
  batch_id?: string
  batch_index?: number
  batch_total?: number
  status: TaskStatus
  progress: TaskProgress
  result?: TaskResult
  elapsed_ms?: number
  error: string | null
  created_at: string
  updated_at: string
}

// ============================================
// Finding（来自 web-tagging 记录内嵌）
// ============================================

export type FindingType = 'hr_contact' | 'business_contact' | 'customer_service' | 'tech_support' | 'social_media' | 'download' | 'form' | 'other'
export type ChannelType = 'email' | 'phone' | 'wechat' | 'qq' | 'form' | 'app' | 'other'

export interface Finding {
  finding_id: string
  task_id?: string
  project_id?: string
  url: string
  type: FindingType | string
  scope?: string
  channel: ChannelType | string
  role: string
  subtype?: string | null
  label: string | null
  value: string | null
  context: string
  source_url?: string
  evidence: string
  attention_score: number
  attention_reason: string
}

// ============================================
// 话术
// ============================================

export type ScriptChannel = 'wechat' | 'email' | 'phone' | 'sms' | 'intranet'

export interface DialogueLine {
  role: 'attacker' | 'target'
  content: string
  tactic: string | null
}

export interface EmailTemplate {
  from?: string
  subject?: string
  body?: string
  signature?: string
}

export interface Script {
  channel: ScriptChannel | string
  dialogue: DialogueLine[]
  email_template: EmailTemplate | string | null
  key_points: string[]
}

export interface LogicChainStep {
  step: number
  channel: string
  action: string
  fallback: string | null
}

export interface Scenario {
  scenario_name: string
  target_background?: string
  scenario_overview?: string
  faked_identity: {
    name: string
    company: string
    company_desc?: string
    position: string
    background?: string
    personality?: string
  }
  logic_chain: LogicChainStep[]
  risk_notes?: string | null
}

export interface Payload {
  archive_name: string
  exe_name: string
  icon_disguise: string
  compression_method?: string
  password: string
  notes?: string | null
}

export interface Objection {
  objection: string
  response: string
  tactic: string
  context_note?: string
}

export interface FindingCopywriting {
  finding_id: string
  url: string
  finding_type: string
  finding_channel: string
  finding_label: string
  finding_value: string
  scenario: Scenario | null
  scripts: Script[]
  payload: Payload | null
  objections: Objection[]
  target_analysis?: string
  psychology_strategy?: string
  case_reference?: string
  loaded_skills: string[]
  status: string
  error?: string | null
}

export interface Skill {
  id: string
  name: string
  description: string
  category: string
  phases: string[]
  tags: string[]
  priority: number
  enabled: boolean
}

// ============================================
// 项目级 Findings（统一数据模型）
// ============================================

export type FindingSource = 'web_tagging' | 'xhs' | 'douyin' | 'mobile'

export interface UnifiedFinding {
  finding_id: string
  project_id: string
  task_id: string
  source: FindingSource
  type: string
  channel?: string
  label: string | null
  value: string | null
  url?: string
  attention_score: number
  attention_reason?: string
  has_profile?: boolean
  notes_count?: number
}

export interface FindingsSummary {
  total: number
  by_source: Record<string, number>
  by_type: Record<string, number>
  score_distribution: {
    high: number
    medium: number
    low: number
  }
  safe_count: number
  tasks_count: number
}

export interface FindingsListResponse {
  items: UnifiedFinding[]
  total: number
  page: number
  page_size: number
}

export interface FindingProfile {
  finding_id: string
  user_id?: string
  nickname?: string
  contact_id?: string
  device_id?: string
  platform?: string
  avatar_url?: string
  persona?: Record<string, unknown>
  identity: Record<string, unknown>
  personality_profile: Record<string, unknown>
  attack_surface: Record<string, unknown>
  attention_score: number
  tags: string[]
  common_phrases?: string[]
  risk_signals?: string[]
  communication_style?: string
  tone?: string
  reply_pattern?: string
  notes_count: number
}

export interface FindingNote {
  note_id: string
  title: string
  liked_count: number
  attention_score: number
  tags: string[]
}

// ============================================
// API 调用
// ============================================

export async function createTask(
  projectId: string,
  body: { task_type: TaskType; params: Record<string, unknown> }
): Promise<{ task_id: string; task_type: string; status: string }> {
  return apiFetch(`/v1/projects/${encodeURIComponent(projectId)}/tasks`, {
    method: 'POST',
    body: JSON.stringify(body),
  })
}

export interface CompanyScanBatchResponse {
  batch_id: string
  task_type: 'company_scan'
  task_count: number
  task_ids: string[]
  concurrency: number
  status: string
}

export async function createCompanyScanBatch(
  projectId: string,
  body: { company_names: string[]; params: Record<string, unknown> },
): Promise<CompanyScanBatchResponse> {
  return apiFetch(`/v1/projects/${encodeURIComponent(projectId)}/tasks/company-scan-batch`, {
    method: 'POST',
    body: JSON.stringify(body),
  })
}

export async function uploadTask(projectId: string, formData: FormData): Promise<{ task_id: string; task_type: string; status: string }> {
  const token = localStorage.getItem('token')
  const headers: Record<string, string> = {}
  if (token) headers['Authorization'] = `Bearer ${token}`
  const base = import.meta.env.VITE_API_BASE_URL || '/api'
  const response = await fetch(`${base}/v1/projects/${encodeURIComponent(projectId)}/tasks/upload`, { method: 'POST', headers, body: formData })
  if (!response.ok) throw new Error(await response.text().catch(() => `HTTP ${response.status}`))
  return response.json()
}

/** POST 分页查询任务列表 */
export async function listTasks(projectId: string, params?: {
  page?: number
  page_size?: number
  task_type?: TaskType
}): Promise<PaginatedResponse<Task>> {
  return apiFetch(`/v1/projects/${encodeURIComponent(projectId)}/tasks/list`, {
    method: 'POST',
    body: JSON.stringify({
      project_id: projectId,
      page: params?.page ?? 1,
      page_size: params?.page_size ?? 50,
      task_type: params?.task_type ?? '',
    }),
  })
}

export async function getTask(projectId: string, taskId: string): Promise<Task> {
  return apiFetch(`/v1/projects/${encodeURIComponent(projectId)}/tasks/${encodeURIComponent(taskId)}`, { method: 'GET' })
}

export async function deleteTask(projectId: string, taskId: string): Promise<{
  task_id: string; deleted: boolean; deleted_findings: number; deleted_copywritings: number
}> {
  return apiFetch(`/v1/projects/${encodeURIComponent(projectId)}/tasks/${encodeURIComponent(taskId)}`, { method: 'DELETE' })
}

export async function batchDeleteTasks(projectId: string, params?: {
  status?: TaskStatus
}): Promise<{ deleted_count: number; task_ids: string[] }> {
  const qs = new URLSearchParams()
  if (params?.status) qs.set('status', params.status)
  const query = qs.toString()
  return apiFetch(`/v1/projects/${encodeURIComponent(projectId)}/tasks${query ? `?${query}` : ''}`, { method: 'DELETE' })
}

// Findings
export async function getProjectFindingsSummary(projectId: string): Promise<FindingsSummary> {
  return apiFetch(`/v1/projects/${encodeURIComponent(projectId)}/findings/summary`, { method: 'GET' })
}

/** POST 分页查询 findings */
export async function listProjectFindings(projectId: string, params?: {
  page?: number
  page_size?: number
  source?: FindingSource
  task_id?: string
  type?: string
  min_score?: number
  sort?: 'score_desc' | 'score_asc' | 'time_desc'
  include_safe?: boolean
}): Promise<FindingsListResponse> {
  return apiFetch(`/v1/projects/${encodeURIComponent(projectId)}/findings`, {
    method: 'POST',
    body: JSON.stringify({
      project_id: projectId,
      page: params?.page ?? 1,
      page_size: params?.page_size ?? 10,
      source: params?.source ?? '',
      task_id: params?.task_id ?? '',
      type: params?.type ?? '',
      min_score: params?.min_score ?? 0,
      sort: params?.sort ?? 'score_desc',
      include_safe: params?.include_safe ?? false,
    }),
  })
}

export async function getFindingDetail(findingId: string): Promise<UnifiedFinding> {
  return apiFetch(`/v1/findings/${encodeURIComponent(findingId)}`, { method: 'GET' })
}

export async function getFindingProfile(findingId: string): Promise<FindingProfile> {
  return apiFetch(`/v1/findings/${encodeURIComponent(findingId)}/profile`, { method: 'GET' })
}

export async function getFindingCopywriting(findingId: string): Promise<FindingCopywriting> {
  return apiFetch(`/v1/findings/${encodeURIComponent(findingId)}/copywriting`, { method: 'GET' })
}

export async function getFindingNotes(findingId: string): Promise<{ notes: FindingNote[] }> {
  return apiFetch(`/v1/findings/${encodeURIComponent(findingId)}/notes`, { method: 'GET' })
}

/** 按需生成话术 */
export async function generateFindingCopywriting(findingId: string): Promise<FindingCopywriting> {
  return apiFetch(`/v1/findings/${encodeURIComponent(findingId)}/generate-copywriting`, { method: 'POST' })
}

// Skills
export async function listSkills(): Promise<{ skills: Skill[]; summary: Record<string, unknown> }> {
  return apiFetch('/v1/skills', { method: 'GET' })
}


// ============================================
// 统计
// ============================================

export interface ModelStatsItem {
  calls: number
  total_tokens: number
  cost_yuan: number
}

export interface StatsBase {
  total_calls: number
  total_input_tokens?: number
  total_output_tokens?: number
  total_tokens: number
  total_cost_yuan: number
  total_duration_ms: number
  by_model: Record<string, ModelStatsItem>
}

export interface ProjectStatsSummary {
  project_id: string
  total_calls: number
  total_tokens: number
  total_cost_yuan: number
}

export interface TaskStatsSummary {
  task_id: string
  total_calls: number
  total_tokens: number
  total_cost_yuan: number
}

export interface AgentStatsSummary {
  agent: string
  total_calls: number
  total_tokens: number
  total_cost_yuan: number
  total_duration_ms: number
}

export interface GlobalStatsResponse {
  global: StatsBase
  projects: ProjectStatsSummary[]
}

export interface ProjectStatsResponse {
  stats: StatsBase
  tasks: TaskStatsSummary[]
}

export interface TaskStatsResponse {
  stats: StatsBase
  agents: AgentStatsSummary[]
}

export interface StatsRecord {
  model: string
  input_tokens: number
  output_tokens: number
  cost_yuan: number
  duration_ms: number
  project_id: string
  task_id: string
  phase: string
  agent: string
  timestamp: number
}

export async function getGlobalStats(): Promise<GlobalStatsResponse> {
  return apiFetch('/v1/stats/global', { method: 'GET' })
}

export async function getProjectStats(projectId: string): Promise<ProjectStatsResponse> {
  return apiFetch(`/v1/stats/project/${encodeURIComponent(projectId)}`, { method: 'GET' })
}

export async function getTaskStats(taskId: string): Promise<TaskStatsResponse> {
  return apiFetch(`/v1/stats/task/${encodeURIComponent(taskId)}`, { method: 'GET' })
}

export async function getStatsRecords(params?: {
  project_id?: string; task_id?: string; limit?: number
}): Promise<{ records: StatsRecord[] }> {
  const qs = new URLSearchParams()
  if (params?.project_id) qs.set('project_id', params.project_id)
  if (params?.task_id) qs.set('task_id', params.task_id)
  if (params?.limit) qs.set('limit', String(params.limit))
  return apiFetch(`/v1/stats/records?${qs}`, { method: 'GET' })
}
