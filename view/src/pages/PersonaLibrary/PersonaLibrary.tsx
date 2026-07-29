import { useCallback, useEffect, useMemo, useState, type ReactNode } from 'react'
import { useSearchParams, useNavigate } from 'react-router-dom'
import {
  Typography,
  Button,
  Input,
  InputNumber,
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
  enrichPerson,
  waitForPersonaResearchTask,
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
  const [personality, setPersonality] = useState('')
  const [ageRange, setAgeRange] = useState('')
  const [sort, setSort] = useState<'confidence_desc' | 'time_desc'>('confidence_desc')

  // 详情 / 编辑
  const [drawerOpen, setDrawerOpen] = useState(false)
  const [active, setActive] = useState<Person | null>(null)
  const [editing, setEditing] = useState(false)
  const [saving, setSaving] = useState(false)
  const [enriching, setEnriching] = useState(false)
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
        personality: personality.trim(),
        age_min: ageRange ? Number(ageRange.split('-')[0]) : undefined,
        age_max: ageRange ? Number(ageRange.split('-')[1]) : undefined,
        sort,
        limit: pageSize,
        skip: (page - 1) * pageSize,
        summary_only: true,
      })
      setPersons(res.items)
      setTotal(res.total)
    } catch (e) {
      message.error(e instanceof Error ? e.message : '加载人设库失败')
    } finally {
      setLoading(false)
    }
  }, [keyword, company, industry, position, personality, ageRange, sort, page])

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

  const handleEnrich = async () => {
    if (!active) return
    let messageKey = ''
    setEnriching(true)
    try {
      const result = await enrichPerson(active.person_id)
      messageKey = `persona-enrich-${result.task_id}`
      message.loading({
        key: messageKey,
        content: `正在持续研究，当前资料版本 v${result.profile_version}`,
        duration: 0,
      })
      const task = await waitForPersonaResearchTask(result.task_id, (progress) => {
        message.loading({
          key: messageKey,
          content: progress.message || '正在持续研究',
          duration: 0,
        })
      })
      if (task.status !== 'completed') {
        throw new Error(task.error || task.message || '持续研究未完成')
      }
      const fresh = await getPerson(active.person_id)
      setActive(fresh)
      await refresh()
      message.success({
        key: messageKey,
        content: `人设已升级至 v${fresh.profile_version || result.profile_version + 1}`,
      })
    } catch (e) {
      message.error({
        key: messageKey || undefined,
        content: e instanceof Error ? e.message : '持续研究发起失败',
      })
    } finally {
      setEnriching(false)
    }
  }

  const handleCollect = async () => {
    try {
      const values = await collectForm.validateFields()
      setCollecting(true)
      const result = await collectPersona(values)
      setCollectOpen(false)
      collectForm.resetFields()
      const messageKey = `persona-generate-${result.task_id}`
      message.loading({
        key: messageKey,
        content: `已进入队列，计划生成 ${result.count} 条虚构人设`,
        duration: 0,
      })
      void waitForPersonaResearchTask(result.task_id, (progress) => {
        message.loading({
          key: messageKey,
          content: progress.message || '正在研究并生成人设',
          duration: 0,
        })
      }).then(async (task) => {
        if (task.status !== 'completed') {
          throw new Error(task.error || task.message || '人设生成未完成')
        }
        await refresh()
        message.success({
          key: messageKey,
          content: `已生成 ${task.completed_count} 条人设`,
        })
      }).catch((error: unknown) => {
        message.error({
          key: messageKey,
          content: error instanceof Error ? error.message : '人设生成失败',
        })
      })
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
              <div className="persona-name">
                {r.name || '未命名'}
                {r.is_fictional && <Tag color="purple">虚构</Tag>}
              </div>
              <div className="persona-sub">
                {[r.age ? `${r.age}岁` : r.age_range, r.position, r.company].filter(Boolean).join(' · ') || '—'}
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
        title: '一致性',
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
            AI 研究行业与岗位背景，批量生成不对应真实自然人的多维虚构人设
          </Paragraph>
        </div>
      </div>

      <div className="persona-toolbar slide-up stagger-1">
        <Input
          id="persona-keyword"
          name="persona_keyword"
          allowClear
          prefix={<SearchOutlined />}
          placeholder="搜索姓名 / 公司 / 职位 / 摘要"
          value={keyword}
          onChange={(e) => setKeyword(e.target.value)}
          onPressEnter={() => { setPage(1); refresh() }}
          className="persona-search"
        />
        <Input id="persona-company" name="persona_company" allowClear placeholder="公司" value={company} onChange={(e) => setCompany(e.target.value)} style={{ maxWidth: 160 }} />
        <Input id="persona-industry" name="persona_industry" allowClear placeholder="行业" value={industry} onChange={(e) => setIndustry(e.target.value)} style={{ maxWidth: 140 }} />
        <Input id="persona-position" name="persona_position" allowClear placeholder="职位" value={position} onChange={(e) => setPosition(e.target.value)} style={{ maxWidth: 140 }} />
        <Input id="persona-personality" name="persona_personality" allowClear placeholder="性格" value={personality} onChange={(e) => setPersonality(e.target.value)} style={{ maxWidth: 140 }} />
        <Select
          allowClear
          placeholder="年龄段"
          value={ageRange || undefined}
          onChange={(value) => setAgeRange(value || '')}
          style={{ minWidth: 110 }}
          options={[
            { label: '18-29', value: '18-29' },
            { label: '30-39', value: '30-39' },
            { label: '40-49', value: '40-49' },
            { label: '50-59', value: '50-59' },
            { label: '60-75', value: '60-75' },
          ]}
        />
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
          批量生成人设
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
          scroll={{ x: 980 }}
          pagination={{
            current: page,
            pageSize,
            total,
            showSizeChanger: false,
            hideOnSinglePage: true,
            onChange: setPage,
          }}
          locale={{ emptyText: <Empty description="暂无人设，点「批量生成人设」开始" /> }}
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
        size={480}
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
                <div className="detail-name">
                  {active.name || '未命名'}
                  {active.is_fictional && <Tag color="purple">虚构人设</Tag>}
                </div>
                <div className="detail-sub">
                  {active.position && <Tag>{active.position}</Tag>}
                  {active.company && <Tag icon={<BankOutlined />}>{active.company}</Tag>}
                </div>
              </div>
            </div>

            <Field label="摘要" value={active.summary} />
            <Field label="年龄" value={active.age ? `${active.age} 岁` : active.age_range} />
            <Field
              label="资料版本"
              value={`v${active.profile_version || 1} · ${active.research_rounds || 0} 轮研究`}
            />
            {active.generation_brief !== active.summary && (
              <Field label="生成背景" value={active.generation_brief} />
            )}

            <div className="detail-section-title">职业与生活背景</div>
            <Field label="职业背景" value={active.background} />
            <Field label="行业" value={active.industry} />
            <Field label="部门与职级" value={[active.department, active.position_level].filter(Boolean).join(' · ')} />
            <Field label="工作年限" value={active.work_years} />
            <Field label="所在地" value={active.location} />
            <Field label="地域类型" value={active.region_type} />
            <Field label="组织环境" value={active.organization_context} />
            <Field label="职业阶段" value={active.career_stage} />
            <Field label="职业路径" value={active.career_path} />
            <Field label="生活阶段" value={active.life_stage} />
            <Field label="工作场景" value={active.work_context} />
            <Field label="工作节奏" value={active.work_rhythm} />
            <Field
              label="教育经历"
              value={active.education
                ? [active.education.school, active.education.degree, active.education.major, active.education.graduation_year]
                    .filter(Boolean)
                    .join(' · ')
                : undefined}
            />

            <div className="detail-section-title">性格与行为逻辑</div>
            <Field label="性格特点" value={active.personality} />
            <Field label="决策方式" value={active.decision_style} />
            <Field label="沟通方式" value={active.communication_style} />
            <Field label="协作方式" value={active.collaboration_style} />
            <Field label="技术态度" value={active.technology_attitude} />
            <Field label="学习方式" value={active.learning_style} />
            <Field label="压力反应" value={active.stress_response} />

            <Chips icon={<HeartOutlined />} label="兴趣" items={active.interests} />
            <Chips label="信息偏好" items={active.information_preferences} />
            <Chips label="数字习惯" items={active.digital_habits} />
            <Chips label="核心动机" items={active.motivations} />
            <Chips label="阶段目标" items={active.goals} />
            <Chips label="具体痛点" items={active.pain_points} />
            <Chips label="价值取向" items={active.values} />
            <Chips label="行为模式" items={active.behavior_patterns} />
            <Chips label="内容偏好" items={active.content_preferences} />
            <Chips label="选择考虑" items={active.purchase_considerations} />
            <Chips icon={<TagsOutlined />} label="标签" items={active.tags} />
            <Chips icon={<WarningOutlined />} label="风险点" items={active.risk_signals} />

            <div className="detail-section-title">背景参考来源</div>
            <div className="persona-sources">
              {(active.research_evidence || []).map((item, i) => (
                <div key={`research-evidence-${i}`} className="source-item">
                  <Tag color="cyan">{item.dimension}</Tag>
                  <div>
                    <Text>{item.finding}</Text>
                    <div><Text type="secondary">适用：{item.applicability}</Text></div>
                    {item.source_urls.map((url) => (
                      <div key={url}>
                        <Typography.Link href={url} target="_blank" rel="noreferrer">{url}</Typography.Link>
                      </div>
                    ))}
                  </div>
                </div>
              ))}
              {(active.source_urls || []).map((source, i) => (
                <div key={`source-${i}`} className="source-item">
                  <Tag color="blue">参考</Tag>
                  {/^https?:\/\//i.test(source) ? (
                    <Typography.Link href={source} target="_blank" rel="noreferrer">
                      {source}
                    </Typography.Link>
                  ) : (
                    <Text>{source}</Text>
                  )}
                </div>
              ))}
              {(active.evidence || []).map((evidence, i) => (
                <div key={`evidence-${i}`} className="source-item">
                  <Tag color="green">依据</Tag>
                  <Text type="secondary">{evidence}</Text>
                </div>
              ))}
              {!active.source_urls?.length && !active.evidence?.length && (
                <Text type="secondary">暂无背景参考来源</Text>
              )}
            </div>

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
              block
              icon={<ReloadOutlined />}
              loading={enriching}
              style={{ marginTop: 16 }}
              onClick={handleEnrich}
            >
              持续研究并升级
            </Button>
            <Button
              type="primary"
              block
              icon={<RobotOutlined />}
              style={{ marginTop: 12 }}
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
        title={<span><ThunderboltOutlined /> AI 批量生成虚构人设</span>}
        open={collectOpen}
        onCancel={() => setCollectOpen(false)}
        onOk={handleCollect}
        confirmLoading={collecting}
        okText="开始研究并生成"
        cancelText="取消"
      >
        <Form form={collectForm} layout="vertical">
          <Form.Item
            name="background"
            label="总体背景设定"
            rules={[{ required: true, message: '请输入背景设定' }]}
          >
            <Input.TextArea
              autoSize={{ minRows: 3, maxRows: 6 }}
              placeholder="例如：覆盖企业数字化、业务运营和公共服务岗位，用于内容演练；人物必须完全虚构"
            />
          </Form.Item>
          <Form.Item name="count" label="生成数量" initialValue={36}>
            <InputNumber min={1} max={60} precision={0} style={{ width: '100%' }} />
          </Form.Item>
          <Form.Item name="industries" label="行业维度（留空由 AI 扩展）">
            <Select mode="tags" placeholder="如 制造业、金融、医疗、教育" />
          </Form.Item>
          <Form.Item name="age_ranges" label="年龄提示（留空由 AI 自主探索）">
            <Select mode="tags" placeholder="如 22-29、30-39、40-49" />
          </Form.Item>
          <Form.Item name="personalities" label="性格提示（留空由 AI 自主探索）">
            <Select mode="tags" placeholder="如 谨慎理性、外向主动、稳定协作" />
          </Form.Item>
          <Form.Item name="company" label="组织设定（可选）"><Input placeholder="留空可使用虚构组织" /></Form.Item>
          <Form.Item name="position" label="职位设定（可选）"><Input placeholder="留空按行业分布" /></Form.Item>
          <Form.Item name="extra" label="其他约束（可选）"><Input.TextArea autoSize={{ minRows: 2, maxRows: 3 }} placeholder="地区、职级比例、生活阶段等约束" /></Form.Item>
        </Form>
        <div className="modal-hint">
          <ThunderboltOutlined /> AI 自主规划研究分片、真实爬取公网并审校人物逻辑；不会采集真人身份和联系方式。
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
