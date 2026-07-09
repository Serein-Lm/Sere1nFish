import { apiFetch } from './http'

// ============ 类型定义 ============

export type PromptStatus = 'draft' | 'pending_review' | 'approved' | 'rejected' | 'archived'

export interface Prompt {
  prompt_id: string
  slug: string
  name: string
  category: string
  description: string
  content?: string
  system_prompt?: string
  user_prompt_template?: string
  variables?: string[]
  tags: string[]
  model_hint?: string
  temperature?: number
  max_tokens?: number
  status: PromptStatus
  version: number
  created_by: string
  reviewed_by?: string
  review_comment?: string
  created_at: string
  updated_at: string
  meta?: Record<string, unknown>
}

export interface PromptsListResponse {
  items: Prompt[]
  total: number
  page: number
  page_size: number
  pages: number
}

export interface PromptStatsResponse {
  [status: string]: number
}

export interface PromptCategory {
  category_id: string
  slug: string
  name: string
  description: string
  parent_id: string | null
  sort_order: number
  children?: PromptCategory[]
}

export interface PromptTag {
  tag_id: string
  name: string
  color: string
  description: string
}

export interface PromptCreateRequest {
  slug: string
  name: string
  category: string
  description: string
  content: string
  system_prompt?: string
  user_prompt_template?: string
  variables?: string[]
  tags?: string[]
  model_hint?: string
  temperature?: number
  max_tokens?: number
  meta?: Record<string, unknown>
}

export interface PromptUpdateRequest extends Partial<PromptCreateRequest> {}

export interface PromptListParams {
  category?: string
  tag?: string
  status?: PromptStatus
  search?: string
  page?: number
  page_size?: number
  sort_by?: string
  sort_order?: 'asc' | 'desc'
  include_content?: boolean
}

// ============ Prompts CRUD ============

export async function listPrompts(params?: PromptListParams): Promise<PromptsListResponse> {
  const qs = new URLSearchParams()
  if (params) {
    Object.entries(params).forEach(([k, v]) => {
      if (v !== undefined && v !== null && v !== '') qs.set(k, String(v))
    })
  }
  const query = qs.toString()
  const path = `/v1/prompts${query ? `?${query}` : ''}`
  return apiFetch<PromptsListResponse>(path, { method: 'GET' })
}

export async function getPromptStats(): Promise<PromptStatsResponse> {
  return apiFetch<PromptStatsResponse>('/v1/prompts/stats', { method: 'GET' })
}

export async function getPromptDetail(idOrSlug: string): Promise<Prompt> {
  return apiFetch<Prompt>(`/v1/prompts/detail/${encodeURIComponent(idOrSlug)}`, { method: 'GET' })
}

export async function createPrompt(data: PromptCreateRequest): Promise<Prompt> {
  return apiFetch<Prompt>('/v1/prompts', {
    method: 'POST',
    body: JSON.stringify(data),
  })
}

export async function updatePrompt(id: string, data: PromptUpdateRequest): Promise<Prompt> {
  return apiFetch<Prompt>(`/v1/prompts/detail/${encodeURIComponent(id)}`, {
    method: 'PUT',
    body: JSON.stringify(data),
  })
}

export async function deletePrompt(id: string): Promise<{ ok: boolean }> {
  return apiFetch<{ ok: boolean }>(`/v1/prompts/detail/${encodeURIComponent(id)}`, {
    method: 'DELETE',
  })
}

// ============ 审核流 ============

export async function submitPromptReview(id: string): Promise<Prompt> {
  return apiFetch<Prompt>(`/v1/prompts/detail/${encodeURIComponent(id)}/submit-review`, {
    method: 'POST',
  })
}

export async function listPendingPrompts(): Promise<Prompt[]> {
  const res = await apiFetch<{ items: Prompt[] }>('/v1/prompts/review/pending', { method: 'GET' })
  return res.items || []
}

