import { apiFetch } from './http'

// ============ 类型定义 ============

export type SkillStatus = 'draft' | 'pending_review' | 'approved' | 'rejected' | 'archived'

export interface Skill {
  skill_id: string
  slug: string
  name: string
  category: string
  description: string
  content_raw?: string
  tags: string[]
  triggers: string[]
  anti_triggers: string[]
  aliases: string[]
  requires: string[]
  related: string[]
  file_signals: string[]
  risk_signals: string[]
  priority: number
  status: SkillStatus
  version: number
  created_by: string
  reviewed_by?: string
  review_comment?: string
  created_at: string
  updated_at: string
  meta?: Record<string, unknown>
}

export interface SkillsListResponse {
  items: Skill[]
  total: number
  page: number
  page_size: number
  pages: number
}

export interface SkillStatsResponse {
  [status: string]: number
}

export interface SkillCategory {
  category_id: string
  id?: string
  slug: string
  name: string
  description: string
  parent_id: string | null
  sort_order: number
  children?: SkillCategory[]
}

export interface SkillTag {
  tag_id: string
  name: string
  color: string
  description: string
}

export interface SkillCreateRequest {
  slug: string
  name: string
  category: string
  description: string
  content_raw: string
  tags?: string[]
  triggers?: string[]
  anti_triggers?: string[]
  aliases?: string[]
  requires?: string[]
  related?: string[]
  file_signals?: string[]
  risk_signals?: string[]
  priority?: number
  meta?: Record<string, unknown>
}

export interface SkillUpdateRequest extends Partial<SkillCreateRequest> {}

export interface SkillListParams {
  category?: string
  tag?: string
  status?: SkillStatus
  search?: string
  page?: number
  page_size?: number
  sort_by?: string
  sort_order?: 'asc' | 'desc'
  include_content?: boolean
}

// ============ Skills CRUD ============

export async function listSkills(params?: SkillListParams): Promise<SkillsListResponse> {
  const qs = new URLSearchParams()
  if (params) {
    Object.entries(params).forEach(([k, v]) => {
      if (v !== undefined && v !== null && v !== '') qs.set(k, String(v))
    })
  }
  const query = qs.toString()
  const path = `/v1/skills${query ? `?${query}` : ''}`
  return apiFetch<SkillsListResponse>(path, { method: 'GET' })
}

export async function getSkillsGrouped(): Promise<Record<string, Skill[]>> {
  const res = await apiFetch<{ groups: Record<string, Skill[]> }>('/v1/skills/grouped', { method: 'GET' })
  return res.groups || {}
}

export async function getSkillsStats(): Promise<SkillStatsResponse> {
  const res = await apiFetch<{ status_counts: SkillStatsResponse }>('/v1/skills/stats', { method: 'GET' })
  return res.status_counts || {}
}

export async function getSkillDetail(idOrSlug: string): Promise<Skill> {
  return apiFetch<Skill>(`/v1/skills/detail/${encodeURIComponent(idOrSlug)}`, { method: 'GET' })
}

export async function createSkill(data: SkillCreateRequest): Promise<Skill> {
  return apiFetch<Skill>('/v1/skills', {
    method: 'POST',
    body: JSON.stringify(data),
  })
}

export async function updateSkill(id: string, data: SkillUpdateRequest): Promise<Skill> {
  return apiFetch<Skill>(`/v1/skills/detail/${encodeURIComponent(id)}`, {
    method: 'PUT',
    body: JSON.stringify(data),
  })
}

export async function deleteSkill(id: string): Promise<{ ok: boolean }> {
  return apiFetch<{ ok: boolean }>(`/v1/skills/detail/${encodeURIComponent(id)}`, {
    method: 'DELETE',
  })
}

// ============ 审核流 ============

export async function submitSkillReview(id: string): Promise<Skill> {
  return apiFetch<Skill>(`/v1/skills/detail/${encodeURIComponent(id)}/submit-review`, {
    method: 'POST',
  })
}

export async function listPendingSkills(): Promise<Skill[]> {
  const res = await apiFetch<{ items: Skill[] }>('/v1/skills/review/pending', { method: 'GET' })
  return res.items || []
}

export async function reviewSkill(
  id: string,
  approved: boolean,
  comment?: string
): Promise<Skill> {
  return apiFetch<Skill>(`/v1/skills/detail/${encodeURIComponent(id)}/review`, {
    method: 'POST',
    body: JSON.stringify({ approved, comment: comment || '' }),
  })
}

export async function archiveSkill(id: string): Promise<Skill> {
  return apiFetch<Skill>(`/v1/skills/detail/${encodeURIComponent(id)}/archive`, {
    method: 'POST',
  })
}

// ============ 分类管理 ============

export async function listSkillCategories(): Promise<SkillCategory[]> {
  const res = await apiFetch<{ items: SkillCategory[] }>('/v1/skills/categories', { method: 'GET' })
  return res.items || []
}

export async function getSkillCategoryTree(): Promise<SkillCategory[]> {
  const res = await apiFetch<{ tree: SkillCategory[] }>('/v1/skills/categories/tree', { method: 'GET' })
  return res.tree || []
}

export async function createSkillCategory(data: {
  slug: string
  name: string
  description?: string
  parent_id?: string | null
  sort_order?: number
}): Promise<SkillCategory> {
  return apiFetch<SkillCategory>('/v1/skills/categories', {
    method: 'POST',
    body: JSON.stringify(data),
  })
}

export async function updateSkillCategory(
  id: string,
  data: Partial<{ slug: string; name: string; description: string; parent_id: string | null; sort_order: number }>
): Promise<SkillCategory> {
  return apiFetch<SkillCategory>(`/v1/skills/categories/${encodeURIComponent(id)}`, {
    method: 'PUT',
    body: JSON.stringify(data),
  })
}

export async function deleteSkillCategory(id: string): Promise<{ ok: boolean }> {
  return apiFetch<{ ok: boolean }>(`/v1/skills/categories/${encodeURIComponent(id)}`, {
    method: 'DELETE',
  })
}

// ============ 标签管理 ============

export async function listSkillTags(): Promise<SkillTag[]> {
  const res = await apiFetch<{ items: SkillTag[] }>('/v1/skills/tags', { method: 'GET' })
  return res.items || []
}

export async function createSkillTag(data: {
  name: string
  color?: string
  description?: string
}): Promise<SkillTag> {
  return apiFetch<SkillTag>('/v1/skills/tags', {
    method: 'POST',
    body: JSON.stringify(data),
  })
}

export async function updateSkillTag(
  tagId: string,
  data: Partial<{ name: string; color: string; description: string }>
): Promise<SkillTag> {
  return apiFetch<SkillTag>(`/v1/skills/tags/${encodeURIComponent(tagId)}`, {
    method: 'PUT',
    body: JSON.stringify(data),
  })
}

export async function deleteSkillTag(tagId: string): Promise<{ ok: boolean }> {
  return apiFetch<{ ok: boolean }>(`/v1/skills/tags/${encodeURIComponent(tagId)}`, {
    method: 'DELETE',
  })
}

// ============ 状态元数据 ============

export const STATUS_META: Record<SkillStatus, { label: string; color: string }> = {
  draft: { label: '草稿', color: 'default' },
  pending_review: { label: '审核中', color: 'processing' },
  approved: { label: '已通过', color: 'success' },
  rejected: { label: '已拒绝', color: 'error' },
  archived: { label: '已归档', color: 'warning' },
}

export function statusMeta(status: SkillStatus): { label: string; color: string } {
  return STATUS_META[status] ?? { label: status, color: 'default' }
}
