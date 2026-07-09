import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from 'react'
import {
  Typography,
  Button,
  Input,
  InputNumber,
  Select,
  Switch,
  Tag,
  Table,
  Modal,
  Empty,
  message,
  Popconfirm,
  Tabs,
  Progress,
  Descriptions,
  Avatar,
  List,
  Tooltip,
  type TableProps,
} from 'antd'
import {
  RobotOutlined,
  EyeOutlined,
  PlayCircleOutlined,
  StopOutlined,
  ReloadOutlined,
  PlusOutlined,
  CommentOutlined,
  ProfileOutlined,
  BulbOutlined,
  ThunderboltOutlined,
  WarningOutlined,
  UserOutlined,
  MessageOutlined,
  TagsOutlined,
  HeartOutlined,
  SafetyOutlined,
} from '@ant-design/icons'
import {
  getPool,
  startWatch,
  stopWatch,
  startAutoChat,
  stopAutoChat,
  getAutoChatStatus,
  subscribeEvents,
  listProfiles,
  getProfile,
  relativeTime,
  type AutoChatSession,
  type MobileEvent,
  type ContactProfile,
} from '../../services/mobileService'
import './MobileAutoChat.css'

const { Title, Paragraph, Text } = Typography

interface MobileAutoChatProps {
  embedded?: boolean
}

interface DeviceOpt {
  label: string
  value: string
}
interface WatcherItem {
  watch_id: string
  device_id: string
  platform: string
  auto_send: boolean
}

const BG_KEY = 'mobile_my_background'

