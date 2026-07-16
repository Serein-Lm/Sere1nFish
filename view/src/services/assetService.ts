import { apiFetch } from './http'

export interface AssetProbe {
  is_alive?: boolean
  status_code?: number | null
  title?: string | null
  content_length?: number | null
  response_time?: number | null
  error?: string | null
}

export interface ProjectAsset {
  asset_id: string
  project_id: string
  target_id?: string
  target_ids?: string[]
  root_domain?: string
  host: string
  ip?: string
  port?: string
  protocol?: string
  domain?: string
  title?: string
  link?: string
  canonical_url?: string
  fingerprints?: string[]
  sources?: string[]
  is_alive?: boolean | null
  probe?: AssetProbe
  latest_task_id?: string
  updated_at: string
}

export function listProjectAssets(
  projectId: string,
  params: { target_id?: string; root_domain?: string; limit?: number } = {},
) {
  const query = new URLSearchParams()
  if (params.target_id) query.set('target_id', params.target_id)
  if (params.root_domain) query.set('root_domain', params.root_domain)
  query.set('limit', String(params.limit ?? 500))
  return apiFetch<{ items: ProjectAsset[]; total: number }>(
    `/v1/projects/${encodeURIComponent(projectId)}/assets?${query.toString()}`,
  )
}
