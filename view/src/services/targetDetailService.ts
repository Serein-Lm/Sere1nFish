import { getLibraryTarget, type LibraryTarget } from './targetLibraryService'
import { getProject, type Project } from './projectService'
import { getProjectTargetDashboard, listProjectTargetOptions, type ProjectTargetDashboard, type ProjectTargetOption } from './sourceDocumentService'

export interface TargetProjectContext {
  library: LibraryTarget
  project: Project
  dashboard: ProjectTargetDashboard | null
  identities: ProjectTargetOption[]
}

export async function loadTargetProjectContext(projectId: string, targetId: string): Promise<TargetProjectContext> {
  const library = await getLibraryTarget(targetId)
  const relation = library.projects.find(item => item.project_id === projectId)
  if (!relation) throw new Error('这个 Target 未关联到当前项目，请从目标库选择所属项目。')
  if (!relation.active) return { library, project: await getProject(projectId), dashboard: null, identities: [] }

  // Library identities may differ across projects; resolve by stable IDs, never by name.
  const identities = library.member_target_ids.length > 1
    ? (await listProjectTargetOptions(projectId)).items.filter(item => library.member_target_ids.includes(item.target_id))
    : []
  if (library.member_target_ids.length > 1 && !identities.length) {
    throw new Error('当前项目中没有可用的 Target 身份，历史档案仍保留在目标库。')
  }
  const resolvedId = identities.length
    ? (identities.find(item => item.target_id === targetId) || identities[0]).target_id
    : library.target_id
  const [project, dashboard] = await Promise.all([
    getProject(projectId), getProjectTargetDashboard(projectId, resolvedId),
  ])
  return { library, project, dashboard, identities }
}
