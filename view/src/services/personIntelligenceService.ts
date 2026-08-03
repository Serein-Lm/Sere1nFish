import { apiFetch } from './http'

const BASE = '/v1/person-intelligence'

export interface IntelligenceSource {
  title?: string
  url: string
  summary?: string
  source_type?: string
  published_at?: string
}

export interface IntelligenceEvidence {
  evidence_id?: string
  dimension: string
  finding: string
  evidence_type: 'fact' | 'inference'
  confidence?: number
  source_urls?: string[]
}

export interface ContextSignal {
  signal_id?: string
  signal_type?: string
  title: string
  summary?: string
  relevance?: string
  observed_at?: string
  expires_at?: string
  source_urls?: string[]
  status?: 'active' | 'expired' | 'undated' | 'invalid'
}

export interface PublicContact {
  channel: string
  value: string
  context?: string
  source_url: string
}

export interface PersonaMatch {
  person_id: string
  name?: string
  rationale?: string
  score?: number
}

export interface IntelligenceCopywriting {
  copywriting_id?: string
  title?: string
  channel?: string
  content: string
  basis?: string
  scenario_ids?: string[]
  source_urls?: string[]
}

export interface EngagementScenario {
  scenario_id?: string
  title: string
  objective?: string
  rationale?: string
  timing?: string
  priority?: number
  source_urls?: string[]
  persona_ids?: string[]
}

export interface IntelligenceLineageNode {
  node_id: string
  node_type: string
  label: string
  url?: string
  artifact_id?: string
  [key: string]: unknown
}

export interface IntelligenceLineageEdge {
  edge_id: string
  source: string
  target: string
  relation: string
}

export interface IntelligenceLineage {
  nodes: IntelligenceLineageNode[]
  edges: IntelligenceLineageEdge[]
}

export interface PersonIntelligence {
  intel_id: string
  name: string
  aliases?: string[]
  organization: string
  position?: string
  department?: string
  location?: string
  summary?: string
  background?: string
  affiliations?: Array<Record<string, unknown>>
  career_history?: Array<Record<string, unknown>>
  research_areas?: string[]
  public_contacts?: PublicContact[]
  profile?: Record<string, unknown>
  sources?: IntelligenceSource[]
  evidence?: IntelligenceEvidence[]
  context_signals?: ContextSignal[]
  recommended_personas?: PersonaMatch[]
  scenarios?: EngagementScenario[]
  engagement_plan?: Record<string, unknown>
  sample_copywritings?: IntelligenceCopywriting[]
  artifact_ids?: string[]
  lineage?: IntelligenceLineage
  confidence?: number
  target_id?: string
  project_ids?: string[]
  task_ids?: string[]
  source_count?: number
  evidence_count?: number
  signal_count?: number
  active_signal_count?: number
  scenario_count?: number
  copywriting_count?: number
  artifact_count?: number
  profile_version?: number
  research_rounds?: number
  last_researched_at?: string
  created_at?: string
  updated_at?: string
}

export interface IntelligenceListParams {
  keyword?: string
  organization?: string
  target_id?: string
  project_id?: string
  min_confidence?: number
  sort?: 'updated_desc' | 'confidence_desc' | 'name_asc'
  skip?: number
  limit?: number
  summary_only?: boolean
}

export interface IntelligenceListResult {
  items: PersonIntelligence[]
  total: number
  skip: number
  limit: number
}

export function listPersonIntelligence(
  params: IntelligenceListParams = {},
): Promise<IntelligenceListResult> {
  const query = new URLSearchParams()
  if (params.keyword) query.set('keyword', params.keyword)
  if (params.organization) query.set('organization', params.organization)
  if (params.target_id) query.set('target_id', params.target_id)
  if (params.project_id) query.set('project_id', params.project_id)
  if (params.min_confidence != null) query.set('min_confidence', String(params.min_confidence))
  if (params.sort) query.set('sort', params.sort)
  if (params.summary_only != null) query.set('summary_only', String(params.summary_only))
  query.set('skip', String(params.skip ?? 0))
  query.set('limit', String(params.limit ?? 20))
  return apiFetch<IntelligenceListResult>(`${BASE}?${query.toString()}`)
}

export const getPersonIntelligence = (intelId: string) =>
  apiFetch<PersonIntelligence>(`${BASE}/${encodeURIComponent(intelId)}`)
