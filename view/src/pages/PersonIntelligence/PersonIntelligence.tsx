import { useCallback, useEffect, useMemo, useState, type ReactNode } from 'react'
import { useNavigate, useSearchParams } from 'react-router-dom'
import {
  Button,
  Descriptions,
  Drawer,
  Empty,
  Flex,
  Input,
  message,
  Progress,
  Select,
  Space,
  Steps,
  Table,
  Tabs,
  Tag,
  Tooltip,
  Typography,
  type TableProps,
} from 'antd'
import {
  CopyOutlined,
  DownloadOutlined,
  EyeOutlined,
  GlobalOutlined,
  LinkOutlined,
  ReloadOutlined,
  RobotOutlined,
  SafetyCertificateOutlined,
  SearchOutlined,
} from '@ant-design/icons'
import {
  getPersonIntelligence,
  listPersonIntelligence,
  type PersonIntelligence as Intelligence,
} from '../../services/personIntelligenceService'
import { downloadWithAuth } from '../../services/http'
import './PersonIntelligence.css'

const { Text, Title, Paragraph } = Typography

const LINEAGE_STAGES = [
  { key: 'evidence', title: '公开事实', types: ['source', 'evidence'] },
  { key: 'signal', title: '当前信号', types: ['signal'] },
  { key: 'persona', title: '匹配人设', types: ['persona'] },
  { key: 'scenario', title: '沟通场景', types: ['scenario'] },
  { key: 'copywriting', title: '成品话术', types: ['copywriting'] },
  { key: 'artifact', title: '交付产物', types: ['artifact'] },
]

const RELATION_LABELS: Record<string, string> = {
  supports: '支撑',
  describes: '描述',
  contextualizes: '形成时机',
  matched_to: '匹配',
  informs: '用于决策',
  targets: '面向',
  produces: '生成',
  grounds: '提供依据',
  documented_by: '归档为',
  affiliated_with: '任职于',
}

function IntelRow({ title, description, actions }: { title: ReactNode; description?: ReactNode; actions?: ReactNode }) {
  return (
    <div className="intel-row">
      <div className="intel-row-copy">
        <div className="intel-row-title">{title}</div>
        {description && <div className="intel-row-description">{description}</div>}
      </div>
      {actions && <div className="intel-row-actions">{actions}</div>}
    </div>
  )
}

function formatDate(value?: string): string {
  if (!value) return '-'
  const date = new Date(value)
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString('zh-CN')
}

function displayValue(value: unknown): string {
  if (Array.isArray(value)) return value.map(displayValue).filter(Boolean).join('、')
  if (value && typeof value === 'object') {
    return Object.entries(value as Record<string, unknown>)
      .map(([key, item]) => `${key}：${displayValue(item)}`)
      .filter(item => !item.endsWith('：'))
      .join('；')
  }
  return value == null ? '' : String(value)
}

