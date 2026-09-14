interface TargetHierarchyItem {
  project_id: string
  target_id: string
  hierarchy_parent_target_id?: string
  parent_target_id?: string
  hierarchy_depth?: number
  relation_depth?: number
}

export type TargetTreeNode<T> = T & { children?: TargetTreeNode<T>[] }

/** 按项目内持久化的直接上级和层级构树，缺失上级的搜索命中保留为可见根节点。 */
export function buildTargetHierarchy<T extends TargetHierarchyItem>(items: T[]): TargetTreeNode<T>[] {
  const identity = (projectId: string, targetId: string) => JSON.stringify([projectId, targetId])
  const byId = new Map<string, TargetTreeNode<T>>()
  for (const item of items) {
    byId.set(identity(item.project_id, item.target_id), { ...item, children: undefined })
  }
  const roots: TargetTreeNode<T>[] = []
  for (const item of byId.values()) {
    const parentId = item.hierarchy_parent_target_id || item.parent_target_id
    const parent = parentId ? byId.get(identity(item.project_id, parentId)) : undefined
    const depth = Number(item.hierarchy_depth ?? item.relation_depth ?? 0)
    const parentDepth = Number(parent?.hierarchy_depth ?? parent?.relation_depth ?? 0)
    // 深度必须递增，避免错误关系构成循环；同名单位不能代替稳定 Target 身份。
    if (parent && parent !== item && parentDepth < depth) {
      parent.children ||= []
      parent.children.push(item)
    } else {
      roots.push(item)
    }
  }
  return roots
}
