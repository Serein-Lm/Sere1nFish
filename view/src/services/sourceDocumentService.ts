import { apiFetch, fetchBlobWithAuth } from './http'

export interface SourceContact {
  channel: string
  value: string
  label?: string
  context?: string
  contexts?: string[]
  source?: string
  sources?: string[]
  image_index?: number
}

export interface SourceImageAnalysis {
  index: number
  description?: string
  visible_text?: string
  contacts?: SourceContact[]
}

export interface SourceImage {
  index: number
  source_url?: string
  storage_object_id?: string
  url?: string
  content_type?: string
  width?: number
  height?: number
  size?: number
  analysis?: SourceImageAnalysis
}

export interface SourceScreenshot {
  index: number
  source_url?: string
  storage_object_id?: string
  url: string
  content_type?: string
  width?: number
  height?: number
  size?: number
}

export interface SourceDocumentVersion {
  version_id: string
  content_hash: string
  status: string
  identity?: {
    title?: string
    account?: string
    publish_time?: string
    canonical_url?: string
  }
  content?: {
    summary?: string
    text?: string
    text_length?: number
  }
  contacts?: SourceContact[]
  analysis?: {
    fields?: Record<string, unknown>
    score?: number
    subject_match?: number
    score_reason?: string
    analysis_model?: string
  }
  images?: SourceImage[]
  screenshots?: SourceScreenshot[]
  artifacts?: {
    raw_html_url?: string
    rendered_html_url?: string
    structured_url?: string
  }
  captured_at?: string
}

export interface SourceDocumentDetail {
  document_id: string
  canonical_url: string
  source_type: string
  title?: string
  account?: string
  publish_time?: string
  latest_version_id?: string
  target_ids?: string[]
  version?: SourceDocumentVersion | null
  links?: Array<{
    link_id: string
    project_id: string
    target_id?: string
    target_name?: string
    latest_analysis?: {
      target_contacts?: SourceContact[]
      target_contact_values?: string[]
      contact_policy_version?: number
      review_decision?: string
      subject_match?: number
      score_reason?: string
    }
    keywords?: string[]
    first_seen_at?: string
    last_seen_at?: string
  }>
}

export interface ProjectTargetSummary {
  project_target_id: string
  project_id: string
  target_id: string
  target_type: string
  target_name: string
  display_name?: string
  short_names?: string[]
  scan_aliases?: string[]
  scan_profile_version?: number
  scan_profile_fingerprint?: string
  scan_profile_updated_at?: string
  scan_coverage?: Record<string, {
    status?: 'running' | 'completed' | 'partial' | 'error' | 'skipped'
    task_id?: string
    profile_fingerprint?: string
    updated_at?: string
    completed_at?: string
  }>
  root_domain?: string
  search_terms?: string[]
  search_terms_by_channel?: Record<string, string[]>
  root_target_id?: string
  root_target_name?: string
  parent_target_id?: string
  parent_target_name?: string
  relation_type?: string
  relation_depth?: number
  ownership_percent?: number
  relation_source?: string
  lineage_target_ids?: string[]
  lineage_target_names?: string[]
  batch_tags?: string[]
  batch_priority_rank?: number | null
  batch_priority_label?: string
  is_expanded_target?: boolean
  relation?: Record<string, unknown>
  task_def_ids?: string[]
  document_count: number
  project_document_count: number
  record_count: number
  asset_count: number
  alive_asset_count: number
  finding_count: number
  high_score_finding_count: number
  high_score_by_source?: Partial<Record<
    'website' | 'xiaohongshu' | 'wechat' | 'bidding' | 'scholars' | 'other',
    number
  >>
  website_count: number
  xhs_count: number
  wechat_count: number
  bidding_count: number
  scholar_contact_count: number
  latest_task_status?: string
  collection_complete?: boolean
  coverage_completed_count?: number
  coverage_required_count?: number
  coverage_completed_channels?: string[]
  coverage_missing_channels?: string[]
  linked_project_count: number
  last_document_at?: string
  search_match?: boolean
  search_score?: number
  child_count?: number
  descendant_count?: number
}

export interface ProjectTargetOption {
  project_target_id: string
  target_id: string
  target_name: string
  root_domain?: string
  root_target_id?: string
  root_target_name?: string
  parent_target_id?: string
  parent_target_name?: string
  relation_depth?: number
  batch_tags?: string[]
}

export interface ProjectTargetBatchOption {
  batch_tag: string
  target_count: number
  root_count: number
}

export interface ProjectTargetPageResponse {
  items: ProjectTargetSummary[]
  total: number
  root_total: number
  project_total: number
  all_root_total: number
  all_project_total: number
  matched_total: number
  page: number
  page_size: number
  matched_target_ids: string[]
  expanded_project_target_ids: string[]
}

export interface ProjectTargetBranchResponse {
  items: ProjectTargetSummary[]
  total: number
  root_target_id: string
}

export interface TargetResearchTaskResponse {
  task_id: string
  target_id: string
  task_type?: 'target_research'
  status: string
  deduplicated?: boolean
}

export interface TargetResearchBatchResponse {
  batch_id: string
  task_type: 'target_research'
  task_count: number
  task_ids: string[]
  linked_target_count: number
  targets: Array<{ target_id: string; target_name: string }>
  deduplicated: Array<{
    target_id: string
    target_name: string
    task_id?: string
    reason: string
  }>
  concurrency: number
  status: 'pending' | 'deduplicated'
}

