import { apiFetch } from './http'

export interface BiddingAttachment {
  index: number
  source_url: string
  label?: string
  status: 'ready' | 'error'
  filename?: string
  storage_object_id?: string
  url?: string
  content_type?: string
  size?: number
  text_length?: number
  text_preview?: string
  error?: string
}

export interface BiddingRecord {
  record_id: string
  provider: string
  title: string
  announcement_type?: string
  stage?: string
  published_on?: string
  province?: string
  purchaser?: string
  agency?: string
  amount?: string
  winner?: string
  enterprise_identity?: string
  detail_url?: string
  provider_url?: string
  content_length?: number
  content_preview?: string
  detail_text_preview?: string
  provider_payload_object_id?: string
  provider_payload_url?: string
  raw_content_object_id?: string
  raw_content_url?: string
  detail_html_object_id?: string
  detail_html_url?: string
  attachment_urls?: string[]
  attachments?: BiddingAttachment[]
  archive_errors?: string[]
  query_names?: string[]
  target_ids?: string[]
  contacts: Array<{
    finding_id?: string
    channel?: string
    value: string
    label?: string
    party_name?: string
    party_role?: string
    role?: string
    context?: string
    evidence?: string
    attention_score?: number
  }>
  contact_count: number
  overview?: string
  original_url?: string
  max_contact_score?: number
  updated_at?: string
}

export interface BiddingPage {
  items: BiddingRecord[]
  total: number
  page: number
  page_size: number
}

export function listProjectBiddingRecords(
  projectId: string,
  params?: { page?: number; page_size?: number; target_id?: string },
): Promise<BiddingPage> {
  const query = new URLSearchParams({
    page: String(params?.page ?? 1),
    page_size: String(params?.page_size ?? 20),
  })
  if (params?.target_id) query.set('target_id', params.target_id)
  return apiFetch<BiddingPage>(
    `/v1/projects/${encodeURIComponent(projectId)}/bidding-records?${query.toString()}`,
    { method: 'GET' },
  )
}
