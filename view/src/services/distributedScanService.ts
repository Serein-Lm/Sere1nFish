import { apiFetch } from './http'

export type NodeStatus = 'online' | 'draining' | 'offline' | 'disabled'
export type WorkStatus = 'queued' | 'leased' | 'running' | 'retry' | 'completed' | 'failed' | 'cancelled'

export interface DistributedOverview {
  nodes: Record<string, number>
  work: Record<string, number>
  proxies: Record<string, number>
  runtime: {
    enabled: boolean
    kinds: string[]
    project_ids: string[]
    fallback_local: boolean
    dispatch_concurrency: number
    wait_seconds: number
    batch_sizes: Record<string, number>
    proxy_mode: 'none' | 'best_effort' | 'required'
    has_default_proxy: boolean
  }
}

export interface ScanNode {
  node_id: string
  display_name: string
  status: NodeStatus
  capabilities: Record<string, string>
  capacity: Record<string, number>
  usage: Record<string, number>
  labels: Record<string, string>
  version: string
  last_heartbeat_at?: string
  registered_at?: string
}

export interface DistributedWorkItem {
  work_item_id: string
  task_id?: string
  project_id?: string
  target_id?: string
  kind: string
  status: WorkStatus
  attempt: number
  max_attempts: number
  priority: number
  lease?: { node_id?: string; expires_at?: string }
  last_error?: { code?: string; message?: string }
  cancel_reason?: string
  progress?: Record<string, unknown>
  payload?: Record<string, unknown>
  result?: Record<string, unknown>
  artifacts?: Array<Record<string, unknown>>
  created_at?: string
  updated_at?: string
}

export interface ProxyProfile {
  profile_id: string
  name: string
  type: 'socks5'
  status: 'active' | 'disabled'
  endpoint_hint: string
  has_username: boolean
  has_password: boolean
  dns_mode: 'remote' | 'local'
  max_concurrency: number
  active_leases: number
  failure_threshold: number
  cooldown_seconds: number
  bypass_classes: string[]
  consecutive_failures: number
  cooldown_until?: string
  last_success_at?: string
  last_failure_at?: string
  labels: Record<string, string>
}

export interface BrowserPoolStatus {
  mode: string
  containers: Array<{
    container_id?: string
    name?: string
    status?: string
    task_id?: string
    purpose?: string
    last_used?: string
  }>
  total: number
  busy: number
  idle: number
  capacity?: Record<string, unknown>
  error?: string
}

export interface BootstrapInput {
  display_name: string
  allowed_capabilities: string[]
  labels: Record<string, string>
  expires_minutes: number
}

export interface BootstrapResult {
  bootstrap_id: string
  bootstrap_token: string
  display_name: string
  allowed_capabilities: string[]
  labels: Record<string, string>
  expires_at: string
}

export interface ProxyProfileInput {
  name: string
  endpoint?: string
  username?: string
  password?: string
  dns_mode: 'remote' | 'local'
  max_concurrency: number
  failure_threshold: number
  cooldown_seconds: number
  bypass_classes: string[]
  labels: Record<string, string>
  status: 'active' | 'disabled'
}

export async function getDistributedOverview(): Promise<DistributedOverview> {
  return apiFetch<DistributedOverview>('/v1/distributed-scan/overview')
}

export async function listScanNodes(): Promise<{ items: ScanNode[]; total: number }> {
  return apiFetch<{ items: ScanNode[]; total: number }>('/v1/distributed-scan/nodes')
}

export async function createNodeBootstrap(input: BootstrapInput): Promise<BootstrapResult> {
  return apiFetch<BootstrapResult>('/v1/distributed-scan/nodes/bootstrap', {
    method: 'POST',
    body: JSON.stringify(input),
  })
}

export async function setScanNodeStatus(nodeId: string, status: NodeStatus): Promise<ScanNode> {
  const response = await apiFetch<{ node: ScanNode }>(
    `/v1/distributed-scan/nodes/${encodeURIComponent(nodeId)}/status`,
    { method: 'PUT', body: JSON.stringify({ status }) },
  )
  return response.node
}

export async function rotateScanNodeToken(nodeId: string): Promise<{ node_id: string; node_token: string }> {
  return apiFetch<{ node_id: string; node_token: string }>(`/v1/distributed-scan/nodes/${encodeURIComponent(nodeId)}/rotate-token`, {
    method: 'POST',
  })
}

export async function listDistributedWork(input: {
  page: number
  pageSize: number
  status?: string
  kind?: string
}): Promise<{ items: DistributedWorkItem[]; total: number; page: number; page_size: number }> {
  const params = new URLSearchParams({ page: String(input.page), page_size: String(input.pageSize) })
  if (input.status) params.set('status', input.status)
  if (input.kind) params.set('kind', input.kind)
  return apiFetch<{ items: DistributedWorkItem[]; total: number; page: number; page_size: number }>(
    `/v1/distributed-scan/work-items?${params.toString()}`,
  )
}

export async function getDistributedWork(workItemId: string): Promise<DistributedWorkItem> {
  return apiFetch<DistributedWorkItem>(`/v1/distributed-scan/work-items/${encodeURIComponent(workItemId)}`)
}

export async function cancelDistributedWork(workItemId: string): Promise<void> {
  await apiFetch(`/v1/distributed-scan/work-items/${encodeURIComponent(workItemId)}/cancel`, {
    method: 'POST',
  })
}

export async function listProxyProfiles(): Promise<{ items: ProxyProfile[]; total: number }> {
  return apiFetch<{ items: ProxyProfile[]; total: number }>('/v1/distributed-scan/proxy-profiles')
}

export async function saveProxyProfile(input: ProxyProfileInput, profileId?: string): Promise<ProxyProfile> {
  const path = profileId
    ? `/v1/distributed-scan/proxy-profiles/${encodeURIComponent(profileId)}`
    : '/v1/distributed-scan/proxy-profiles'
  const response = await apiFetch<{ profile: ProxyProfile }>(path, {
    method: profileId ? 'PUT' : 'POST',
    body: JSON.stringify(input),
  })
  return response.profile
}

export async function testProxyProfile(profileId: string): Promise<{ ok: boolean; latency_ms: number }> {
  return apiFetch<{ ok: boolean; latency_ms: number }>(`/v1/distributed-scan/proxy-profiles/${encodeURIComponent(profileId)}/test`, {
    method: 'POST',
  })
}

export async function deleteProxyProfile(profileId: string): Promise<void> {
  await apiFetch(`/v1/distributed-scan/proxy-profiles/${encodeURIComponent(profileId)}`, {
    method: 'DELETE',
  })
}

export async function getBrowserPoolStatus(): Promise<BrowserPoolStatus> {
  return apiFetch<BrowserPoolStatus>('/v1/browser/pool/status')
}
