import { apiFetch } from './http'

// ============ 通用分页 ============

export interface PaginatedRequest {
  page?: number
  page_size?: number
}

export interface PaginatedResponse<T> {
  items: T[]
  total: number
  page: number
  page_size: number
}

// ============ 项目 ============

export interface Project {
  id: string
  name: string
  description: string | null
  created_at: string
  updated_at: string
}

export interface CreateProjectRequest {
  name: string
  description?: string
}

export interface UpdateProjectRequest {
  name?: string
  description?: string
}

// ============ Web Tagging ============

export interface WebTaggingIntro {
  url: string | null
  final_url: string | null
  domain: string | null
  site_name: string | null
  entity_name: string | null
  summary: string | null
}

export interface WebTaggingFinding {
  finding_id: string
  task_id?: string
  project_id?: string
  url?: string
  type: string
  scope: string
  channel: string
  role: string
  subtype: string | null
  label: string | null
  value: string | null
  context: string
  source_url: string
  evidence: string
  attention_score: number
  attention_reason: string
}

export interface WebTaggingData {
  intro: WebTaggingIntro
  has_findings: boolean
  no_findings_reason: string | null
  findings: WebTaggingFinding[]
}

export interface WebTaggingRecord {
  id: string
  project_id: string
  url: string
  task_id?: string
  created_at: string
  data: WebTaggingData
}

// ============ 看板数据类型 ============

export interface DashboardData {
  findings: {
    total: number
    by_source: Record<string, number>
    by_type: Record<string, number>
    score_distribution: { high: number; medium: number; low: number }
  }
  tasks: {
    total: number
    by_status: Record<string, number>
  }
  data_counts: {
    xhs_notes: number
    xhs_profiles: number
    web_tagging: number
    douyin_search: number
    douyin_tagged: number
    douyin_profiles: number
    mobile_profiles: number
    mobile_profile_observations: number
    copywritings: number
  }
  top_findings: Array<{
    finding_id: string
    source: string
    type: string
    label: string
    attention_score: number
  }>
  safe_count: number
  token_usage: { total_tokens: number; total_cost: number }
}

export interface TimelineEvent {
  type: 'task' | 'finding' | 'xhs_note' | 'xhs_profile'
  id: string
  label: string
  source?: string
  status?: string
  score?: number
  time: string
}

export interface ScoreDistributionBin {
  min: number
  count: number
}

export interface SourceBreakdownItem {
  source: string
  count: number
  avg_score: number
  max_score: number
  min_score: number
}

export interface TypeBreakdownItem {
  type: string
  count: number
  avg_score: number
  max_score: number
}

export interface HighValueTarget {
  finding_id: string
  source: string
  type: string
  label: string
  attention_score: number
  has_copywriting: boolean
  has_profile: boolean
}

export interface CopywritingCoverage {
  total_findings: number
  total_copywritings: number
  coverage_rate: number
  high_score: {
    total: number
    covered: number
    uncovered: number
    coverage_rate: number
  }
}

// ============ 项目 CRUD ============

export async function listProjects(params?: PaginatedRequest): Promise<PaginatedResponse<Project>> {
  return apiFetch<PaginatedResponse<Project>>('/v1/projects/list', {
    method: 'POST',
    body: JSON.stringify({ page: params?.page ?? 1, page_size: params?.page_size ?? 50 }),
  })
}

export async function getProject(projectId: string): Promise<Project> {
  return apiFetch<Project>(`/v1/projects/${encodeURIComponent(projectId)}`, { method: 'GET' })
}

export async function createProject(body: CreateProjectRequest): Promise<Project> {
  return apiFetch<Project>('/v1/projects', { method: 'POST', body: JSON.stringify(body) })
}

export async function updateProject(projectId: string, body: UpdateProjectRequest): Promise<Project> {
  return apiFetch<Project>(`/v1/projects/${encodeURIComponent(projectId)}`, {
    method: 'PATCH',
    body: JSON.stringify(body),
  })
}

export async function deleteProject(projectId: string): Promise<{ ok: boolean }> {
  return apiFetch<{ ok: boolean }>(`/v1/projects/${encodeURIComponent(projectId)}`, { method: 'DELETE' })
}

// ============ Web Tagging 操作 ============

export async function createWebTagging(body: {
  project_id: string
  url: string
}): Promise<WebTaggingRecord> {
  return apiFetch<WebTaggingRecord>('/v1/projects/web-tagging', {
    method: 'POST',
    body: JSON.stringify(body),
  })
}

export async function createCompanyWebTagging(body: {
  project_id: string
  company_name: string
}): Promise<WebTaggingRecord> {
  return apiFetch<WebTaggingRecord>('/v1/projects/company/web-tagging', {
    method: 'POST',
    body: JSON.stringify(body),
  })
}

// ============ 项目原始数据（POST 分页） ============

export async function listProjectWebTaggingRecords(
  projectId: string,
  params?: PaginatedRequest
): Promise<PaginatedResponse<WebTaggingRecord>> {
  return apiFetch<PaginatedResponse<WebTaggingRecord>>(
    `/v1/projects/${encodeURIComponent(projectId)}/web-tagging`,
    { method: 'POST', body: JSON.stringify({ project_id: projectId, page: params?.page ?? 1, page_size: params?.page_size ?? 50 }) }
  )
}

// ============ 看板 + 聚合 API ============

export async function getProjectDashboard(projectId: string): Promise<DashboardData> {
  return apiFetch<DashboardData>(`/v1/projects/${encodeURIComponent(projectId)}/dashboard`, { method: 'GET' })
}

export async function getProjectTimeline(projectId: string, limit?: number): Promise<{ events: TimelineEvent[] }> {
  const qs = limit ? `?limit=${limit}` : ''
  return apiFetch<{ events: TimelineEvent[] }>(`/v1/projects/${encodeURIComponent(projectId)}/timeline${qs}`, { method: 'GET' })
}

export async function getProjectScoreDistribution(projectId: string, source?: string): Promise<{ bins: ScoreDistributionBin[]; source: string }> {
  const qs = source ? `?source=${source}` : ''
  return apiFetch(`/v1/projects/${encodeURIComponent(projectId)}/score-distribution${qs}`, { method: 'GET' })
}

export async function getProjectSourceBreakdown(projectId: string): Promise<{ sources: SourceBreakdownItem[] }> {
  return apiFetch(`/v1/projects/${encodeURIComponent(projectId)}/source-breakdown`, { method: 'GET' })
}

export async function getProjectTypeBreakdown(projectId: string, source?: string): Promise<{ types: TypeBreakdownItem[]; source: string }> {
  const qs = source ? `?source=${source}` : ''
  return apiFetch(`/v1/projects/${encodeURIComponent(projectId)}/type-breakdown${qs}`, { method: 'GET' })
}

export async function getProjectHighValueTargets(projectId: string, params?: { min_score?: number; limit?: number }): Promise<{ items: HighValueTarget[]; total: number; min_score: number }> {
  const qs = new URLSearchParams()
  if (params?.min_score !== undefined) qs.set('min_score', String(params.min_score))
  if (params?.limit !== undefined) qs.set('limit', String(params.limit))
  const query = qs.toString()
  return apiFetch(`/v1/projects/${encodeURIComponent(projectId)}/high-value-targets${query ? `?${query}` : ''}`, { method: 'GET' })
}

export async function getProjectCopywritingCoverage(projectId: string): Promise<CopywritingCoverage> {
  return apiFetch(`/v1/projects/${encodeURIComponent(projectId)}/copywriting-coverage`, { method: 'GET' })
}