export default function PersonIntelligence() {
  const [items, setItems] = useState<Intelligence[]>([])
  const [total, setTotal] = useState(0)
  const [loading, setLoading] = useState(false)
  const [page, setPage] = useState(1)
  const [keyword, setKeyword] = useState('')
  const [organization, setOrganization] = useState('')
  const [sort, setSort] = useState<'updated_desc' | 'confidence_desc' | 'name_asc'>('updated_desc')
  const [drawerOpen, setDrawerOpen] = useState(false)
  const [detailLoading, setDetailLoading] = useState(false)
  const [active, setActive] = useState<Intelligence | null>(null)
  const [searchParams, setSearchParams] = useSearchParams()
  const navigate = useNavigate()
  const pageSize = 20

  const refresh = useCallback(async () => {
    setLoading(true)
    try {
      const result = await listPersonIntelligence({
        keyword: keyword.trim(),
        organization: organization.trim(),
        sort,
        skip: (page - 1) * pageSize,
        limit: pageSize,
        summary_only: true,
      })
      setItems(result.items)
      setTotal(result.total)
    } catch (error) {
      message.error(error instanceof Error ? error.message : '加载人物情报失败')
    } finally {
      setLoading(false)
    }
  }, [keyword, organization, page, sort])

  useEffect(() => {
    void refresh()
  }, [refresh])

  const openDetail = useCallback(async (intelId: string) => {
    setDrawerOpen(true)
    setDetailLoading(true)
    try {
      setActive(await getPersonIntelligence(intelId))
    } catch (error) {
      message.error(error instanceof Error ? error.message : '读取人物情报失败')
      setDrawerOpen(false)
    } finally {
      setDetailLoading(false)
    }
  }, [])

  useEffect(() => {
    const intelId = searchParams.get('intel_id')
    if (!intelId) return
    void openDetail(intelId)
    const next = new URLSearchParams(searchParams)
    next.delete('intel_id')
    setSearchParams(next, { replace: true })
  }, [openDetail, searchParams, setSearchParams])

  const referenceInHub = (item: Intelligence) => {
    const params = new URLSearchParams({
      ref_person_intel: item.intel_id,
      label: item.name,
      desc: [item.organization, item.position].filter(Boolean).join(' · '),
    })
    navigate(`/phishing?${params.toString()}`)
  }

  const downloadArtifact = async (artifactId: string) => {
    try {
      await downloadWithAuth(`/api/v1/artifacts/${encodeURIComponent(artifactId)}/download`, artifactId)
    } catch (error) {
      message.error(error instanceof Error ? error.message : '下载产物失败')
    }
  }

  const columns: TableProps<Intelligence>['columns'] = useMemo(() => [
    {
      title: '人物',
      key: 'identity',
      width: 260,
      render: (_, record) => (
        <div className="intel-identity">
          <div className="intel-avatar">{record.name.slice(0, 1)}</div>
          <div className="intel-identity-copy">
            <Text strong>{record.name}</Text>
            <Text type="secondary" ellipsis={{ tooltip: record.organization }}>
              {[record.organization, record.position].filter(Boolean).join(' · ')}
            </Text>
          </div>
        </div>
      ),
    },
    {
      title: '摘要',
      dataIndex: 'summary',
      key: 'summary',
      render: (value: string) => <Paragraph className="intel-summary" ellipsis={{ rows: 2, tooltip: value }}>{value || '-'}</Paragraph>,
    },
    {
      title: '可信度',
      dataIndex: 'confidence',
      key: 'confidence',
      width: 130,
      render: (value: number) => (
        <Progress percent={Math.round((value || 0) * 100)} size="small" status="normal" />
      ),
    },
    {
      title: '证据',
      key: 'evidence',
      width: 190,
      render: (_, record) => (
        <div className="intel-counts">
          <Text>{record.source_count || 0} 来源 · {record.evidence_count || 0} 证据</Text>
          <Text type="secondary">{record.signal_count || 0} 信号 · {record.scenario_count || 0} 场景 · {record.copywriting_count || 0} 话术</Text>
        </div>
      ),
    },
    {
      title: '版本',
      key: 'version',
      width: 100,
      render: (_, record) => <Tag>v{record.profile_version || 1} · {record.research_rounds || 1} 轮</Tag>,
    },
    {
      title: '更新时间',
      dataIndex: 'updated_at',
      key: 'updated_at',
      width: 170,
      render: (value: string) => <Text type="secondary">{formatDate(value)}</Text>,
    },
    {
      title: '操作',
      key: 'actions',
      width: 96,
      fixed: 'right',
      render: (_, record) => (
        <Tooltip title="查看情报详情">
          <Button type="text" icon={<EyeOutlined />} onClick={() => void openDetail(record.intel_id)} />
        </Tooltip>
      ),
    },
  ], [openDetail])

  const lineageNodes = active?.lineage?.nodes || []
  const lineageEdges = active?.lineage?.edges || []
  const lineageNodeMap = new Map(lineageNodes.map(node => [node.node_id, node]))
  const chainNodes = lineageNodes.filter(node => !['person', 'organization'].includes(node.node_type))
  const detailTabs = active ? [
    {
      key: 'lineage',
      label: '研究链路',
      children: (
        <div className="intel-chain">
          <Steps
            size="small"
            responsive
            items={LINEAGE_STAGES.map(stage => {
              const count = lineageNodes.filter(node => stage.types.includes(node.node_type)).length
              return {
                title: stage.title,
                content: count ? `${count} 项` : '待补充',
                status: count ? 'finish' : 'wait',
              }
            })}
          />
          {chainNodes.length > 0 ? (
            <div className="intel-list">
              {chainNodes.map(node => {
              const links = lineageEdges
                .filter(edge => edge.source === node.node_id || edge.target === node.node_id)
                .map(edge => {
                  const peerId = edge.source === node.node_id ? edge.target : edge.source
                  const peer = lineageNodeMap.get(peerId)
                  return `${RELATION_LABELS[edge.relation] || edge.relation} ${peer?.label || peerId}`
                })
              return (
                <IntelRow
                  key={node.node_id}
                  title={<Space wrap><Tag>{LINEAGE_STAGES.find(stage => stage.types.includes(node.node_type))?.title || node.node_type}</Tag><Text strong>{node.label}</Text></Space>}
                  description={links.join('；') || '已纳入人物研究链路'}
                  actions={<Space>{node.url && <Tooltip title="打开来源"><Button type="text" icon={<LinkOutlined />} href={String(node.url)} target="_blank" rel="noreferrer" /></Tooltip>}{node.artifact_id && <Tooltip title="下载产物"><Button type="text" icon={<DownloadOutlined />} onClick={() => void downloadArtifact(String(node.artifact_id))} /></Tooltip>}</Space>}
                />
              )
              })}
            </div>
          ) : <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="暂无持久化链路" />}
        </div>
      ),
    },
    {
      key: 'evidence',
      label: `证据与来源 (${active.source_count || active.sources?.length || 0})`,
      children: (
        <div className="intel-detail-list">
          {(active.evidence || []).length > 0 ? (
            <div className="intel-list">{(active.evidence || []).map(item => (
              <IntelRow
                key={item.evidence_id || `${item.dimension}-${item.finding}`}
                title={<Space wrap><Tag color={item.evidence_type === 'fact' ? 'green' : 'gold'}>{item.evidence_type === 'fact' ? '事实' : '推断'}</Tag><Text strong>{item.dimension}</Text><Text type="secondary">{Math.round((item.confidence || 0) * 100)}%</Text></Space>}
                description={item.finding}
              />
            ))}</div>
          ) : <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="暂无证据" />}
          <Text strong>公开来源</Text>
          {(active.sources || []).length > 0 ? (
            <div className="intel-list">{(active.sources || []).map(source => (
              <IntelRow
                key={source.url}
                title={<Text strong>{source.title || source.url}</Text>}
                description={source.summary || source.url}
                actions={<Space><Tooltip title="复制链接"><Button type="text" icon={<CopyOutlined />} onClick={() => void navigator.clipboard.writeText(source.url).then(() => message.success('链接已复制'))} /></Tooltip><Tooltip title="打开原文"><Button type="text" icon={<LinkOutlined />} href={source.url} target="_blank" rel="noreferrer" /></Tooltip></Space>}
              />
            ))}</div>
          ) : <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="暂无来源" />}
        </div>
      ),
    },
    {
      key: 'signals',
      label: `当前信号 (${active.context_signals?.length || 0})`,
      children: (
        (active.context_signals || []).length > 0 ? (
          <div className="intel-list">{(active.context_signals || []).map(signal => (
            <IntelRow
              key={signal.signal_id || signal.title}
              title={<Space wrap><Tag color={signal.status === 'expired' ? 'default' : signal.status === 'invalid' ? 'red' : 'cyan'}>{signal.status === 'expired' ? '已过期' : signal.status === 'invalid' ? '日期异常' : signal.signal_type || '当前信号'}</Tag><Text strong delete={signal.status === 'expired'}>{signal.title}</Text></Space>}
              description={<div className="intel-signal-copy">{signal.summary && <Paragraph>{signal.summary}</Paragraph>}{signal.relevance && <Text>关联：{signal.relevance}</Text>}<Text type="secondary">观察时间：{formatDate(signal.observed_at)}{signal.expires_at ? ` · 有效至 ${formatDate(signal.expires_at)}` : ''}</Text><Space wrap>{(signal.source_urls || []).map(url => <Button key={url} type="link" size="small" icon={<LinkOutlined />} href={url} target="_blank" rel="noreferrer">来源</Button>)}</Space></div>}
            />
          ))}</div>
        ) : <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="暂无当前时机或热点信号" />
      ),
    },
    {
      key: 'profile',
      label: '人物画像',
      children: (
        <Descriptions bordered size="small" column={1} items={Object.entries(active.profile || {}).map(([key, value]) => ({ key, label: key, children: displayValue(value) || '-' }))} />
      ),
    },
    {
      key: 'plan',
      label: '沟通方案',
      children: (
        <div className="intel-plan">
          <Descriptions bordered size="small" column={1} items={Object.entries(active.engagement_plan || {}).map(([key, value]) => ({ key, label: key, children: displayValue(value) || '-' }))} />
          {(active.scenarios || []).length > 0 && (
            <div className="intel-section"><Text strong>沟通场景</Text><div className="intel-list">{[...(active.scenarios || [])].sort((a, b) => (b.priority || 0) - (a.priority || 0)).map(scenario => (
              <IntelRow
                key={scenario.scenario_id || scenario.title}
                title={<Space wrap><Text strong>{scenario.title}</Text><Tag color="blue">优先级 {scenario.priority ?? 50}</Tag>{scenario.timing && <Tag>{scenario.timing}</Tag>}</Space>}
                description={<div className="intel-scenario-copy"><Text>{scenario.objective || '-'}</Text>{scenario.rationale && <Text type="secondary">依据：{scenario.rationale}</Text>}</div>}
              />
            ))}</div></div>
          )}
          {(active.recommended_personas || []).length > 0 && (
            <div className="intel-section"><Text strong>匹配人设</Text><div className="intel-list">{(active.recommended_personas || []).map(item => (
              <IntelRow
                key={item.person_id}
                title={<Space wrap><Text>{item.name || item.person_id}</Text><Tag>{Math.round((item.score || 0) * 100)}%</Tag></Space>}
                description={item.rationale || '-'}
                actions={<Button type="link" onClick={() => navigate(`/persona-library?person_id=${encodeURIComponent(item.person_id)}`)}>查看人设</Button>}
              />
            ))}</div></div>
          )}
        </div>
      ),
    },
    {
      key: 'copywriting',
      label: `话术 (${active.sample_copywritings?.length || 0})`,
      children: (
        (active.sample_copywritings || []).length > 0 ? (
          <div className="intel-list">{(active.sample_copywritings || []).map(item => (
            <IntelRow
              key={item.copywriting_id || `${item.title}-${item.content}`}
              title={<Space wrap><Text strong>{item.title || '沟通话术'}</Text><Tag>{item.channel || '通用'}</Tag></Space>}
              description={<div><Paragraph className="intel-copywriting">{item.content}</Paragraph>{item.basis && <Text type="secondary">依据：{item.basis}</Text>}</div>}
              actions={<Tooltip title="复制话术"><Button type="text" icon={<CopyOutlined />} onClick={() => void navigator.clipboard.writeText(item.content).then(() => message.success('话术已复制'))} /></Tooltip>}
            />
          ))}</div>
        ) : <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="暂无话术" />
      ),
    },
  ] : []

  return (
    <div className="person-intelligence page-container fade-in">
      <div className="page-header intel-header">
        <div>
          <Title level={2} className="page-title"><GlobalOutlined /> 人物情报</Title>
          <Text type="secondary">{total} 名已核验人物</Text>
        </div>
        <Button icon={<ReloadOutlined />} onClick={() => void refresh()} loading={loading}>刷新</Button>
      </div>

      <div className="intel-toolbar">
        <Input allowClear prefix={<SearchOutlined />} placeholder="姓名、职位、研究方向" value={keyword} onChange={event => setKeyword(event.target.value)} onPressEnter={() => { setPage(1); void refresh() }} />
        <Input allowClear placeholder="机构" value={organization} onChange={event => setOrganization(event.target.value)} onPressEnter={() => { setPage(1); void refresh() }} />
        <Select value={sort} onChange={value => { setSort(value); setPage(1) }} options={[{ value: 'updated_desc', label: '最近更新' }, { value: 'confidence_desc', label: '可信度优先' }, { value: 'name_asc', label: '姓名排序' }]} />
        <Button type="primary" icon={<SearchOutlined />} onClick={() => { setPage(1); void refresh() }}>检索</Button>
      </div>

      <Table<Intelligence>
        rowKey="intel_id"
        columns={columns}
        dataSource={items}
        loading={loading}
        scroll={{ x: 1120 }}
        pagination={{ current: page, pageSize, total, showSizeChanger: false, showTotal: value => `共 ${value} 条`, onChange: setPage }}
      />

      <Drawer
        title="人物情报详情"
        open={drawerOpen}
        size={900}
        loading={detailLoading}
        onClose={() => setDrawerOpen(false)}
        extra={active && <Button type="primary" icon={<RobotOutlined />} onClick={() => referenceInHub(active)}>引用到 AI 中枢</Button>}
      >
        {active && (
          <div className="intel-detail">
            <div className="intel-detail-head">
              <div className="intel-avatar large"><SafetyCertificateOutlined /></div>
              <div>
                <Title level={3}>{active.name}</Title>
                <Flex gap={8} wrap="wrap">
                  <Text>{active.organization}</Text>
                  {active.position && <Tag>{active.position}</Tag>}
                  <Tag color="blue">可信度 {Math.round((active.confidence || 0) * 100)}%</Tag>
                  <Tag>v{active.profile_version || 1}</Tag>
                </Flex>
              </div>
            </div>
            <Descriptions size="small" column={{ xs: 1, sm: 2 }} items={[
              { key: 'department', label: '部门', children: active.department || '-' },
              { key: 'location', label: '地区', children: active.location || '-' },
              { key: 'research', label: '研究方向', children: active.research_areas?.join('、') || '-' },
              { key: 'updated', label: '最近研究', children: formatDate(active.last_researched_at || active.updated_at) },
            ]} />
            {active.summary && <Paragraph className="intel-detail-summary">{active.summary}</Paragraph>}
            {active.background && <Paragraph>{active.background}</Paragraph>}
            <Tabs items={detailTabs} />
          </div>
        )}
      </Drawer>
    </div>
  )
}
