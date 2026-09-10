import { useMemo } from 'react'
import {
  CheckCircleOutlined,
  ClockCircleOutlined,
  CloseCircleOutlined,
  LinkOutlined,
  LoadingOutlined,
  ReloadOutlined,
  StopOutlined,
} from '@ant-design/icons'
import { Button, Collapse, Empty, Space, Spin, Tabs, Tag, Tooltip, Typography } from 'antd'
import {
  formatArtifactSize,
  getArtifactPresentation,
  type Artifact,
  type ExecutionNode,
  type ExecutionState,
} from '../../../services/agentService'
import AIHubArtifactIcon from './AIHubArtifactIcon'

const { Text } = Typography

interface AIHubWorkspaceInspectorProps {
  executionState?: ExecutionState
  artifacts: Artifact[]
  artifactsLoading: boolean
  onReloadArtifacts: () => void
  onReferenceArtifact: (artifact: Artifact) => void
  onDownloadArtifact: (artifact: Artifact) => void
}

const statusMeta = (node: ExecutionNode) => {
  if (node.status === 'running') {
    return { label: '执行中', color: 'processing', icon: <LoadingOutlined spin /> }
  }
  if (node.status === 'success') {
    return { label: '完成', color: 'success', icon: <CheckCircleOutlined /> }
  }
  if (node.status === 'error') {
    return { label: '失败', color: 'error', icon: <CloseCircleOutlined /> }
  }
  if (node.status === 'abort') {
    return { label: '中止', color: 'default', icon: <StopOutlined /> }
  }
  return { label: '等待', color: 'default', icon: <ClockCircleOutlined /> }
}

const nodeDuration = (node: ExecutionNode) => {
  const duration = node.duration
    ?? (node.endTime && node.startTime ? node.endTime - node.startTime : undefined)
  if (!duration || duration < 1) return ''
  return duration >= 1000 ? `${(duration / 1000).toFixed(1)}s` : `${Math.round(duration)}ms`
}

export default function AIHubWorkspaceInspector({
  executionState,
  artifacts,
  artifactsLoading,
  onReloadArtifacts,
  onReferenceArtifact,
  onDownloadArtifact,
}: AIHubWorkspaceInspectorProps) {
  const nodes = useMemo(
    () => executionState
      ? [...executionState.nodes.values()].sort((left, right) => left.startTime - right.startTime)
      : [],
    [executionState],
  )
  const activeCount = executionState?.activeNodes.size || 0

  const executionPanel = nodes.length ? (
    <div className="ai-hub-inspector-scroll">
      <div className="ai-hub-inspector-summary">
        <span><b>{nodes.length}</b> 个执行节点</span>
        <span>{activeCount ? `${activeCount} 个运行中` : '本轮已稳定'}</span>
      </div>
      <Collapse
        ghost
        destroyOnHidden
        className="ai-hub-execution-list"
        items={nodes.map((node) => {
          const meta = statusMeta(node)
          return {
            key: node.path,
            label: (
              <div className="ai-hub-execution-label">
                <span className={`ai-hub-execution-icon ${node.status}`}>{meta.icon}</span>
                <span className="ai-hub-execution-name">{node.displayName || node.name}</span>
                {nodeDuration(node) && <Text type="secondary">{nodeDuration(node)}</Text>}
              </div>
            ),
            extra: <Tag color={meta.color} bordered={false}>{meta.label}</Tag>,
            children: (
              <div className="ai-hub-execution-detail">
                {node.description && <p>{node.description}</p>}
                {node.content && <pre>{node.content.slice(0, 40_000)}</pre>}
                {!node.description && !node.content && <Text type="secondary">暂无附加输出</Text>}
              </div>
            ),
          }
        })}
      />
    </div>
  ) : (
    <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="当前会话暂无执行轨迹" />
  )

  const artifactPanel = (
    <Spin spinning={artifactsLoading}>
      <div className="ai-hub-inspector-scroll">
        <div className="ai-hub-inspector-summary">
          <span><b>{artifacts.length}</b> 个产物</span>
          <Tooltip title="刷新产物">
            <Button type="text" size="small" icon={<ReloadOutlined />} onClick={onReloadArtifacts} />
          </Tooltip>
        </div>
        {artifacts.length ? artifacts.map((artifact) => {
          const presentation = getArtifactPresentation(artifact)
          return (
            <div className="ai-hub-inspector-artifact" key={artifact.artifact_id}>
              <span className="ai-hub-inspector-artifact-icon">
                <AIHubArtifactIcon artifact={artifact} />
              </span>
              <span className="ai-hub-inspector-artifact-copy">
                <strong>{artifact.title}</strong>
                <span>{presentation.label} · {formatArtifactSize(artifact.size) || '大小未知'}</span>
              </span>
              <Space size={2}>
                <Tooltip title="引用">
                  <Button
                    type="text"
                    size="small"
                    icon={<LinkOutlined />}
                    onClick={() => onReferenceArtifact(artifact)}
                  />
                </Tooltip>
                <Tooltip title={`下载 ${presentation.label}`}>
                  <Button
                    type="text"
                    size="small"
                    icon={<AIHubArtifactIcon artifact={artifact} />}
                    onClick={() => onDownloadArtifact(artifact)}
                  />
                </Tooltip>
              </Space>
            </div>
          )
        }) : (
          <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="当前会话暂无产物" />
        )}
      </div>
    </Spin>
  )

  return (
    <aside className="ai-hub-workspace-inspector">
      <Tabs
        size="small"
        defaultActiveKey="execution"
        items={[
          { key: 'execution', label: '执行轨迹', children: executionPanel },
          { key: 'artifacts', label: `产物 ${artifacts.length}`, children: artifactPanel },
        ]}
      />
    </aside>
  )
}
