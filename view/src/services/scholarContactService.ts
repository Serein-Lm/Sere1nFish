import { apiFetch } from './http'
import type { PaginatedResponse } from './projectService'

// ============================================
// 学者学术联系发现
//
// 按「单位 + 研究方向」收集匹配文章及文章绑定的公开通讯邮箱。
// 采集通过统一任务框架下发(task_type=scholar_contact)，本 service 负责查询与概览。
// ============================================

export interface ScholarContact {
  doc_id: string
  project_id: string
  email: string
  article_id: string
  source_key: string
  author_name?: string | null
  is_corresponding: boolean
  unit?: string | null
  direction?: string | null
  created_at?: string
  updated_at?: string
  // joined from scholar_articles / computed
  article_title?: string | null
  article_doi?: string | null
  article_pmcid?: string | null
  article_landing_page?: string | null
  article_year?: string | null
  email_kind?: 'personal' | 'institutional' | ''
  // 人物↔单位一致性验证
  unit_verified?: boolean
  evidence?: string
}

export interface ScholarArticle {
  doc_id: string
  project_id: string
  article_id: string
  title: string
  year?: string | null
  doi?: string | null
  pmcid?: string | null
  unit?: string | null
  direction?: string | null
  source_keys: string[]
  landing_page?: string | null
  unit_verified?: boolean
  match_evidence?: string
  created_at?: string
  updated_at?: string
}

export interface ScholarUnitSummary {
  unit: string
  contacts: number
  corresponding: number
}

/** 分页查询学者联系（邮箱 → 文章 → 来源） */
export async function listScholarContacts(
  projectId: string,
  params?: { page?: number; page_size?: number; unit?: string; only_corresponding?: boolean; only_verified?: boolean }
): Promise<PaginatedResponse<ScholarContact>> {
  return apiFetch(`/v1/projects/${encodeURIComponent(projectId)}/scholar-contacts`, {
    method: 'POST',
    body: JSON.stringify({
      project_id: projectId,
      page: params?.page ?? 1,
      page_size: params?.page_size ?? 20,
      unit: params?.unit ?? '',
      only_corresponding: params?.only_corresponding ?? false,
      only_verified: params?.only_verified ?? false,
    }),
  })
}

/** 分页查询已收集文章 */
export async function listScholarArticles(
  projectId: string,
  params?: { page?: number; page_size?: number; unit?: string; only_verified?: boolean }
): Promise<PaginatedResponse<ScholarArticle>> {
  return apiFetch(`/v1/projects/${encodeURIComponent(projectId)}/scholar-articles`, {
    method: 'POST',
    body: JSON.stringify({
      project_id: projectId,
      page: params?.page ?? 1,
      page_size: params?.page_size ?? 20,
      unit: params?.unit ?? '',
      only_verified: params?.only_verified ?? false,
    }),
  })
}

/** 按单位聚合已收集的联系/通讯计数 */
export async function listScholarUnits(
  projectId: string
): Promise<{ units: ScholarUnitSummary[]; total: number }> {
  return apiFetch(`/v1/projects/${encodeURIComponent(projectId)}/scholar-contacts/units`)
}
