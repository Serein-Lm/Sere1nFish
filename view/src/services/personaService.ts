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

export interface PersonaResearchEvidence {
  dimension: string
  finding: string
  applicability: string
  source_urls: string[]
}

export interface Person {
  person_id: string
  project_ids?: string[]
  name: string
  is_fictional?: boolean
  generation_brief?: string
  generation_key?: string
  gender?: string
  age?: number
  age_range?: string
  region_type?: string
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
  organization_context?: string
  career_stage?: string
  career_path?: string
  life_stage?: string
  work_context?: string
  work_rhythm?: string
  decision_style?: string
  communication_style?: string
  collaboration_style?: string
  technology_attitude?: string
  learning_style?: string
  stress_response?: string
  contact?: PersonaContact
  background?: string
  personality?: string
  summary?: string
  interests?: string[]
  information_preferences?: string[]
  digital_habits?: string[]
  motivations?: string[]
  goals?: string[]
  pain_points?: string[]
  values?: string[]
  behavior_patterns?: string[]
  content_preferences?: string[]
  purchase_considerations?: string[]
  tags?: string[]
  risk_signals?: string[]
  source_urls?: string[]
  evidence?: string[]
  research_evidence?: PersonaResearchEvidence[]
  sources?: PersonSource[]
  confidence?: number
  profile_version?: number
  research_rounds?: number
  last_researched_at?: string
  created_at?: string
  updated_at?: string
}

export interface PersonListResult {
  items: Person[]
  total: number
  limit: number
  skip: number
}

export type PersonaResearchTaskStatus = 'queued' | 'running' | 'completed' | 'failed' | 'cancelled'

export interface PersonaResearchTask {
  task_id: string
  task_type: 'generate' | 'enrich'
  status: PersonaResearchTaskStatus
  stage: string
  message: string
  requested_count: number
  completed_count: number
  failed_count: number
  person_id?: string
  project_id?: string
  result_person_ids: string[]
  error?: string
  details?: Record<string, unknown>
  created_at?: string
  updated_at?: string
  started_at?: string
  finished_at?: string
}

export interface PersonSearchParams {
  project_id?: string
  keyword?: string
  company?: string
  industry?: string
  position?: string
  personality?: string
  age_min?: number
  age_max?: number
  tags?: string[]
  min_confidence?: number
  sort?: 'confidence_desc' | 'time_desc'
  limit?: number
  skip?: number
  summary_only?: boolean
}

export async function listPersons(params: PersonSearchParams = {}): Promise<PersonListResult> {
  const q = new URLSearchParams()
  if (params.project_id) q.set('project_id', params.project_id)
  if (params.keyword) q.set('keyword', params.keyword)
  if (params.company) q.set('company', params.company)
  if (params.industry) q.set('industry', params.industry)
  if (params.position) q.set('position', params.position)
  if (params.personality) q.set('personality', params.personality)
  if (params.age_min != null) q.set('age_min', String(params.age_min))
  if (params.age_max != null) q.set('age_max', String(params.age_max))
  if (params.tags?.length) q.set('tags', params.tags.join(','))
  if (params.min_confidence) q.set('min_confidence', String(params.min_confidence))
  if (params.sort) q.set('sort', params.sort)
  if (params.summary_only != null) q.set('summary_only', String(params.summary_only))
  q.set('limit', String(params.limit ?? 20))
  q.set('skip', String(params.skip ?? 0))
  return apiFetch<PersonListResult>(`${BASE}?${q.toString()}`)
}

export const getPerson = (personId: string) =>
  apiFetch<Person>(`${BASE}/${encodeURIComponent(personId)}`)

export interface CollectPersonaBody {
  background: string
  count?: number
  industries?: string[]
  age_ranges?: string[]
  personalities?: string[]
  name?: string
  company?: string
  position?: string
  extra?: string
  project_id?: string
}

export const collectPersona = (body: CollectPersonaBody) =>
  apiFetch<{ task_id: string; status: string; name: string; count: number; is_fictional: boolean }>(`${BASE}/generate`, {
    method: 'POST',
    body: JSON.stringify(body),
  })

export const enrichPerson = (personId: string, extra = '', projectId = '') =>
  apiFetch<{ task_id: string; status: string; person_id: string; profile_version: number }>(
    `${BASE}/${encodeURIComponent(personId)}/enrich`,
    {
      method: 'POST',
      body: JSON.stringify({ extra, project_id: projectId }),
    },
  )

export const getPersonaResearchTask = (taskId: string) =>
  apiFetch<PersonaResearchTask>(`${BASE}/tasks/${encodeURIComponent(taskId)}`)

export async function waitForPersonaResearchTask(
  taskId: string,
  onUpdate?: (task: PersonaResearchTask) => void,
  timeoutMs = 30 * 60 * 1000,
): Promise<PersonaResearchTask> {
  const startedAt = Date.now()
  while (Date.now() - startedAt < timeoutMs) {
    const task = await getPersonaResearchTask(taskId)
    onUpdate?.(task)
    if (task.status === 'completed' || task.status === 'failed' || task.status === 'cancelled') {
      return task
    }
    await new Promise((resolve) => window.setTimeout(resolve, 2500))
  }
  throw new Error('人设研究任务等待超时，请稍后刷新查看')
}

export const upsertPerson = (personId: string, profile: Partial<Person>, projectId = '') =>
  apiFetch<Person>(`${BASE}/${encodeURIComponent(personId)}`, {
    method: 'PUT',
    body: JSON.stringify({ profile, project_id: projectId }),
  })

export const deletePerson = (personId: string) =>
  apiFetch<{ ok: boolean; person_id: string }>(`${BASE}/${encodeURIComponent(personId)}`, {
    method: 'DELETE',
  })
