import { useCallback, useEffect, useMemo, useState, type ReactNode } from 'react'
import { useSearchParams, useNavigate } from 'react-router-dom'
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
  Form,
  Space,
  type TableProps,
} from 'antd'
import {
  TeamOutlined,
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
  BankOutlined,
  WarningOutlined,
  RobotOutlined,
} from '@ant-design/icons'
import {
  listPersons,
  getPerson,
  collectPersona,
  upsertPerson,
  deletePerson,
  type Person,
} from '../../services/personaService'
import './PersonaLibrary.css'

const { Title, Paragraph, Text } = Typography

export default function PersonaLibrary() {
  const [persons, setPersons] = useState<Person[]>([])
  const [total, setTotal] = useState(0)
  const [loading, setLoading] = useState(false)
  const [page, setPage] = useState(1)
  const pageSize = 10

  // 筛选条件
  const [keyword, setKeyword] = useState('')
  const [company, setCompany] = useState('')
  const [industry, setIndustry] = useState('')
  const [position, setPosition] = useState('')
  const [sort, setSort] = useState<'confidence_desc' | 'time_desc'>('confidence_desc')

  // 详情 / 编辑
  const [drawerOpen, setDrawerOpen] = useState(false)
  const [active, setActive] = useState<Person | null>(null)
  const [editing, setEditing] = useState(false)
  const [saving, setSaving] = useState(false)
  const [editForm] = Form.useForm()

  // 采集
  const [collectOpen, setCollectOpen] = useState(false)
  const [collecting, setCollecting] = useState(false)
  const [collectForm] = Form.useForm()

  const refresh = useCallback(async () => {
    setLoading(true)
    try {
      const res = await listPersons({
        keyword: keyword.trim(),
        company: company.trim(),
        industry: industry.trim(),
        position: position.trim(),
        sort,
        limit: pageSize,
        skip: (page - 1) * pageSize,
      })
      setPersons(res.items)
      setTotal(res.total)
    } catch (e) {
      message.error(e instanceof Error ? e.message : '加载人设库失败')
    } finally {
      setLoading(false)
    }
  }, [keyword, company, industry, position, sort, page])

  useEffect(() => {
    refresh()
  }, [refresh])

  const [searchParams, setSearchParams] = useSearchParams()
  const navigate = useNavigate()

  // 带该人物跳转到 AI 中枢并自动引用，用户到中台后直接说出需求
  const jumpToHubWithPerson = (p: Person) => {
    const params = new URLSearchParams({ ref_person: p.person_id, label: p.name || p.person_id })
    const desc = [p.company, p.position].filter(Boolean).join(' · ')
    if (desc) params.set('desc', desc)
    navigate(`/phishing?${params.toString()}`)
  }

  // 处理来自 AI 中枢的跳转：?person_id=... 打开详情，?company=... 预填筛选
  useEffect(() => {
    const personId = searchParams.get('person_id')
    const companyParam = searchParams.get('company')
    if (personId) {
      getPerson(personId)
        .then(p => {
          setActive(p)
          setEditing(false)
          setDrawerOpen(true)
        })
        .catch(() => message.error('未找到该人物'))
      searchParams.delete('person_id')
      setSearchParams(searchParams, { replace: true })
    } else if (companyParam) {
      setCompany(companyParam)
      setPage(1)
      searchParams.delete('company')
      setSearchParams(searchParams, { replace: true })
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [searchParams])

  const openDetail = async (p: Person) => {
    setActive(p)
    setEditing(false)
    setDrawerOpen(true)
    try {
      const fresh = await getPerson(p.person_id)
      setActive(fresh)
    } catch {
      /* 用列表数据兜底 */
    }
  }

  const startEdit = () => {
    if (!active) return
    editForm.setFieldsValue({
      name: active.name,
      company: active.company,
      industry: active.industry,
      position: active.position,
      location: active.location,
      background: active.background,
      personality: active.personality,
      summary: active.summary,
      interests: active.interests ?? [],
      tags: active.tags ?? [],
      risk_signals: active.risk_signals ?? [],
    })
    setEditing(true)
  }

  const saveEdit = async () => {
    if (!active) return
    try {
      const values = await editForm.validateFields()
      setSaving(true)
      const updated = await upsertPerson(active.person_id, values)
      message.success('人设已保存')
      setActive(updated)
      setEditing(false)
      refresh()
    } catch (e) {
      if (e instanceof Error) message.error(e.message)
    } finally {
      setSaving(false)
    }
  }

  const handleDelete = async (personId: string) => {
    try {
      await deletePerson(personId)
      message.success('已删除人设')
      if (active?.person_id === personId) setDrawerOpen(false)
      refresh()
    } catch (e) {
      message.error(e instanceof Error ? e.message : '删除失败')
    }
  }

  const handleCollect = async () => {
    try {
      const values = await collectForm.validateFields()
      setCollecting(true)
      await collectPersona(values)
      message.success('已发起采集，稍后刷新查看结果')
      setCollectOpen(false)
      collectForm.resetFields()
    } catch (e) {
      if (e instanceof Error) message.error(e.message)
    } finally {
      setCollecting(false)
    }
  }

  const columns: TableProps<Person>['columns'] = useMemo(
    () => [
      {
        title: '人物',
        dataIndex: 'name',
        key: 'name',
        render: (_, r) => (
          <div className="persona-name-cell">
            <div className="persona-avatar">{(r.name || '?').slice(0, 1)}</div>
            <div className="persona-name-info">
              <div className="persona-name">{r.name || '未命名'}</div>
              <div className="persona-sub">
                {[r.position, r.company].filter(Boolean).join(' · ') || '—'}
              </div>
            </div>
          </div>
        ),
      },
      {
        title: '行业',
        dataIndex: 'industry',
        key: 'industry',
        width: 120,
        render: (v) => (v ? <Tag>{v}</Tag> : <Text type="secondary">—</Text>),
      },
      {
        title: '摘要',
        key: 'summary',
        render: (_, r) => (
          <Text className="persona-summary" type="secondary">
            {r.summary || r.background || '暂无摘要'}
          </Text>
        ),
      },
      {
        title: '标签',
        key: 'tags',
        width: 200,
        render: (_, r) => (
          <div className="persona-tags">
            {(r.tags || []).slice(0, 3).map((t) => (
              <span key={t} className="custom-tag">
                {t}
              </span>
            ))}
          </div>
        ),
      },
      {
        title: '置信度',
        dataIndex: 'confidence',
        key: 'confidence',
        width: 90,
        render: (v: number | undefined) =>
          v != null ? <Tag color={v >= 0.7 ? 'green' : v >= 0.4 ? 'orange' : 'default'}>{Math.round(v * 100)}%</Tag> : <Text type="secondary">—</Text>,
      },
      {
        title: '操作',
        key: 'action',
        width: 150,
        render: (_, r) => (
          <div className="persona-actions">
            <Tooltip title="查看">
              <Button type="text" size="small" icon={<EyeOutlined />} onClick={() => openDetail(r)} />
            </Tooltip>
            <Tooltip title="引用到 AI 中枢">
              <Button type="text" size="small" icon={<RobotOutlined />} onClick={() => jumpToHubWithPerson(r)} />
            </Tooltip>
            <Popconfirm title="确认删除该人设？" onConfirm={() => handleDelete(r.person_id)} okText="删除" cancelText="取消">
              <Button type="text" size="small" danger icon={<DeleteOutlined />} />
            </Popconfirm>
          </div>
        ),
      },
    ],
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [active],
  )

  return (
    <div className="persona-library page-container fade-in">
      <div className="pc-header slide-up">
        <div className="pc-header-text">
          <Title level={2} className="page-title">
            <TeamOutlined /> 人设库
          </Title>
          <Paragraph className="page-description">
            全局人物真源 · AI 浏览器采集结构化档案 · 跨项目复用，供 AI 中枢检索
          </Paragraph>
        </div>
      </div>

      <div className="persona-toolbar slide-up stagger-1">
        <Input
          allowClear
          prefix={<SearchOutlined />}
          placeholder="搜索姓名 / 公司 / 职位 / 摘要"
          value={keyword}
          onChange={(e) => setKeyword(e.target.value)}
          onPressEnter={() => { setPage(1); refresh() }}
          className="persona-search"
        />
        <Input allowClear placeholder="公司" value={company} onChange={(e) => setCompany(e.target.value)} style={{ maxWidth: 160 }} />
        <Input allowClear placeholder="行业" value={industry} onChange={(e) => setIndustry(e.target.value)} style={{ maxWidth: 140 }} />
        <Input allowClear placeholder="职位" value={position} onChange={(e) => setPosition(e.target.value)} style={{ maxWidth: 140 }} />
        <Select
          value={sort}
          onChange={setSort}
          style={{ minWidth: 130 }}
          options={[
            { label: '按置信度', value: 'confidence_desc' },
            { label: '按更新时间', value: 'time_desc' },
          ]}
        />
        <div className="toolbar-spacer" />
        <Button type="primary" icon={<ThunderboltOutlined />} onClick={() => setCollectOpen(true)}>
          采集人设
        </Button>
        <Tooltip title="刷新">
          <Button icon={<ReloadOutlined />} loading={loading} onClick={refresh} />
        </Tooltip>
      </div>

      <div className="glass-card persona-table-card slide-up stagger-2">
        <Table<Person>
          rowKey="person_id"
          columns={columns}
          dataSource={persons}
          loading={loading}
          pagination={{
            current: page,
            pageSize,
            total,
            showSizeChanger: false,
            hideOnSinglePage: true,
            onChange: setPage,
          }}
          locale={{ emptyText: <Empty description="暂无人设，点「采集人设」开始" /> }}
        />
      </div>

      {/* 详情 / 编辑 抽屉 */}
      <Drawer
        title={
          <div className="drawer-title">
            <TeamOutlined /> {active?.name || '人设详情'}
          </div>
        }
        open={drawerOpen}
        onClose={() => setDrawerOpen(false)}
        width={480}
        extra={
          editing ? (
            <Space>
              <Button icon={<CloseOutlined />} onClick={() => setEditing(false)}>取消</Button>
              <Button type="primary" icon={<SaveOutlined />} loading={saving} onClick={saveEdit}>保存</Button>
            </Space>
          ) : (
            <Button icon={<EditOutlined />} onClick={startEdit}>编辑</Button>
          )
        }
      >
        {active && !editing && (
          <div className="persona-detail">
            <div className="detail-head">
              <div className="persona-avatar lg">{(active.name || '?').slice(0, 1)}</div>
              <div>
                <div className="detail-name">{active.name || '未命名'}</div>
                <div className="detail-sub">
                  {active.position && <Tag>{active.position}</Tag>}
                  {active.company && <Tag icon={<BankOutlined />}>{active.company}</Tag>}
                </div>
              </div>
            </div>

            <Field label="摘要" value={active.summary} />
            <Field label="职业背景" value={active.background} />
            <Field label="性格特点" value={active.personality} />
            <Field label="行业" value={active.industry} />
            <Field label="所在地" value={active.location} />
            <Field label="公司根域名" value={active.company_root_domain} />

            <Chips icon={<HeartOutlined />} label="兴趣" items={active.interests} />
            <Chips icon={<TagsOutlined />} label="标签" items={active.tags} />
            <Chips icon={<WarningOutlined />} label="风险点" items={active.risk_signals} />

            <div className="detail-section-title">来源溯源（{active.sources?.length || 0}）</div>
            <div className="persona-sources">
              {(active.sources || []).slice(-8).reverse().map((s, i) => (
                <div key={i} className="source-item">
                  <Tag>{s.source || '未知'}</Tag>
                  <Text type="secondary">{s.project_id || '全局'}{s.finding_id ? ` · ${s.finding_id}` : ''}</Text>
                </div>
              ))}
              {!active.sources?.length && <Text type="secondary">暂无来源记录</Text>}
            </div>

            <Button
              type="primary"
              block
              icon={<RobotOutlined />}
              style={{ marginTop: 16 }}
              onClick={() => jumpToHubWithPerson(active)}
            >
              引用到 AI 中枢并提出需求
            </Button>
            <Popconfirm title="确认删除该人设？" onConfirm={() => handleDelete(active.person_id)} okText="删除" cancelText="取消">
              <Button danger block icon={<DeleteOutlined />} style={{ marginTop: 12 }}>删除人设</Button>
            </Popconfirm>
          </div>
        )}

        {active && editing && (
          <Form form={editForm} layout="vertical" className="persona-edit">
            <Form.Item name="name" label="姓名" rules={[{ required: true, message: '请输入姓名' }]}>
              <Input />
            </Form.Item>
            <Form.Item name="company" label="公司"><Input /></Form.Item>
            <Form.Item name="industry" label="行业"><Input /></Form.Item>
            <Form.Item name="position" label="职位"><Input /></Form.Item>
            <Form.Item name="location" label="所在地"><Input /></Form.Item>
            <Form.Item name="summary" label="摘要"><Input.TextArea autoSize={{ minRows: 2, maxRows: 4 }} /></Form.Item>
            <Form.Item name="background" label="职业背景"><Input.TextArea autoSize={{ minRows: 2, maxRows: 4 }} /></Form.Item>
            <Form.Item name="personality" label="性格特点"><Input.TextArea autoSize={{ minRows: 2, maxRows: 3 }} /></Form.Item>
            <Form.Item name="interests" label="兴趣"><Select mode="tags" placeholder="回车添加" /></Form.Item>
            <Form.Item name="tags" label="标签"><Select mode="tags" placeholder="回车添加" /></Form.Item>
            <Form.Item name="risk_signals" label="风险点"><Select mode="tags" placeholder="回车添加" /></Form.Item>
          </Form>
        )}
      </Drawer>

      {/* 采集人设弹窗 */}
      <Modal
        title={<span><ThunderboltOutlined /> AI 采集人设</span>}
        open={collectOpen}
        onCancel={() => setCollectOpen(false)}
        onOk={handleCollect}
        confirmLoading={collecting}
        okText="开始采集"
        cancelText="取消"
      >
        <Form form={collectForm} layout="vertical">
          <Form.Item name="name" label="姓名" rules={[{ required: true, message: '请输入人物姓名' }]}>
            <Input placeholder="如 张三" />
          </Form.Item>
          <Form.Item name="company" label="公司（可选）"><Input placeholder="所属公司" /></Form.Item>
          <Form.Item name="position" label="职位（可选）"><Input placeholder="职位" /></Form.Item>
          <Form.Item name="extra" label="其他线索（可选）"><Input.TextArea autoSize={{ minRows: 2, maxRows: 3 }} placeholder="有助于定位人物的其他信息" /></Form.Item>
        </Form>
        <div className="modal-hint">
          <ThunderboltOutlined /> 将由 AI 浏览器搜索公开渠道，结构化提取并增量归并入库。
        </div>
      </Modal>
    </div>
  )
}

function Field({ label, value }: { label: string; value?: string }) {
  return (
    <div className="persona-field">
      <div className="persona-field-label">{label}</div>
      <div className="persona-field-value">{value || <span className="muted">—</span>}</div>
    </div>
  )
}

function Chips({ icon, label, items }: { icon?: ReactNode; label: string; items?: string[] }) {
  return (
    <div className="detail-chips">
      <div className="detail-chips-label">{icon} {label}</div>
      <div className="persona-tags">
        {items?.length ? items.map((t) => <span key={t} className="custom-tag">{t}</span>) : <Text type="secondary">—</Text>}
      </div>
    </div>
  )
}
