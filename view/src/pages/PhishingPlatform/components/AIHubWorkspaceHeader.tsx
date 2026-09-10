import { ApiOutlined, HistoryOutlined, InboxOutlined } from '@ant-design/icons'
import { Badge, Button, Segmented, Space, Tooltip } from 'antd'

export type AIHubLayoutMode = 'chat' | 'split'

interface AIHubWorkspaceHeaderProps {
  title: string
  requesting: boolean
  messageCount: number
  artifactCount: number
  referenceCount: number
  skillCount: number
  layoutMode: AIHubLayoutMode
  onLayoutModeChange: (mode: AIHubLayoutMode) => void
  onShowHistory: () => void
  onShowCapabilities: () => void
  onShowArtifacts: () => void
}

export default function AIHubWorkspaceHeader({
  title,
  requesting,
  messageCount,
  artifactCount,
  referenceCount,
  skillCount,
  layoutMode,
  onLayoutModeChange,
  onShowHistory,
  onShowCapabilities,
  onShowArtifacts,
}: AIHubWorkspaceHeaderProps) {
  return (
    <header className="ai-hub-workspace-header">
      <div className="ai-hub-workspace-title">
        <Button
          className="mobile-history-button"
          size="small"
          type="text"
          icon={<HistoryOutlined />}
          aria-label="打开会话目录"
          onClick={onShowHistory}
        />
        <span className={`ai-hub-presence${requesting ? ' running' : ''}`} aria-hidden="true" />
        <div>
          <strong>{title}</strong>
          <span>{requesting ? '正在执行' : '已连接'}</span>
        </div>
      </div>

      <div className="ai-hub-runtime-strip" aria-label="会话状态">
        <span><b>{messageCount}</b> 消息</span>
        <span><b>{referenceCount}</b> 引用</span>
        <span><b>{skillCount}</b> Skills</span>
      </div>

      <Segmented
        className="ai-hub-layout-switch"
        size="small"
        value={layoutMode}
        options={[
          { label: '对话', value: 'chat' },
          { label: '并排', value: 'split' },
        ]}
        onChange={(value) => onLayoutModeChange(value as AIHubLayoutMode)}
      />

      <Space size={6} className="ai-hub-header-actions">
        <Tooltip title="Agent、Prompt、工具与查询接口">
          <Button size="small" type="text" icon={<ApiOutlined />} onClick={onShowCapabilities}>
            能力
          </Button>
        </Tooltip>
        <Tooltip title="当前会话产物">
          <Badge count={artifactCount} size="small" overflowCount={99}>
            <Button size="small" type="text" icon={<InboxOutlined />} onClick={onShowArtifacts}>
              产物
            </Button>
          </Badge>
        </Tooltip>
      </Space>
    </header>
  )
}
