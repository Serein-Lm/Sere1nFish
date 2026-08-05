import { Space, Tag, Tooltip, Typography } from 'antd'
import type { ProjectTargetRelation } from '../services/projectService'

const { Text } = Typography

interface TargetRelationLabelProps {
  name?: string | null
  relation?: ProjectTargetRelation | null
}

function formatPercent(value?: number | null): string {
  if (value == null || !Number.isFinite(value)) return ''
  return Number.isInteger(value) ? String(value) : value.toFixed(2).replace(/0+$/, '').replace(/\.$/, '')
}

function depthLabel(depth: number): string {
  if (depth <= 0) return '主 Target'
  if (depth === 1) return '子单位'
  if (depth === 2) return '孙单位'
  return `${depth} 级单位`
}

function controlLabel(relation: ProjectTargetRelation): string {
  if (relation.is_primary || relation.control_kind === 'primary') return '主 Target'
  const percent = formatPercent(relation.effective_ownership_percent)
  if (percent) {
    return `${percent}% ${relation.relation_depth > 1 ? '穿透控股' : '控股'}`
  }
  if (relation.control_kind === 'wholly_owned') return '全资控股'
  if (relation.control_kind === 'controlled') {
    return relation.relation_depth > 1 ? '穿透控股' : '控股关系'
  }
  return '关联单位'
}

function relationTooltip(relation: ProjectTargetRelation): string {
  const parts: string[] = []
  if (relation.lineage_target_names?.length) {
    parts.push(`关系链路：${relation.lineage_target_names.join(' > ')}`)
  }
  const effective = formatPercent(relation.effective_ownership_percent)
  if (effective) parts.push(`主 Target 穿透持股：${effective}%`)
  const direct = formatPercent(relation.ownership_percent)
  if (direct && relation.relation_depth > 1) parts.push(`直接上级持股：${direct}%`)
  return parts.join('；')
}

export default function TargetRelationLabel({ name, relation }: TargetRelationLabelProps) {
  const displayName = name || relation?.target_name || ''
  if (!displayName && !relation) return <Text type="secondary">-</Text>

  const isPrimary = Boolean(relation?.is_primary || relation?.control_kind === 'primary')
  const targetDiffers = Boolean(
    relation?.target_name
      && displayName
      && relation.target_name.trim() !== displayName.trim(),
  )

  return (
    <Space orientation="vertical" size={2} style={{ minWidth: 0, maxWidth: '100%' }}>
      <Tooltip title={displayName}>
        <Text strong ellipsis style={{ maxWidth: '100%' }}>{displayName || '-'}</Text>
      </Tooltip>
      {targetDiffers && (
        <Text type="secondary" ellipsis style={{ maxWidth: '100%', fontSize: 12 }}>
          采集 Target：{relation?.target_name}
        </Text>
      )}
      {relation && (
        <Tooltip title={relationTooltip(relation)}>
          <Space size={[4, 4]} wrap>
            <Tag color={isPrimary ? 'blue' : relation.control_kind === 'related' ? 'default' : 'green'}>
              {controlLabel(relation)}
            </Tag>
            {!isPrimary && <Tag>{depthLabel(relation.relation_depth)}</Tag>}
            {!isPrimary && relation.root_target_name && (
              <Text type="secondary" ellipsis style={{ maxWidth: 220, fontSize: 12 }}>
                主 Target：{relation.root_target_name}
              </Text>
            )}
          </Space>
        </Tooltip>
      )}
    </Space>
  )
}