export default function MobileAutoChat({ embedded = false }: MobileAutoChatProps) {
  const [activeTab, setActiveTab] = useState('monitor')
  const [devices, setDevices] = useState<DeviceOpt[]>([])
  const [sessions, setSessions] = useState<AutoChatSession[]>([])
  const [watchers, setWatchers] = useState<WatcherItem[]>([])
  const [events, setEvents] = useState<MobileEvent[]>([])
  const [loading, setLoading] = useState(false)

  // 人物画像
  const [profiles, setProfiles] = useState<ContactProfile[]>([])
  const [profilesLoading, setProfilesLoading] = useState(false)
  const [selectedContactId, setSelectedContactId] = useState<string | null>(null)
  const [selectedProfile, setSelectedProfile] = useState<ContactProfile | null>(null)
  const [profileDetailLoading, setProfileDetailLoading] = useState(false)

  // watcher 表单
  const [w, setW] = useState({
    device_id: '',
    platform: '微信',
    my_background: localStorage.getItem(BG_KEY) ?? '',
    auto_accept: true,
    auto_send: false,
    interval: 20,
  })
  const [watchStarting, setWatchStarting] = useState(false)

  // 新会话弹窗
  const [sessionOpen, setSessionOpen] = useState(false)
  const [s, setS] = useState({
    device_id: '',
    contact_id: '',
    contact_name: '',
    goal: '',
    my_background: localStorage.getItem(BG_KEY) ?? '',
    platform: '微信',
    interval: 8,
    auto_send: false,
    ensure_chat: true,
  })
  const [starting, setStarting] = useState(false)

  const loadStatus = useCallback(async () => {
    setLoading(true)
    try {
      const res = await getAutoChatStatus()
      setSessions(res.sessions || [])
    } catch {
      /* 后端未连接时静默 */
    } finally {
      setLoading(false)
    }
  }, [])

  const loadProfiles = useCallback(async () => {
    setProfilesLoading(true)
    try {
      const list = await listProfiles(undefined, 100)
      setProfiles(list)
      setSelectedContactId((cur) => cur ?? (list[0]?.contact_id ?? null))
    } catch {
      /* 静默 */
    } finally {
      setProfilesLoading(false)
    }
  }, [])

  const loadProfileDetail = useCallback(async (contactId: string) => {
    setProfileDetailLoading(true)
    try {
      const p = await getProfile(contactId)
      setSelectedProfile(p)
    } catch {
      setSelectedProfile(null)
    } finally {
      setProfileDetailLoading(false)
    }
  }, [])

  useEffect(() => {
    getPool()
      .then((r) => setDevices(r.devices.map((d) => ({ label: d.model || d.device_id, value: d.device_id }))))
      .catch(() => {})
  }, [])

  useEffect(() => {
    loadStatus()
    const t = window.setInterval(loadStatus, 5000)
    return () => clearInterval(t)
  }, [loadStatus])

  // 画像列表：进入画像 tab 时加载并周期刷新（实时增长）
  useEffect(() => {
    if (activeTab !== 'personas') return
    loadProfiles()
    const t = window.setInterval(loadProfiles, 6000)
    return () => clearInterval(t)
  }, [activeTab, loadProfiles])

  // 选中画像详情：周期刷新以体现实时更新
  useEffect(() => {
    if (activeTab !== 'personas' || !selectedContactId) return
    loadProfileDetail(selectedContactId)
    const t = window.setInterval(() => loadProfileDetail(selectedContactId), 6000)
    return () => clearInterval(t)
  }, [activeTab, selectedContactId, loadProfileDetail])

  // 实时事件
  useEffect(() => {
    let stop = false
    const ctrl = new AbortController()
    ;(async () => {
      let wait = 1000
      while (!stop && !ctrl.signal.aborted) {
        try {
          await subscribeEvents(
            { types: ['auto_chat', 'auto_chat_watch', 'suggestion', 'profile_updated'] },
            (ev) => {
              setEvents((prev) => [...prev.slice(-120), ev])
              // 画像更新事件到达时，若正查看该联系人则即时刷新
              if (ev.type === 'profile_updated') {
                setSelectedContactId((cur) => {
                  if (cur && ev.contact_id === cur) loadProfileDetail(cur)
                  return cur
                })
              }
            },
            ctrl.signal,
          )
          wait = 1000
        } catch {
          if (stop || ctrl.signal.aborted) break
          await new Promise((r) => setTimeout(r, wait))
          wait = Math.min(wait * 2, 15000)
        }
      }
    })()
    return () => {
      stop = true
      ctrl.abort()
    }
  }, [loadProfileDetail])

  const sbRef = useRef<HTMLDivElement>(null)

  const handleStartWatch = async () => {
    if (!w.device_id) {
      message.warning('请选择设备')
      return
    }
    setWatchStarting(true)
    try {
      localStorage.setItem(BG_KEY, w.my_background)
      const res = await startWatch({
        device_id: w.device_id,
        platform: w.platform,
        my_background: w.my_background,
        auto_accept: w.auto_accept,
        auto_send: w.auto_send,
        interval: w.interval,
      })
      message.success('新好友 watcher 已启动')
      setWatchers((prev) => [...prev, { watch_id: res.watch_id, device_id: w.device_id, platform: w.platform, auto_send: w.auto_send }])
    } catch (e) {
      message.error(e instanceof Error ? e.message : '启动失败')
    } finally {
      setWatchStarting(false)
    }
  }

  const handleStopWatch = async (watchId: string) => {
    try {
      await stopWatch(watchId)
      message.success('watcher 已停止')
      setWatchers((prev) => prev.filter((x) => x.watch_id !== watchId))
    } catch (e) {
      message.error(e instanceof Error ? e.message : '停止失败')
    }
  }

  const handleStartSession = async () => {
    if (!s.device_id) {
      message.warning('请选择设备')
      return
    }
    setStarting(true)
    try {
      localStorage.setItem(BG_KEY, s.my_background)
      await startAutoChat({
        device_id: s.device_id,
        contact_id: s.contact_id || undefined,
        contact_name: s.contact_name || undefined,
        goal: s.goal || undefined,
        my_background: s.my_background,
        platform: s.platform,
        interval: s.interval,
        auto_send: s.auto_send,
        ensure_chat: s.ensure_chat,
      })
      message.success('自动聊天会话已启动')
      setSessionOpen(false)
      loadStatus()
    } catch (e) {
      message.error(e instanceof Error ? e.message : '启动失败')
    } finally {
      setStarting(false)
    }
  }

  const handleStopSession = async (taskId: string) => {
    try {
      await stopAutoChat(taskId)
      message.success('会话已停止')
      loadStatus()
    } catch (e) {
      message.error(e instanceof Error ? e.message : '停止失败')
    }
  }

  const runningCount = sessions.filter((x) => x.running).length
  const totalReplies = sessions.reduce((a, b) => a + (b.replies_sent || 0), 0)

  const columns: TableProps<AutoChatSession>['columns'] = [
    {
      title: '联系人',
      key: 'contact',
      render: (_, r) => (
        <div>
          <div className="ac-contact-name">{r.contact_name || r.contact_id || '（待识别）'}</div>
          <div className="ac-contact-id">{r.contact_id}</div>
        </div>
      ),
    },
    {
      title: '设备',
      dataIndex: 'device_id',
      key: 'device_id',
      width: 150,
      render: (v) => <Text className="ac-mono" type="secondary">{v}</Text>,
    },
    {
      title: '状态',
      key: 'running',
      width: 130,
      render: (_, r) => (
        <div className="ac-status-cell">
          <Tag color={r.running ? 'success' : 'default'}>{r.running ? '运行中' : '已停止'}</Tag>
          {r.auto_send ? <Tag color="error">自动发</Tag> : <Tag>观察</Tag>}
        </div>
      ),
    },
    {
      title: '回合 / 已发',
      key: 'rounds',
      width: 100,
      render: (_, r) => (
        <Text className="ac-mono">
          {r.rounds ?? 0} / {r.replies_sent ?? 0}
        </Text>
      ),
    },
    {
      title: '最近内容',
      key: 'last',
      render: (_, r) => (
        <Text className="ac-last" type="secondary">
          {r.last_reply || r.last_suggestion || r.last_error || '—'}
        </Text>
      ),
    },
    {
      title: '启动',
      dataIndex: 'started_at',
      key: 'started_at',
      width: 100,
      render: (v) => <Text type="secondary">{relativeTime(v)}</Text>,
    },
    {
      title: '操作',
      key: 'action',
      width: 90,
      render: (_, r) =>
        r.running ? (
          <Popconfirm title="停止该会话？" onConfirm={() => handleStopSession(r.task_id)} okText="停止" cancelText="取消">
            <Button size="small" danger icon={<StopOutlined />}>
              停止
            </Button>
          </Popconfirm>
        ) : (
          <Text type="secondary">—</Text>
        ),
    },
  ]

  // ===== 监控 tab 内容 =====
  const monitorTab = (
    <div className="ac-grid">
      {/* 左：watcher + 会话表 */}
      <div className="ac-main">
        <div className="glass-card ac-watcher slide-up stagger-1">
          <div className="ac-card-title">
            <EyeOutlined /> 新好友 Watcher
            <span className="ac-badge-danger">{w.auto_send ? '自动回复' : '观察模式'}</span>
          </div>
          <div className="ac-watcher-form">
            <Select
              placeholder="选择设备"
              style={{ minWidth: 160, flex: 1 }}
              options={devices}
              value={w.device_id || undefined}
              onChange={(v) => setW((p) => ({ ...p, device_id: v }))}
            />
            <Input
              style={{ maxWidth: 120 }}
              value={w.platform}
              onChange={(e) => setW((p) => ({ ...p, platform: e.target.value }))}
              placeholder="平台"
            />
            <InputNumber
              min={8}
              value={w.interval}
              onChange={(v) => setW((p) => ({ ...p, interval: v ?? 20 }))}
              addonAfter="秒"
              style={{ width: 110 }}
            />
          </div>
          <Input.TextArea
            value={w.my_background}
            onChange={(e) => setW((p) => ({ ...p, my_background: e.target.value }))}
            placeholder="我的背景（注入到后续 auto-chat）"
            autoSize={{ minRows: 2, maxRows: 3 }}
            style={{ marginTop: 10 }}
          />
          <div className="ac-switches">
            <span className="ac-switch">
              <Switch checked={w.auto_accept} onChange={(v) => setW((p) => ({ ...p, auto_accept: v }))} size="small" />
              自动通过好友
            </span>
            <span className="ac-switch">
              <Switch checked={w.auto_send} onChange={(v) => setW((p) => ({ ...p, auto_send: v }))} size="small" />
              <span className={w.auto_send ? 'ac-danger-text' : ''}>自动发送回复</span>
            </span>
            <div className="toolbar-spacer" />
            <Button type="primary" icon={<PlayCircleOutlined />} loading={watchStarting} onClick={handleStartWatch}>
              启动 Watcher
            </Button>
          </div>

          {watchers.length > 0 && (
            <div className="ac-watcher-list">
              {watchers.map((wt) => (
                <div key={wt.watch_id} className="ac-watcher-item">
                  <span className="ac-live-dot" />
                  <span className="ac-watcher-id">{wt.watch_id}</span>
                  <Text type="secondary" className="ac-mono">
                    {wt.device_id} · {wt.platform}
                  </Text>
                  <div className="toolbar-spacer" />
                  <Button size="small" danger icon={<StopOutlined />} onClick={() => handleStopWatch(wt.watch_id)}>
                    停止
                  </Button>
                </div>
              ))}
            </div>
          )}
        </div>

        <div className="glass-card ac-sessions slide-up stagger-2">
          <div className="ac-card-title">
            <CommentOutlined /> 自动聊天会话
            <div className="toolbar-spacer" />
            <Button size="small" icon={<ReloadOutlined />} loading={loading} onClick={loadStatus} />
            <Button type="primary" size="small" icon={<PlusOutlined />} onClick={() => setSessionOpen(true)}>
              新会话
            </Button>
          </div>
          <Table<AutoChatSession>
            rowKey="task_id"
            size="small"
            columns={columns}
            dataSource={sessions}
            loading={loading}
            pagination={{ pageSize: 8, hideOnSinglePage: true }}
            locale={{ emptyText: <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="暂无会话，启动 watcher 或点「新会话」" /> }}
          />
        </div>
      </div>

      {/* 右：实时活动 */}
      <div className="glass-card ac-activity slide-up stagger-2">
        <div className="ac-card-title">
          <ThunderboltOutlined /> 实时活动
          <span className="ac-live-badge">
            <span className="ac-live-dot" /> LIVE
          </span>
        </div>
        <div className="ac-timeline">
          {events.length === 0 ? (
            <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="等待事件…" />
          ) : (
            [...events].reverse().map((ev, i) => {
              const m = eventMeta(ev)
              return (
                <div key={`${ev.ts}-${i}`} className={`ac-event tone-${m.tone}`}>
                  <span className="ac-event-icon">{m.icon}</span>
                  <div className="ac-event-main">
                    <div className="ac-event-title">
                      {m.title}
                      <span className="ac-event-time">{relativeTime(ev.ts)}</span>
                    </div>
                    {m.desc && <div className="ac-event-desc">{m.desc}</div>}
                  </div>
                </div>
              )
            })
          )}
        </div>
      </div>
    </div>
  )

  // ===== 人物画像 tab 内容 =====
  const personaTab = (
    <PersonaPanel
      profiles={profiles}
      profilesLoading={profilesLoading}
      selectedContactId={selectedContactId}
      selectedProfile={selectedProfile}
      detailLoading={profileDetailLoading}
      onSelect={setSelectedContactId}
      onRefresh={loadProfiles}
    />
  )

  return (
    <div className={`mobile-autochat${embedded ? ' mobile-module' : ' page-container'} fade-in`}>
      <div className="pc-header slide-up">
        <div className="pc-header-text">
          <Title level={2} className="page-title">
            <RobotOutlined /> 自动聊天中心
          </Title>
          <Paragraph className="page-description">
            加人即自动聊：watcher 检测新好友 → 自动通过 → 进对话 → 起会话；读屏沉淀画像 + 生成/自动发送回复
          </Paragraph>
        </div>
        <div className="ac-stats">
          <div className="ac-stat">
            <div className="ac-stat-num">{runningCount}</div>
            <div className="ac-stat-label">运行会话</div>
          </div>
          <div className="ac-stat">
            <div className="ac-stat-num">{watchers.length}</div>
            <div className="ac-stat-label">活跃 watcher</div>
          </div>
          <div className="ac-stat">
            <div className="ac-stat-num">{totalReplies}</div>
            <div className="ac-stat-label">累计自动发送</div>
          </div>
          <div className="ac-stat">
            <div className="ac-stat-num">{profiles.length}</div>
            <div className="ac-stat-label">画像联系人</div>
          </div>
        </div>
      </div>

      <Tabs
        activeKey={activeTab}
        onChange={setActiveTab}
        className="ac-tabs"
        items={[
          { key: 'monitor', label: <span><CommentOutlined /> 会话监控</span>, children: monitorTab },
          { key: 'personas', label: <span><ProfileOutlined /> 人物画像</span>, children: personaTab },
        ]}
      />

      {/* 新会话弹窗 */}
      <Modal
        title={
          <span>
            <PlusOutlined /> 新建自动聊天会话
          </span>
        }
        open={sessionOpen}
        onCancel={() => setSessionOpen(false)}
        onOk={handleStartSession}
        confirmLoading={starting}
        okText="启动会话"
        cancelText="取消"
        width={520}
      >
        <div className="ac-session-form" ref={sbRef}>
          <label className="field-label">设备</label>
          <Select
            placeholder="选择设备"
            style={{ width: '100%' }}
            options={devices}
            value={s.device_id || undefined}
            onChange={(v) => setS((p) => ({ ...p, device_id: v }))}
          />
          <div className="ac-form-row">
            <div className="ac-form-col">
              <label className="field-label">contact_id（可选，留空自动推导）</label>
              <Input value={s.contact_id} onChange={(e) => setS((p) => ({ ...p, contact_id: e.target.value }))} placeholder="wechat:张三" />
            </div>
            <div className="ac-form-col">
              <label className="field-label">联系人名（可选）</label>
              <Input value={s.contact_name} onChange={(e) => setS((p) => ({ ...p, contact_name: e.target.value }))} placeholder="张三" />
            </div>
          </div>
          <label className="field-label">我的背景</label>
          <Input.TextArea
            value={s.my_background}
            onChange={(e) => setS((p) => ({ ...p, my_background: e.target.value }))}
            autoSize={{ minRows: 2, maxRows: 3 }}
            placeholder="我是 XX 公司商务…"
          />
          <label className="field-label">目标 goal（可选，留空为普通聊天）</label>
          <Input.TextArea
            value={s.goal}
            onChange={(e) => setS((p) => ({ ...p, goal: e.target.value }))}
            autoSize={{ minRows: 2, maxRows: 3 }}
            placeholder="填写本次对话希望达成的目标；填写后将结合话术库按目标生成回复"
          />
          <div className="ac-form-row">
            <div className="ac-form-col">
              <label className="field-label">平台</label>
              <Input value={s.platform} onChange={(e) => setS((p) => ({ ...p, platform: e.target.value }))} />
            </div>
            <div className="ac-form-col">
              <label className="field-label">轮询间隔</label>
              <InputNumber min={2} value={s.interval} onChange={(v) => setS((p) => ({ ...p, interval: v ?? 8 }))} addonAfter="秒" style={{ width: '100%' }} />
            </div>
          </div>
          <div className="ac-switches">
            <span className="ac-switch">
              <Switch checked={s.ensure_chat} onChange={(v) => setS((p) => ({ ...p, ensure_chat: v }))} size="small" />
              自动导航进对话
            </span>
            <span className="ac-switch">
              <Switch checked={s.auto_send} onChange={(v) => setS((p) => ({ ...p, auto_send: v }))} size="small" />
              <span className={s.auto_send ? 'ac-danger-text' : ''}>自动发送</span>
            </span>
          </div>
          <div className="modal-hint">
            发送键由 AI 视觉自助定位并点击，发送后自动恢复原输入法，无需手动配置坐标。
          </div>
          {s.auto_send && (
            <div className="modal-hint ac-warn">
              <WarningOutlined /> 自动发送会真实下发消息，建议先用观察模式确认画像沉淀无误。
            </div>
          )}
        </div>
      </Modal>
    </div>
  )
}

