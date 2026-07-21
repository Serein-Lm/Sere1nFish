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
  root_domain?: string
  search_terms?: string[]
  search_terms_by_channel?: Record<string, string[]>
  parent_target_id?: string
  parent_target_name?: string
  relation_type?: string
  relation_depth?: number
  ownership_percent?: number
  relation_source?: string
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
  linked_project_count: number
  last_document_at?: string
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

export function listProjectTargets(projectId: string) {
  return apiFetch<{ items: ProjectTargetSummary[]; total: number }>(
    `/v1/targets?project_id=${encodeURIComponent(projectId)}&compact=true`,
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
