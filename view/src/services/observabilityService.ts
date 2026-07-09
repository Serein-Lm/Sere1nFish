import { apiFetch } from './http'

export interface TokenStats {
  total_calls: number
  total_input_tokens: number
  total_output_tokens: number
  total_tokens: number
  total_cost_yuan: number
  total_duration_ms: number
  by_model: Record<string, { calls: number; input_tokens: number; output_tokens: number; cost_yuan: number }>
  by_phase: Record<string, { calls: number; input_tokens: number; output_tokens: number; cost_yuan: number }>
  by_agent: Record<string, { calls: number; input_tokens: number; output_tokens: number; cost_yuan: number }>
  by_task_type: Record<string, { calls: number; input_tokens: number; output_tokens: number; cost_yuan: number }>
}

export interface TokenTurnCall {
  call_index: number
  model: string
  input_tokens: number
  output_tokens: number
  total_tokens: number
  cost_yuan: number
  duration_ms: number
  project_id: string
  task_id: string
  task_type: string
  turn_id: string
  run_id: string
  phase: string
  agent: string
  langgraph_node: string
  timestamp: number
}

export interface TokenTurn extends TokenStats {
  turn_key: string
  turn_id: string
  project_id: string
  task_id: string
  task_type: string
  started_at: number
  ended_at: number
  calls: TokenTurnCall[]
}

export interface TokenTurnsResponse {
  items: TokenTurn[]
  total: number
  limit: number
}

export interface TokenHierarchyTask {
  stats: TokenStats
  phases: Record<string, TokenStats>
}

export interface TokenHierarchyProject {
  stats: TokenStats
  tasks: Record<string, TokenHierarchyTask>
}

export interface TokenHierarchy {
  global: TokenStats
  projects: Record<string, TokenHierarchyProject>
}

export interface TaskStatusCounts {
  total: number
  by_status: Record<string, number>
}

export interface LogEntry {
  log_id: string
  ts: number
  level: string
  source: string
  event: string
  message: string
  data?: Record<string, any>
  project_id?: string
  task_id?: string
  phase?: string
  agent?: string
}

export interface OverviewData {
  token: TokenStats
  tasks: TaskStatusCounts
  logs: {
    by_level: Record<string, number>
    recent_warn_error: LogEntry[]
  }
  recent_failed_tasks: Array<{
    task_id: string
    project_id: string
    task_type: string
    error: string
    updated_at: string
  }>
}

export interface LogQueryParams {
  page?: number
  page_size?: number
  project_id?: string
  task_id?: string
  source?: string
  level?: string
  min_level?: string
  event?: string
  since?: number
}

export interface PagedLogs {
  items: LogEntry[]
  total: number
  page: number
  page_size: number
}

export interface ScenarioStat {
  task_type: string
  token: TokenStats
  tasks: {
    total: number
    by_status: Record<string, number>
  }
}

// 任务场景中文名映射（Dashboard / 观测面板共用）
export const TASK_TYPE_LABELS: Record<string, string> = {
  url_scan: 'URL 扫描',
  xhs_search: '小红书搜索',
  douyin_search: '抖音搜索',
  web_tagging: '官网打标',
  company_scan: '综合公司扫描',
  fofa_collect: 'FOFA 资产采集',
}

export function taskTypeLabel(taskType: string): string {
  return TASK_TYPE_LABELS[taskType] || taskType || '未知'
}

export interface ScenariosResponse {
  items: ScenarioStat[]
  total: number
}

export async function getOverview(): Promise<OverviewData> {
  return apiFetch<OverviewData>('/v1/observability/overview')
}

export async function getScenarios(): Promise<ScenariosResponse> {
  return apiFetch<ScenariosResponse>('/v1/observability/scenarios')
}

export async function getStats(params?: {
  project_id?: string
  task_id?: string
  phase?: string
  agent?: string
  task_type?: string
}): Promise<TokenStats> {
  const qs = new URLSearchParams()
  if (params?.project_id) qs.set('project_id', params.project_id)
  if (params?.task_id) qs.set('task_id', params.task_id)
  if (params?.phase) qs.set('phase', params.phase)
  if (params?.agent) qs.set('agent', params.agent)
  if (params?.task_type) qs.set('task_type', params.task_type)
  const q = qs.toString()
  return apiFetch<TokenStats>(`/v1/observability/stats${q ? `?${q}` : ''}`)
}

export async function getTurns(params?: {
  project_id?: string
  task_id?: string
  limit?: number
}): Promise<TokenTurnsResponse> {
  const qs = new URLSearchParams()
  if (params?.project_id) qs.set('project_id', params.project_id)
  if (params?.task_id) qs.set('task_id', params.task_id)
  if (params?.limit) qs.set('limit', String(params.limit))
  const q = qs.toString()
  return apiFetch<TokenTurnsResponse>(`/v1/observability/turns${q ? `?${q}` : ''}`)
}

export async function getHierarchy(projectId?: string): Promise<TokenHierarchy> {
  const q = projectId ? `?project_id=${projectId}` : ''
  return apiFetch<TokenHierarchy>(`/v1/observability/hierarchy${q}`)
}

export async function queryLogs(params: LogQueryParams): Promise<PagedLogs> {
  return apiFetch<PagedLogs>('/v1/observability/logs/query', {
    method: 'POST',
    body: JSON.stringify(params),
  })
}

export async function getTaskLogs(
  taskId: string,
  limit = 200,
  minLevel = ''
): Promise<{ task_id: string; items: LogEntry[]; total: number }> {
  const qs = new URLSearchParams()
  qs.set('limit', String(limit))
  if (minLevel) qs.set('min_level', minLevel)
  return apiFetch(`/v1/observability/tasks/${taskId}/logs?${qs}`)
}