export interface TargetResearchResult {
  research_id: string
  target_id: string
  project_id: string
  canonical_name: string
  summary: string
  industry?: string
  organization_type?: string
  expanded_target_count: number
  expanded_targets?: Array<Record<string, unknown>>
  researched_at?: string
}

export function getSourceDocument(documentId: string, projectId?: string, versionId?: string) {
  const query = new URLSearchParams()
  if (projectId) query.set('project_id', projectId)
  if (versionId) query.set('version_id', versionId)
  const suffix = query.size ? `?${query.toString()}` : ''
  return apiFetch<SourceDocumentDetail>(
    `/v1/source-documents/${encodeURIComponent(documentId)}${suffix}`,
  )
}

export function listProjectTargets(
  projectId: string,
  options: { page?: number; page_size?: number; q?: string; batch_tag?: string } = {},
) {
  const query = new URLSearchParams({
    project_id: projectId,
    compact: 'true',
    page: String(options.page ?? 1),
    page_size: String(options.page_size ?? 10),
  })
  if (options.q?.trim()) query.set('q', options.q.trim())
  if (options.batch_tag?.trim()) query.set('batch_tag', options.batch_tag.trim())
  return apiFetch<ProjectTargetPageResponse>(`/v1/targets?${query.toString()}`)
}

export function listProjectTargetOptions(projectId: string) {
  return apiFetch<{ items: ProjectTargetOption[]; total: number }>(
    `/v1/targets/options?project_id=${encodeURIComponent(projectId)}`,
  )
}

export function listProjectTargetBranch(projectId: string, targetId: string) {
  return apiFetch<ProjectTargetBranchResponse>(
    `/v1/targets/${encodeURIComponent(targetId)}/branch?project_id=${encodeURIComponent(projectId)}`,
  )
}

export function listProjectTargetBatches(projectId: string) {
  return apiFetch<{ items: ProjectTargetBatchOption[]; total: number }>(
    `/v1/targets/batches?project_id=${encodeURIComponent(projectId)}`,
  )
}

export function getProjectTargetSummary(projectId: string, targetId: string) {
  return apiFetch<{ item: ProjectTargetSummary }>(
    `/v1/targets/${encodeURIComponent(targetId)}/summary?project_id=${encodeURIComponent(projectId)}`,
  )
}

export function assignProjectTargetBatches(
  projectId: string,
  targetIds: string[],
  batchTags: string[],
  options: {
    operation?: 'add' | 'remove' | 'replace'
    include_descendants?: boolean
  } = {},
) {
  return apiFetch<{
    matched_count: number
    modified_count: number
    target_count: number
    target_ids: string[]
    batch_tags: string[]
  }>('/v1/targets/batches/assign', {
    method: 'POST',
    body: JSON.stringify({
      project_id: projectId,
      target_ids: targetIds,
      batch_tags: batchTags,
      operation: options.operation ?? 'add',
      include_descendants: options.include_descendants ?? true,
    }),
  })
}

export function createTargetResearch(
  projectId: string,
  targetId: string,
  options?: {
    scan_discovered_targets?: boolean
    rescan_root?: boolean
    max_related_targets?: number
    force_refresh?: boolean
  },
) {
  return apiFetch<TargetResearchTaskResponse>(
    `/v1/targets/${encodeURIComponent(targetId)}/research`,
    {
      method: 'POST',
      body: JSON.stringify({
        project_id: projectId,
        scan_discovered_targets: options?.scan_discovered_targets ?? true,
        rescan_root: options?.rescan_root ?? true,
        max_related_targets: options?.max_related_targets ?? 8,
        force_refresh: options?.force_refresh ?? true,
      }),
    },
  )
}

export function createTargetResearchBatch(
  projectId: string,
  targetNames: string[],
  options?: {
    concurrency?: number
    scan_discovered_targets?: boolean
    rescan_root?: boolean
    max_related_targets?: number
    force_refresh?: boolean
    scan_params?: Record<string, unknown>
  },
) {
  return apiFetch<TargetResearchBatchResponse>('/v1/targets/research-batch', {
    method: 'POST',
    body: JSON.stringify({
      project_id: projectId,
      target_names: targetNames,
      concurrency: options?.concurrency ?? 4,
      scan_discovered_targets: options?.scan_discovered_targets ?? true,
      rescan_root: options?.rescan_root ?? true,
      max_related_targets: options?.max_related_targets ?? 4,
      force_refresh: options?.force_refresh ?? true,
      scan_params: options?.scan_params ?? {},
    }),
  })
}

export function getTargetResearch(projectId: string, targetId: string) {
  return apiFetch<{ item: TargetResearchResult | null }>(
    `/v1/targets/${encodeURIComponent(targetId)}/research?project_id=${encodeURIComponent(projectId)}`,
  )
}

export async function openAuthenticatedArtifact(path: string): Promise<void> {
  const target = window.open('', '_blank')
  if (target) target.opener = null
  try {
    const url = new URL(path, window.location.origin)
    if (url.origin !== window.location.origin) {
      throw new Error('产物地址必须使用本站鉴权接口')
    }
    url.searchParams.set('proxy', 'true')
    const blob = await fetchBlobWithAuth(`${url.pathname}${url.search}`)
    const objectUrl = URL.createObjectURL(blob)
    if (target) target.location.href = objectUrl
    else window.open(objectUrl, '_blank', 'noopener,noreferrer')
    window.setTimeout(() => URL.revokeObjectURL(objectUrl), 60_000)
  } catch (error) {
    target?.close()
    throw error
  }
}
