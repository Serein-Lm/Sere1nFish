import type { LibraryTarget } from '../services/targetLibraryService'

export const PROJECT_TARGET_TABS = ['target', 'website', 'xiaohongshu', 'wechat', 'bidding', 'scholars'] as const
export type ProjectTargetTab = typeof PROJECT_TARGET_TABS[number]
export type LibraryDetailTab = 'overview' | 'documents' | 'mobile' | 'scans' | 'versions'

export const projectTargetTab = (value: string | null): ProjectTargetTab =>
  PROJECT_TARGET_TABS.includes(value as ProjectTargetTab) ? value as ProjectTargetTab : 'target'

export const libraryDetailTab = (value: string | null): LibraryDetailTab =>
  ['overview', 'documents', 'mobile', 'scans', 'versions'].includes(value || '') ? value as LibraryDetailTab : 'overview'

export function projectTargetPath(projectId: string, targetId: string, tab: ProjectTargetTab = 'target') {
  const path = `/projects/${encodeURIComponent(projectId)}/targets/${encodeURIComponent(targetId)}`
  return tab === 'target' ? path : `${path}?tab=${tab}`
}

export function targetLibraryPath(targetId: string, tab: LibraryDetailTab = 'overview') {
  const path = `/targets/${encodeURIComponent(targetId)}`
  return tab === 'overview' ? path : `${path}?tab=${tab}`
}

export function targetEntryPath(target: LibraryTarget, projectId = '') {
  const project = target.projects.find(item => item.project_id === projectId)
    || (target.projects.length === 1 ? target.projects[0] : undefined)
  return project ? projectTargetPath(project.project_id, target.target_id) : targetLibraryPath(target.target_id)
}

export function targetListReturnPath(state: { targetListSearch?: string } | null) {
  const search = state?.targetListSearch
  return typeof search === 'string' && search.startsWith('?') ? `/targets${search}` : '/targets'
}