// ============ 人物画像面板 ============
interface PersonaPanelProps {
  profiles: ContactProfile[]
  profilesLoading: boolean
  selectedContactId: string | null
  selectedProfile: ContactProfile | null
  detailLoading: boolean
  onSelect: (contactId: string) => void
  onRefresh: () => void
}

function PersonaPanel({
  profiles,
  profilesLoading,
  selectedContactId,
  selectedProfile,
  detailLoading,
  onSelect,
  onRefresh,
}: PersonaPanelProps) {
  const persona = selectedProfile?.persona
  const confidencePct = Math.round(((persona?.confidence ?? 0) as number) * 100)

  // 画像完整度：统计已填充的关键维度
  const completeness = useMemo(() => {
    if (!persona) return 0
    const fields = [
      persona.background,
      persona.personality,
      persona.communication_style,
      persona.tone,
      persona.reply_pattern,
      persona.summary,
      persona.common_phrases?.length ? 'x' : '',
      persona.interests?.length ? 'x' : '',
      persona.tags?.length ? 'x' : '',
      persona.risk_signals?.length ? 'x' : '',
    ]
    const filled = fields.filter((f) => f && String(f).trim()).length
    return Math.round((filled / fields.length) * 100)
  }, [persona])

  return (
    <div className="ac-persona-grid">
      {/* 左：联系人列表 */}
      <div className="glass-card ac-persona-list slide-up stagger-1">
        <div className="ac-card-title">
          <UserOutlined /> 画像联系人
          <div className="toolbar-spacer" />
          <Button size="small" icon={<ReloadOutlined />} loading={profilesLoading} onClick={onRefresh} />
        </div>
        {profiles.length === 0 ? (
          <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="暂无画像，自动聊天运行后实时沉淀" />
        ) : (
          <List
            className="ac-persona-contacts"
            dataSource={profiles}
            renderItem={(p) => {
              const active = p.contact_id === selectedContactId
              const conf = Math.round(((p.persona?.confidence ?? 0) as number) * 100)
              return (
                <div
                  className={`ac-persona-contact${active ? ' active' : ''}`}
                  onClick={() => onSelect(p.contact_id)}
                >
                  <Avatar size={38} icon={<UserOutlined />} className="ac-persona-avatar" />
                  <div className="ac-persona-contact-main">
                    <div className="ac-persona-contact-name">{p.name || p.contact_id}</div>
                    <div className="ac-persona-contact-meta">
                      {p.platform && <Tag>{p.platform}</Tag>}
                      <span className="ac-persona-obs">{p.observations?.length ?? 0} 次观察</span>
                    </div>
                  </div>
                  <Tooltip title={`置信度 ${conf}%`}>
                    <Progress type="circle" percent={conf} size={34} strokeWidth={10} />
                  </Tooltip>
                </div>
              )
            }}
          />
        )}
      </div>

      {/* 右：画像详情（多维度实时展示） */}
      <div className="glass-card ac-persona-detail slide-up stagger-2">
        {!selectedProfile ? (
          <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="选择左侧联系人查看画像" />
        ) : (
          <>
            <div className="ac-persona-head">
              <Avatar size={52} icon={<UserOutlined />} className="ac-persona-avatar" />
              <div className="ac-persona-head-main">
                <div className="ac-persona-head-name">
                  {selectedProfile.name || selectedProfile.contact_id}
                  {detailLoading && <SyncBadge />}
                </div>
                <div className="ac-persona-head-meta">
                  {selectedProfile.platform && <Tag color="blue">{selectedProfile.platform}</Tag>}
                  <Text type="secondary" className="ac-mono">{selectedProfile.contact_id}</Text>
                  <Text type="secondary">更新 {relativeTime(selectedProfile.updated_at)}</Text>
                </div>
              </div>
            </div>

            {/* 指标条 */}
            <div className="ac-persona-metrics">
              <div className="ac-persona-metric">
                <div className="ac-persona-metric-label">画像完整度</div>
                <Progress percent={completeness} size="small" status="active" />
              </div>
              <div className="ac-persona-metric">
                <div className="ac-persona-metric-label">AI 置信度</div>
                <Progress percent={confidencePct} size="small" strokeColor="#52c41a" />
              </div>
              <div className="ac-persona-metric ac-persona-metric-num">
                <div className="ac-persona-metric-label">累计观察</div>
                <div className="ac-persona-metric-value">{selectedProfile.observations?.length ?? 0}</div>
              </div>
            </div>

            {persona?.summary && (
              <div className="ac-persona-summary">
                <BulbOutlined /> {persona.summary}
              </div>
            )}

            {/* 说话习惯 —— 重点维度 */}
            <div className="ac-persona-section-title">
              <MessageOutlined /> 说话习惯
            </div>
            <Descriptions column={1} size="small" bordered className="ac-persona-desc">
              <Descriptions.Item label="语气 tone">{persona?.tone || '—'}</Descriptions.Item>
              <Descriptions.Item label="沟通风格">{persona?.communication_style || '—'}</Descriptions.Item>
              <Descriptions.Item label="回复模式">{persona?.reply_pattern || '—'}</Descriptions.Item>
              <Descriptions.Item label="口头禅 / 常用语">
                {persona?.common_phrases?.length ? (
                  <div className="ac-tag-wrap">
                    {persona.common_phrases.map((c, i) => (
                      <Tag key={i} color="geekblue">{c}</Tag>
                    ))}
                  </div>
                ) : (
                  '—'
                )}
              </Descriptions.Item>
            </Descriptions>

            {/* 人物特征 */}
            <div className="ac-persona-section-title">
              <ProfileOutlined /> 人物特征
            </div>
            <Descriptions column={1} size="small" bordered className="ac-persona-desc">
              <Descriptions.Item label="背景">{persona?.background || '—'}</Descriptions.Item>
              <Descriptions.Item label="性格">{persona?.personality || '—'}</Descriptions.Item>
              <Descriptions.Item label={<span><HeartOutlined /> 兴趣</span>}>
                {persona?.interests?.length ? (
                  <div className="ac-tag-wrap">
                    {persona.interests.map((c, i) => (
                      <Tag key={i} color="magenta">{c}</Tag>
                    ))}
                  </div>
                ) : (
                  '—'
                )}
              </Descriptions.Item>
              <Descriptions.Item label={<span><TagsOutlined /> 标签</span>}>
                {persona?.tags?.length ? (
                  <div className="ac-tag-wrap">
                    {persona.tags.map((c, i) => (
                      <Tag key={i}>{c}</Tag>
                    ))}
                  </div>
                ) : (
                  '—'
                )}
              </Descriptions.Item>
              <Descriptions.Item label={<span><SafetyOutlined /> 风险信号</span>}>
                {persona?.risk_signals?.length ? (
                  <div className="ac-tag-wrap">
                    {persona.risk_signals.map((c, i) => (
                      <Tag key={i} color="red">{c}</Tag>
                    ))}
                  </div>
                ) : (
                  '—'
                )}
              </Descriptions.Item>
            </Descriptions>

            {/* 观察时间线 —— 实时增长 */}
            <div className="ac-persona-section-title">
              <ThunderboltOutlined /> 观察时间线（实时增长）
            </div>
            <div className="ac-persona-obs-timeline">
              {selectedProfile.observations?.length ? (
                [...selectedProfile.observations].reverse().map((o, i) => (
                  <div key={i} className="ac-persona-obs-item">
                    <span className="ac-live-dot" />
                    <div className="ac-persona-obs-main">
                      <div className="ac-persona-obs-head">
                        <Tag>{o.source || 'observe'}</Tag>
                        <span className="ac-event-time">{relativeTime(o.ts)}</span>
                      </div>
                      <div className="ac-persona-obs-content">{o.content}</div>
                    </div>
                  </div>
                ))
              ) : (
                <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="暂无观察记录" />
              )}
            </div>
          </>
        )}
      </div>
    </div>
  )
}

