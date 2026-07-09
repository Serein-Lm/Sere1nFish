import { useCallback, useEffect, useRef, useState, type ReactNode } from 'react'
import {
  Typography,
  Button,
  Input,
  Select,
  Tag,
  Drawer,
  Modal,
  Popconfirm,
  message,
  Empty,
  Tooltip,
  Table,
  type TableProps,
} from 'antd'
import {
  ProfileOutlined,
  ReloadOutlined,
  SearchOutlined,
  ThunderboltOutlined,
  EditOutlined,
  DeleteOutlined,
  EyeOutlined,
  SaveOutlined,
  CloseOutlined,
  TagsOutlined,
  HeartOutlined,
  MessageOutlined,
} from '@ant-design/icons'
import {
  listProfiles,
  getProfile,
  analyzeProfile,
  upsertProfile,
  deleteProfile,
  getPool,
  subscribeEvents,
  relativeTime,
  type ContactProfile,
  type Persona,
} from '../../services/mobileService'
import './MobileProfiles.css'

const { Title, Paragraph, Text } = Typography

interface MobileProfilesProps {
  embedded?: boolean
}

interface DeviceOpt {
  label: string
  value: string
}

const emptyPersona: Persona = {
  background: '',
  personality: '',
  communication_style: '',
  summary: '',
  interests: [],
  tags: [],
}

