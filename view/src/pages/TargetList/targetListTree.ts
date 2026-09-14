import type { TargetListRow } from '../../services/targetListService'
import { buildTargetHierarchy } from '../../utils/targetHierarchy'

export interface TargetListTreeRow extends TargetListRow {
  children?: TargetListTreeRow[]
  isLoadingPlaceholder?: boolean
}

export function buildTargetListTree(
  rows: TargetListRow[],
  branches: Record<string, TargetListRow[]>,
  loading: Record<string, boolean>,
): TargetListTreeRow[] {
  const tree = buildTargetHierarchy<TargetListTreeRow>([
    ...rows,
    ...rows.flatMap((row) => branches[row.project_target_id] || []),
  ])
  for (const row of tree) {
    if (!row.children?.length && (row.child_count || row.descendant_count)
      && !Object.prototype.hasOwnProperty.call(branches, row.project_target_id)) {
      row.children = [{
        ...row,
        project_target_id: `lazy:${row.project_target_id}`,
        target_id: `lazy:${row.target_id}`,
        target_name: loading[row.project_target_id] ? '正在加载关联单位…' : '展开以加载关联单位，加载失败时可重新展开重试',
        children: undefined,
        isLoadingPlaceholder: true,
      }]
    }
  }
  return tree
}