export async function reviewPrompt(
  id: string,
  approved: boolean,
  comment?: string
): Promise<Prompt> {
  return apiFetch<Prompt>(`/v1/prompts/detail/${encodeURIComponent(id)}/review`, {
    method: 'POST',
    body: JSON.stringify({ approved, comment: comment || '' }),
  })
}

export async function archivePrompt(id: string): Promise<Prompt> {
  return apiFetch<Prompt>(`/v1/prompts/detail/${encodeURIComponent(id)}/archive`, {
    method: 'POST',
  })
}

// ============ 分类管理 ============

export async function listPromptCategories(): Promise<PromptCategory[]> {
  const res = await apiFetch<{ items: PromptCategory[] }>('/v1/prompts/categories', { method: 'GET' })
  return res.items || []
}

export async function getPromptCategoryTree(): Promise<PromptCategory[]> {
  const res = await apiFetch<{ tree: PromptCategory[] }>('/v1/prompts/categories/tree', { method: 'GET' })
  return res.tree || []
}

export async function createPromptCategory(data: {
  slug: string
  name: string
  description?: string
  parent_id?: string | null
  sort_order?: number
}): Promise<PromptCategory> {
  return apiFetch<PromptCategory>('/v1/prompts/categories', {
    method: 'POST',
    body: JSON.stringify(data),
  })
}

export async function updatePromptCategory(
  id: string,
  data: Partial<{ slug: string; name: string; description: string; parent_id: string | null; sort_order: number }>
): Promise<PromptCategory> {
  return apiFetch<PromptCategory>(`/v1/prompts/categories/${encodeURIComponent(id)}`, {
    method: 'PUT',
    body: JSON.stringify(data),
  })
}

export async function deletePromptCategory(id: string): Promise<{ ok: boolean }> {
  return apiFetch<{ ok: boolean }>(`/v1/prompts/categories/${encodeURIComponent(id)}`, {
    method: 'DELETE',
  })
}

// ============ 标签管理 ============

export async function listPromptTags(): Promise<PromptTag[]> {
  const res = await apiFetch<{ items: PromptTag[] }>('/v1/prompts/tags', { method: 'GET' })
  return res.items || []
}

export async function createPromptTag(data: {
  name: string
  color?: string
  description?: string
}): Promise<PromptTag> {
  return apiFetch<PromptTag>('/v1/prompts/tags', {
    method: 'POST',
    body: JSON.stringify(data),
  })
}

export async function updatePromptTag(
  tagId: string,
  data: Partial<{ name: string; color: string; description: string }>
): Promise<PromptTag> {
  return apiFetch<PromptTag>(`/v1/prompts/tags/${encodeURIComponent(tagId)}`, {
    method: 'PUT',
    body: JSON.stringify(data),
  })
}

export async function deletePromptTag(tagId: string): Promise<{ ok: boolean }> {
  return apiFetch<{ ok: boolean }>(`/v1/prompts/tags/${encodeURIComponent(tagId)}`, {
    method: 'DELETE',
  })
}

// ============ 状态元数据 ============

export const STATUS_META: Record<PromptStatus, { label: string; color: string }> = {
  draft: { label: '草稿', color: 'default' },
  pending_review: { label: '审核中', color: 'processing' },
  approved: { label: '已通过', color: 'success' },
  rejected: { label: '已拒绝', color: 'error' },
  archived: { label: '已归档', color: 'warning' },
}

export function promptStatusMeta(status: PromptStatus): { label: string; color: string } {
  return STATUS_META[status] ?? { label: status, color: 'default' }
}

export const PRESET_CATEGORIES = [
  { value: 'system-prompts', label: '系统提示词' },
  { value: 'task-templates', label: '任务模板' },
  { value: 'analysis', label: '分析' },
  { value: 'generation', label: '生成' },
  { value: 'review', label: '审查' },
  { value: 'extraction', label: '提取' },
  { value: 'conversation', label: '对话' },
  { value: 'custom', label: '自定义' },
]
