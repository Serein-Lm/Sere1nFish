import { apiFetch } from './http'

// ============================================
// 人设库（persons）— 全局人物实体，默认不绑定项目
// ============================================

const BASE = '/v1/persons'

export interface PersonaEducation {
  school?: string
  degree?: string
  major?: string
  graduation_year?: string
}

export interface PersonaContact {
  phone?: string
  email?: string
  wechat?: string
  other_social?: string[]
}

export interface PersonSource {
  source?: string
  ref_id?: string
  finding_id?: string
  task_id?: string
  project_id?: string
  collected_at?: string
}

export interface Person {
  person_id: string
  project_ids?: string[]
  name: string
  gender?: string
  aliases?: string[]
  company?: string
  company_root_domain?: string
  company_meta_id?: string
  industry?: string
  position?: string
  position_level?: string
  department?: string
  work_years?: string
  education?: PersonaEducation
  location?: string
  contact?: PersonaContact
  background?: string
  personality?: string
  summary?: string
  interests?: string[]
  tags?: string[]
  risk_signals?: string[]
  sources?: PersonSource[]
  confidence?: number
  created_at?: string
  updated_at?: string
}

export interface PersonListResult {
  items: Person[]
  total: number
  limit: number
  skip: number
}

export interface PersonSearchParams {
  project_id?: string
  keyword?: string
  company?: string
  industry?: string
  position?: string
  tags?: string[]
  min_confidence?: number
  sort?: 'confidence_desc' | 'time_desc'
  limit?: number
  skip?: number
}

export async function listPersons(params: PersonSearchParams = {}): Promise<PersonListResult> {
  const q = new URLSearchParams()
  if (params.project_id) q.set('project_id', params.project_id)
  if (params.keyword) q.set('keyword', params.keyword)
  if (params.company) q.set('company', params.company)
  if (params.industry) q.set('industry', params.industry)
  if (params.position) q.set('position', params.position)
  if (params.tags?.length) q.set('tags', params.tags.join(','))
  if (params.min_confidence) q.set('min_confidence', String(params.min_confidence))
  if (params.sort) q.set('sort', params.sort)
  q.set('limit', String(params.limit ?? 20))
  q.set('skip', String(params.skip ?? 0))
  return apiFetch<PersonListResult>(`${BASE}?${q.toString()}`)
}

export const getPerson = (personId: string) =>
  apiFetch<Person>(`${BASE}/${encodeURIComponent(personId)}`)

export interface CollectPersonaBody {
  name: string
  company?: string
  position?: string
  extra?: string
  project_id?: string
}

export const collectPersona = (body: CollectPersonaBody) =>
  apiFetch<{ task_id: string; status: string; name: string }>(`${BASE}/collect`, {
    method: 'POST',
    body: JSON.stringify(body),
  })

export const upsertPerson = (personId: string, profile: Partial<Person>, projectId = '') =>
  apiFetch<Person>(`${BASE}/${encodeURIComponent(personId)}`, {
    method: 'PUT',
    body: JSON.stringify({ profile, project_id: projectId }),
  })

export const deletePerson = (personId: string) =>
  apiFetch<{ ok: boolean; person_id: string }>(`${BASE}/${encodeURIComponent(personId)}`, {
    method: 'DELETE',
  })
