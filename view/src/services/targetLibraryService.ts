import { apiFetch } from './http'
import type { CollectRecord } from './mobileCollectService'

export interface LibraryRun {
  task_id: string; project_id: string; task_type: string; status: string
  started_at?: string; completed_at?: string; created_at?: string
  progress?: { stage?: string; message?: string; current?: number; total?: number }
  incremental: boolean; incremental_until?: string; cursor_advanced?: boolean
  incremental_targets?: Record<string, { since?: string; scope_key?: string }>
}
export interface LibraryTarget {
  target_id: string; target_name: string; member_target_ids: string[]; aliases: string[]
  projects: Array<{ project_id: string; project_name: string; active: boolean }>
  related_units?: Array<{ target_id: string; target_name: string; direction: string; relation_type: string; summary?: string; project_id: string; source_urls: string[]; active: boolean }>
  relationships: Array<{ parent_id: string; parent_name: string; project_id: string; project_name: string; active: boolean; relation_type: string; ownership_percent?: number; source_urls: string[]; source_id: string }>
  parent_target_id: string; parent_target_name: string; child_count: number; merged_count: number; relation_conflict: boolean
  document_count: number; version_count: number; change_count: number; mobile_count: number; bidding_count: number; scan_count: number
  first_scan_at?: string; last_scan_at?: string; last_success_at?: string
  latest_run?: LibraryRun; latest_incremental_run?: LibraryRun
  incremental_scopes: Array<{ project_id: string; target_id: string; scope_key?: string; baseline_since?: string; through_at?: string; run_task_id?: string }>
}
export interface LibraryPage { items: LibraryTarget[]; total: number; page: number; page_size: number; target_count: number; original_target_count: number; scoped_target_count: number; generated_at: string }
export interface HistoryPage<T> { items: T[]; total: number; skip: number; limit: number }
export interface LibraryDocument { document_id: string; canonical_url: string; title?: string; source_type?: string; publish_time?: string; first_seen_at?: string; last_seen_at?: string; latest_version_id?: string; summary?: string }
export interface LibraryVersion { document_id: string; version_id: string; content_hash: string; captured_at?: string; status: string; identity?: { title?: string; canonical_url?: string } }
export type HistoryKind = 'documents' | 'versions' | 'scans' | 'mobile'
export type HistoryItem = LibraryDocument | LibraryVersion | LibraryRun | CollectRecord

export function listTargetLibrary(params: { page?: number; page_size?: number; q?: string; project_id?: string; parent_id?: string; refresh?: boolean } = {}) {
  const query = new URLSearchParams(Object.entries(params).filter(([, value]) => value !== undefined).map(([key, value]) => [key, String(value)]))
  return apiFetch<LibraryPage>(`/v1/target-library?${query}`)
}
export const getLibraryTarget = (id: string) => apiFetch<LibraryTarget>(`/v1/target-library/${encodeURIComponent(id)}`)
export function getLibraryHistory<T = HistoryItem>(id: string, kind: HistoryKind, page = 1, documentId = '') {
  const query = new URLSearchParams({ kind, skip: String((page - 1) * 20), limit: '20', document_id: documentId })
  return apiFetch<HistoryPage<T>>(`/v1/target-library/${encodeURIComponent(id)}/history?${query}`)
}
export function libraryRunLabel(run?: LibraryRun) {
  if (!run) return '尚无执行记录'
  if (run.progress?.stage === 'waiting_mobile') return '等待手机'
  return ({ running: '采集中', completed: '已完成', queued: '排队中', pending: '待执行', paused: '已暂停', failed: '失败', error: '失败', cancelled: '已取消' } as Record<string, string>)[run.status] || run.status
}

export interface VersionComparison { before_version_id?: string; after_version_id: string; lines: string[]; changed: boolean; truncated: boolean; message?: string }
export const compareLibraryVersion = (id: string, versionId: string) => apiFetch<VersionComparison>(`/v1/target-library/${encodeURIComponent(id)}/compare?version_id=${encodeURIComponent(versionId)}`)