export default function MobileProfiles({ embedded = false }: MobileProfilesProps) {
  const [profiles, setProfiles] = useState<ContactProfile[]>([])
  const [loading, setLoading] = useState(false)
  const [search, setSearch] = useState('')
  const [deviceFilter, setDeviceFilter] = useState<string | undefined>()
  const [devices, setDevices] = useState<DeviceOpt[]>([])

  const [drawerOpen, setDrawerOpen] = useState(false)
  const [active, setActive] = useState<ContactProfile | null>(null)
  const [editing, setEditing] = useState(false)
  const [form, setForm] = useState<{ name: string; platform: string; persona: Persona }>({
    name: '',
    platform: '',
    persona: emptyPersona,
  })
  const [saving, setSaving] = useState(false)

  const [analyzeOpen, setAnalyzeOpen] = useState(false)
  const [az, setAz] = useState({ device_id: '', contact_id: '', name: '', platform: '微信' })
  const [analyzing, setAnalyzing] = useState(false)

  const refresh = useCallback(async () => {
    setLoading(true)
    try {
      const list = await listProfiles(deviceFilter)
      setProfiles(list)
    } catch (e) {
      message.error(e instanceof Error ? e.message : '加载画像失败')
    } finally {
      setLoading(false)
    }
  }, [deviceFilter])

  useEffect(() => {
    refresh()
  }, [refresh])

  useEffect(() => {
    getPool()
      .then((r) => setDevices(r.devices.map((d) => ({ label: d.model || d.device_id, value: d.device_id }))))
      .catch(() => {})
  }, [])

  // profile_updated 实时刷新
  const activeRef = useRef<string | null>(null)
  activeRef.current = active?.contact_id ?? null
  useEffect(() => {
    let stop = false
    const ctrl = new AbortController()
    ;(async () => {
      let wait = 1000
      while (!stop && !ctrl.signal.aborted) {
        try {
          await subscribeEvents({ types: ['profile_updated'] }, (ev) => {
            refresh()
            if (ev.contact_id && ev.contact_id === activeRef.current) {
              getProfile(ev.contact_id).then(setActive).catch(() => {})
            }
          }, ctrl.signal)
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
  }, [refresh])

  const openDetail = async (p: ContactProfile) => {
    setActive(p)
    setEditing(false)
    setDrawerOpen(true)
    try {
      const fresh = await getProfile(p.contact_id)
      setActive(fresh)
    } catch {
      /* 用列表数据兜底 */
    }
  }

  const startEdit = () => {
    if (!active) return
    setForm({
      name: active.name ?? '',
      platform: active.platform ?? '',
      persona: { ...emptyPersona, ...active.persona },
    })
    setEditing(true)
  }

  const saveEdit = async () => {
    if (!active) return
    setSaving(true)
    try {
      const updated = await upsertProfile(active.contact_id, {
        name: form.name,
        platform: form.platform,
        persona: form.persona,
      })
      message.success('画像已保存')
      setActive(updated)
      setEditing(false)
      refresh()
    } catch (e) {
      message.error(e instanceof Error ? e.message : '保存失败')
    } finally {
      setSaving(false)
    }
  }

  const handleDelete = async (contactId: string) => {
    try {
      await deleteProfile(contactId)
      message.success('已删除画像')
      if (active?.contact_id === contactId) setDrawerOpen(false)
      refresh()
    } catch (e) {
      message.error(e instanceof Error ? e.message : '删除失败')
    }
  }

  const handleAnalyze = async () => {
    if (!az.device_id || !az.contact_id) {
      message.warning('请填写设备与 contact_id')
      return
    }
    setAnalyzing(true)
    try {
      const doc = await analyzeProfile(az)
      message.success('识别完成，已沉淀画像')
      setAnalyzeOpen(false)
      refresh()
      openDetail(doc)
    } catch (e) {
      message.error(e instanceof Error ? e.message : '识别失败')
    } finally {
      setAnalyzing(false)
    }
  }

  const kw = search.trim().toLowerCase()
  const filtered = profiles.filter(
    (p) =>
      !kw ||
      (p.name || '').toLowerCase().includes(kw) ||
      p.contact_id.toLowerCase().includes(kw) ||
      (p.persona?.summary || '').toLowerCase().includes(kw),
  )

  const columns: TableProps<ContactProfile>['columns'] = [
    {
      title: '联系人',
      dataIndex: 'name',
      key: 'name',
      render: (_, r) => (
        <div className="profile-name-cell">
          <div className="profile-avatar">{(r.name || r.contact_id || '?').slice(0, 1)}</div>
          <div className="profile-name-info">
            <div className="profile-name">{r.name || '未命名'}</div>
            <div className="profile-cid">{r.contact_id}</div>
          </div>
        </div>
      ),
    },
    {
      title: '平台',
      dataIndex: 'platform',
      key: 'platform',
      width: 90,
      render: (v) => (v ? <Tag>{v}</Tag> : <Text type="secondary">—</Text>),
    },
    {
      title: '画像摘要',
      key: 'summary',
      render: (_, r) => (
        <Text className="profile-summary" type="secondary">
          {r.persona?.summary || '暂无摘要'}
        </Text>
      ),
    },
    {
      title: '标签',
      key: 'tags',
      width: 200,
      render: (_, r) => (
        <div className="profile-tags">
          {(r.persona?.tags || []).slice(0, 3).map((t) => (
            <span key={t} className="custom-tag">
              {t}
            </span>
          ))}
        </div>
      ),
    },
    {
      title: '更新',
      dataIndex: 'updated_at',
      key: 'updated_at',
      width: 110,
      render: (v) => <Text type="secondary">{relativeTime(v)}</Text>,
    },
    {
      title: '操作',
      key: 'action',
      width: 130,
      render: (_, r) => (
        <div className="profile-actions">
          <Tooltip title="查看">
            <Button type="text" size="small" icon={<EyeOutlined />} onClick={() => openDetail(r)} />
          </Tooltip>
          <Popconfirm title="确认删除该画像？" onConfirm={() => handleDelete(r.contact_id)} okText="删除" cancelText="取消">
            <Button type="text" size="small" danger icon={<DeleteOutlined />} />
          </Popconfirm>
        </div>
      ),
    },
  ]

  return (
    <div className={`mobile-profiles${embedded ? ' mobile-module' : ' page-container'} fade-in`}>
      <div className="pc-header slide-up">
        <div className="pc-header-text">
          <Title level={2} className="page-title">
            <ProfileOutlined /> 联系人画像库
          </Title>
          <Paragraph className="page-description">
            读屏识别 · 结构化沉淀对方背景/性格/兴趣 · 越聊越准，自动注入话术生成
          </Paragraph>
        </div>
      </div>

      <div className="device-toolbar slide-up stagger-1">
        <Input
          allowClear
          prefix={<SearchOutlined />}
          placeholder="搜索姓名 / contact_id / 摘要"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          className="device-search"
        />
        <Select
          allowClear
          placeholder="按设备筛选"
          style={{ minWidth: 180 }}
          options={devices}
          value={deviceFilter}
          onChange={setDeviceFilter}
        />
        <div className="toolbar-spacer" />
        <Button type="primary" icon={<ThunderboltOutlined />} onClick={() => setAnalyzeOpen(true)}>
          识别沉淀
        </Button>
        <Tooltip title="刷新">
          <Button icon={<ReloadOutlined />} loading={loading} onClick={refresh} />
        </Tooltip>
      </div>

      <div className="glass-card profile-table-card slide-up stagger-2">
        <Table<ContactProfile>
          rowKey="contact_id"
          columns={columns}
          dataSource={filtered}
          loading={loading}
          pagination={{ pageSize: 10, hideOnSinglePage: true }}
          locale={{ emptyText: <Empty description="暂无画像，点「识别沉淀」开始" /> }}
        />
      </div>

      {/* 详情 / 编辑 抽屉 */}
      <Drawer
        title={
          <div className="drawer-title">
            <ProfileOutlined /> {active?.name || active?.contact_id || '画像详情'}
          </div>
        }
        open={drawerOpen}
        onClose={() => setDrawerOpen(false)}
        width={460}
        extra={
          editing ? (
            <div className="drawer-extra">
              <Button icon={<CloseOutlined />} onClick={() => setEditing(false)}>
                取消
              </Button>
              <Button type="primary" icon={<SaveOutlined />} loading={saving} onClick={saveEdit}>
                保存
              </Button>
            </div>
          ) : (
            <Button icon={<EditOutlined />} onClick={startEdit}>
              编辑
            </Button>
          )
        }
      >
        {active && !editing && (
          <div className="profile-detail">
            <div className="detail-head">
              <div className="profile-avatar lg">{(active.name || active.contact_id || '?').slice(0, 1)}</div>
              <div>
                <div className="detail-name">{active.name || '未命名'}</div>
                <div className="detail-cid">
                  {active.platform && <Tag>{active.platform}</Tag>}
                  {active.contact_id}
                </div>
              </div>
            </div>

            <PersonaField icon={<MessageOutlined />} label="摘要" value={active.persona?.summary} />
            <PersonaField label="背景" value={active.persona?.background} />
            <PersonaField label="性格" value={active.persona?.personality} />
            <PersonaField label="沟通风格" value={active.persona?.communication_style} />

            <div className="detail-chips">
              <div className="detail-chips-label">
                <HeartOutlined /> 兴趣
              </div>
              <div className="profile-tags">
                {(active.persona?.interests || []).length
                  ? active.persona.interests.map((t) => <span key={t} className="custom-tag">{t}</span>)
                  : <Text type="secondary">—</Text>}
              </div>
            </div>
            <div className="detail-chips">
              <div className="detail-chips-label">
                <TagsOutlined /> 标签
              </div>
              <div className="profile-tags">
                {(active.persona?.tags || []).length
                  ? active.persona.tags.map((t) => <span key={t} className="custom-tag">{t}</span>)
                  : <Text type="secondary">—</Text>}
              </div>
            </div>

            <div className="detail-section-title">观察记录（{active.observations?.length || 0}）</div>
            <div className="observations">
              {(active.observations || []).slice(-12).reverse().map((o, i) => (
                <div key={i} className="observation-item">
                  <div className="observation-time">{relativeTime(o.ts)}</div>
                  <div className="observation-content">{o.content}</div>
                </div>
              ))}
              {!active.observations?.length && <Text type="secondary">暂无观察记录</Text>}
            </div>

            <Popconfirm title="确认删除该画像？" onConfirm={() => handleDelete(active.contact_id)} okText="删除" cancelText="取消">
              <Button danger block icon={<DeleteOutlined />} style={{ marginTop: 16 }}>
                删除画像
              </Button>
            </Popconfirm>
          </div>
        )}

        {active && editing && (
          <div className="profile-edit">
            <label className="field-label">姓名</label>
            <Input value={form.name} onChange={(e) => setForm((f) => ({ ...f, name: e.target.value }))} />
            <label className="field-label">平台</label>
            <Input value={form.platform} onChange={(e) => setForm((f) => ({ ...f, platform: e.target.value }))} />
            {(['summary', 'background', 'personality', 'communication_style'] as const).map((k) => (
              <div key={k}>
                <label className="field-label">
                  {{ summary: '摘要', background: '背景', personality: '性格', communication_style: '沟通风格' }[k]}
                </label>
                <Input.TextArea
                  value={form.persona[k] ?? ''}
                  onChange={(e) => setForm((f) => ({ ...f, persona: { ...f.persona, [k]: e.target.value } }))}
                  autoSize={{ minRows: 2, maxRows: 4 }}
                />
              </div>
            ))}
            <label className="field-label">兴趣</label>
            <Select
              mode="tags"
              style={{ width: '100%' }}
              value={form.persona.interests}
              onChange={(v) => setForm((f) => ({ ...f, persona: { ...f.persona, interests: v } }))}
              placeholder="回车添加"
            />
            <label className="field-label">标签</label>
            <Select
              mode="tags"
              style={{ width: '100%' }}
              value={form.persona.tags}
              onChange={(v) => setForm((f) => ({ ...f, persona: { ...f.persona, tags: v } }))}
              placeholder="回车添加"
            />
          </div>
        )}
      </Drawer>

      {/* 识别沉淀弹窗 */}
      <Modal
        title={
          <span>
            <ThunderboltOutlined /> 读屏识别并沉淀画像
          </span>
        }
        open={analyzeOpen}
        onCancel={() => setAnalyzeOpen(false)}
        onOk={handleAnalyze}
        confirmLoading={analyzing}
        okText="开始识别"
        cancelText="取消"
      >
        <div className="analyze-form">
          <label className="field-label">设备</label>
          <Select
            placeholder="选择在线设备"
            style={{ width: '100%' }}
            options={devices}
            value={az.device_id || undefined}
            onChange={(v) => setAz((a) => ({ ...a, device_id: v }))}
          />
          <label className="field-label">contact_id（唯一键，建议 平台:对方标识）</label>
          <Input value={az.contact_id} onChange={(e) => setAz((a) => ({ ...a, contact_id: e.target.value }))} placeholder="如 wechat:张三" />
          <label className="field-label">姓名（可选）</label>
          <Input value={az.name} onChange={(e) => setAz((a) => ({ ...a, name: e.target.value }))} placeholder="张三" />
          <label className="field-label">平台（可选）</label>
          <Input value={az.platform} onChange={(e) => setAz((a) => ({ ...a, platform: e.target.value }))} placeholder="微信" />
        </div>
        <div className="modal-hint">
          <ThunderboltOutlined /> 将读取设备当前聊天界面，由视觉模型结构化提取并合并入库。
        </div>
      </Modal>
    </div>
  )
}

function PersonaField({ icon, label, value }: { icon?: ReactNode; label: string; value?: string }) {
  return (
    <div className="persona-field">
      <div className="persona-field-label">
        {icon} {label}
      </div>
      <div className="persona-field-value">{value || <span className="muted">—</span>}</div>
    </div>
  )
}
