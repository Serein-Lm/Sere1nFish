import { apiFetch } from './http'

export interface IndustryCoverage {
  code: string; name: string; sector_code: string; person_count: number; minimum_personas: number
  organization_count: number; phone_count: number; source_count: number; gaps: string[]; complete: boolean
  job?: { job_id: string; status: string; stage: string; message?: string; started_at?: string; updated_at?: string; next_attempt_at?: string; errors?: string[]; research_task_id?: string }
}
export interface CoverageReport {
  standard: string; source_url: string; sectors: Array<{ code: string; name: string; person_count: number }>; items: IndustryCoverage[]
  summary: { person_count: number; sector_count: number; division_count: number; complete_count: number; unclassified_count: number; organization_count: number; phone_count: number; running: number; queued: number }
}
export interface OrganizationFact {
  fact_id: string; organization_name: string; industry_code: string; office_phone: string; website: string; address: string; excerpt: string
  source_url: string; source_document_id: string; source_document_version_id: string; captured_at: string; target_id: string
}
export const getPersonaCoverage = () => apiFetch<CoverageReport>('/persona-coverage')
export const startPersonaCoverage = (industryCodes: string[] = [], minimumPersonas = 4) => apiFetch<{ queued_industries: number; minimum_personas: number }>('/persona-coverage/start', { method: 'POST', body: JSON.stringify({ industry_codes: industryCodes, minimum_personas: minimumPersonas }) })
export const getIndustryOrganizations = (code = '', page = 1) => apiFetch<{ items: OrganizationFact[]; total: number }>(`/persona-coverage/organizations?industry_code=${encodeURIComponent(code)}&skip=${(page - 1) * 20}&limit=20`)
