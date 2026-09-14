import { listProjects, type Project } from './projectService'
import { listProjectTargets, type ProjectTargetSummary } from './sourceDocumentService'

export interface TargetListRow extends ProjectTargetSummary {
  project_name: string
}

export async function loadTargetListProjects(groupId?: string): Promise<Project[]> {
  const first = await listProjects({ group_id: groupId, page_size: 100 })
  const remaining = await Promise.all(Array.from(
    { length: Math.max(0, Math.ceil(first.total / 100) - 1) },
    (_, index) => listProjects({ group_id: groupId, page: index + 2, page_size: 100 }),
  ))
  return [...first.items, ...remaining.flatMap((page) => page.items)]
}

export async function loadTargetListRows(projects: Project[], query: string) {
  const rows: TargetListRow[] = []
  const errors: string[] = []
  const queue = [...projects]
  await Promise.all(Array.from({ length: Math.min(4, queue.length) }, async () => {
    while (queue.length) {
      const project = queue.shift()!
      try {
        let page = 1
        let totalPages = 1
        const projectRows: TargetListRow[] = []
        do {
          const result = await listProjectTargets(project.id, { q: query, page, page_size: 100 })
          totalPages = Math.ceil(result.total / 100)
          projectRows.push(...result.items.map((item) => ({ ...item, project_name: project.name })))
          page += 1
        } while (page <= totalPages)
        rows.push(...projectRows)
      } catch (error) {
        errors.push(`${project.name}：${(error as Error).message}`)
      }
    }
  }))
  return { rows: rows.sort((a, b) => a.project_name.localeCompare(b.project_name, 'zh-CN')), errors }
}
