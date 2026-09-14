import { listProjects, type Project } from './projectService'
import { listProjectTargetBranch, listProjectTargets, type ProjectTargetSummary } from './sourceDocumentService'

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
          let items = result.items
          if (query.trim()) {
            const expanded = new Set(result.expanded_project_target_ids)
            const matched = new Set(result.matched_target_ids)
            const branches = await Promise.all(items.filter((item) => expanded.has(item.project_target_id))
              .map((item) => listProjectTargetBranch(project.id, item.target_id)))
            items = [...new Map([...items, ...branches.flatMap((branch) => branch.items)]
              .filter((item) => matched.has(item.target_id)).map((item) => [item.target_id, item])).values()]
          }
          projectRows.push(...items.map((item) => ({ ...item, project_name: project.name })))
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
