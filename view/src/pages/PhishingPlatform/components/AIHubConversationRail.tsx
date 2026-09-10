import { useMemo } from 'react'
import { Button, Empty, Popconfirm, Space, Spin, Tooltip } from 'antd'
import {
  DeleteOutlined,
  MenuFoldOutlined,
  MenuUnfoldOutlined,
  MessageOutlined,
  PlusOutlined,
} from '@ant-design/icons'
import type { Conversation } from '../../../services/agentService'

interface ConversationGroup {
  key: string
  label: string
  items: Conversation[]
}

interface AIHubConversationRailProps {
  conversations: Conversation[]
  activeConversationId: string | null
  loading: boolean
  collapsed: boolean
  disabled: boolean
  onCollapsedChange: (collapsed: boolean) => void
  onNew: () => void
  onSelect: (conversationId: string) => void
  onDelete: (conversationId: string) => void
}

const dateValue = (conversation: Conversation) => {
  const value = conversation.last_message_at || conversation.updated_at || conversation.created_at
  const timestamp = value ? new Date(value).getTime() : 0
  return Number.isFinite(timestamp) ? timestamp : 0
}

const formatConversationTime = (conversation: Conversation) => {
  const timestamp = dateValue(conversation)
  if (!timestamp) return ''
  const date = new Date(timestamp)
  return date.toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' })
}

function groupConversations(conversations: Conversation[]): ConversationGroup[] {
  const now = new Date()
  const startOfToday = new Date(now.getFullYear(), now.getMonth(), now.getDate()).getTime()
  const sevenDaysAgo = startOfToday - 6 * 24 * 60 * 60 * 1000
  const groups: ConversationGroup[] = [
    { key: 'today', label: '今天', items: [] },
    { key: 'recent', label: '最近 7 天', items: [] },
    { key: 'earlier', label: '更早', items: [] },
  ]
  for (const conversation of conversations) {
    const timestamp = dateValue(conversation)
    const group = timestamp >= startOfToday
      ? groups[0]
      : timestamp >= sevenDaysAgo ? groups[1] : groups[2]
    group.items.push(conversation)
  }
  return groups.filter((group) => group.items.length > 0)
}

export default function AIHubConversationRail({
  conversations,
  activeConversationId,
  loading,
  collapsed,
  disabled,
  onCollapsedChange,
  onNew,
  onSelect,
  onDelete,
}: AIHubConversationRailProps) {
  const groups = useMemo(() => groupConversations(conversations), [conversations])

  return (
    <>
      {!collapsed && (
        <button
          type="button"
          className="conversation-sidebar-backdrop"
          aria-label="关闭对话历史"
          onClick={() => onCollapsedChange(true)}
        />
      )}
      <aside className={`conversation-sidebar${collapsed ? ' collapsed' : ''}`}>
        {collapsed ? (
          <div
            className="conversation-sidebar-rail"
            onClick={() => onCollapsedChange(false)}
            role="button"
            tabIndex={0}
            aria-label="展开对话历史"
            onKeyDown={(event) => {
              if (event.key === 'Enter' || event.key === ' ') onCollapsedChange(false)
            }}
          >
            <Tooltip title="新建会话" placement="right">
              <Button
                type="text"
                size="small"
                icon={<PlusOutlined />}
                onClick={(event) => {
                  event.stopPropagation()
                  onNew()
                }}
                disabled={disabled}
              />
            </Tooltip>
            <div className="conversation-sidebar-rail-tab">
              <MenuUnfoldOutlined />
              <span className="conversation-sidebar-rail-text">会话</span>
            </div>
          </div>
        ) : (
          <>
            <div className="conversation-sidebar-header">
              <span className="conversation-sidebar-title">会话目录</span>
              <Space size={4}>
                <Tooltip title="新建会话">
                  <Button
                    type="text"
                    size="small"
                    icon={<PlusOutlined />}
                    onClick={onNew}
                    disabled={disabled}
                  />
                </Tooltip>
                <Tooltip title="收起">
                  <Button
                    type="text"
                    size="small"
                    icon={<MenuFoldOutlined />}
                    onClick={() => onCollapsedChange(true)}
                  />
                </Tooltip>
              </Space>
            </div>
            <div className="conversation-list">
              {loading ? (
                <div className="conversation-loading"><Spin size="small" /></div>
              ) : groups.length === 0 ? (
                <Empty
                  image={Empty.PRESENTED_IMAGE_SIMPLE}
                  description="暂无会话"
                  style={{ marginTop: 40 }}
                />
              ) : groups.map((group) => (
                <section className="conversation-group" key={group.key}>
                  <div className="conversation-group-label">{group.label}</div>
                  {group.items.map((conversation) => (
                    <div
                      key={conversation.conversation_id}
                      className={`conversation-item${conversation.conversation_id === activeConversationId ? ' active' : ''}`}
                      onClick={() => onSelect(conversation.conversation_id)}
                    >
                      <MessageOutlined className="conversation-item-icon" />
                      <span className="conversation-item-copy">
                        <span className="conversation-item-title">{conversation.title || '新会话'}</span>
                        <span className="conversation-item-meta">
                          {conversation.message_count || 0} 条
                          {formatConversationTime(conversation) && ` · ${formatConversationTime(conversation)}`}
                        </span>
                      </span>
                      <Popconfirm
                        title="删除该会话？"
                        okText="删除"
                        cancelText="取消"
                        onConfirm={(event) => {
                          event?.stopPropagation()
                          onDelete(conversation.conversation_id)
                        }}
                        onCancel={(event) => event?.stopPropagation()}
                      >
                        <Button
                          type="text"
                          size="small"
                          className="conversation-item-delete"
                          icon={<DeleteOutlined />}
                          onClick={(event) => event.stopPropagation()}
                        />
                      </Popconfirm>
                    </div>
                  ))}
                </section>
              ))}
            </div>
          </>
        )}
      </aside>
    </>
  )
}