function SyncBadge() {
  return (
    <span className="ac-sync-badge">
      <span className="ac-live-dot" /> 更新中
    </span>
  )
}

function eventMeta(ev: MobileEvent): { icon: ReactNode; title: string; desc: string; tone: string } {
  const d = ev.data as Record<string, unknown>
  switch (ev.type) {
    case 'profile_updated':
      return { icon: <ProfileOutlined />, title: `画像更新 · ${(d.name as string) ?? ev.contact_id ?? ''}`, desc: (d.summary as string) ?? '', tone: 'info' }
    case 'suggestion':
      return { icon: <BulbOutlined />, title: '新建议', desc: String(d.suggestions ?? '').slice(0, 120), tone: 'warn' }
    case 'auto_chat':
      return {
        icon: <CommentOutlined />,
        title: `自动聊天 · ${(d.event as string) ?? ''}`,
        desc: (d.reply as string) || (d.suggestion as string) || (d.message as string) || `回合 ${d.rounds ?? 0} · 已发 ${d.replies_sent ?? 0}`,
        tone: 'success',
      }
    case 'auto_chat_watch':
      return {
        icon: <EyeOutlined />,
        title: `Watcher · ${(d.event as string) ?? ''}`,
        desc: (d.contact_name as string) ? `新联系人：${d.contact_name as string}` : (d.message as string) ?? '',
        tone: 'info',
      }
    default:
      return { icon: <ThunderboltOutlined />, title: ev.type, desc: '', tone: 'info' }
  }
}
