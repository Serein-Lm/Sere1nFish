import { useState, useEffect, useCallback } from 'react'
import { useParams, useNavigate } from 'react-router-dom'
import {
  Typography, Card, Table, Tag, Space, Button, Skeleton, Empty,
  Tabs, Drawer, Statistic, Row, Col, Progress, message, Modal,
} from 'antd'
import type { ColumnsType } from 'antd/es/table'
import {
  ArrowLeftOutlined, ReloadOutlined, EyeOutlined, DeleteOutlined,
  ClockCircleOutlined, CheckCircleOutlined, SyncOutlined,
  ExclamationCircleOutlined, BarChartOutlined,
  DollarOutlined, FilterOutlined, FileTextOutlined,
} from '@ant-design/icons'
import CopywritingRenderer, {
  FINDING_TYPE_ICONS, CHANNEL_TYPE_LABELS,
} from '../../components/CopywritingRenderer/CopywritingRenderer'
import { renderFindingValue } from '../../utils/findingValueRenderer'
import {
  getTask, getTaskStats, deleteTask, listProjectFindings, getFindingCopywriting,
} from '../../services/taskService'
import type {
  Task, FindingCopywriting, TaskStatsResponse, TaskProgress, UnifiedFinding,
  XhsTargetDecision, WechatTargetDecision,
} from '../../services/taskService'
import './TaskDetail.css'

const { Title, Paragraph, Text } = Typography

const TASK_TYPE_LABELS: Record<string, string> = {
  company_scan: '综合公司扫描', url_scan: 'URL 扫描', xhs_search: '小红书搜索', douyin_search: '抖音搜索', web_tagging: '官网打标',
}
const STATUS_MAP: Record<string, { color: string; icon: React.ReactNode; label: string }> = {
  pending: { color: 'default', icon: <ClockCircleOutlined />, label: '等待中' },
  probing: { color: 'processing', icon: <SyncOutlined spin />, label: '探活中' },
  scanning: { color: 'processing', icon: <SyncOutlined spin />, label: '扫描中' },
  generating: { color: 'processing', icon: <SyncOutlined spin />, label: '生成中' },
  running: { color: 'processing', icon: <SyncOutlined spin />, label: '执行中' },
  completed: { color: 'success', icon: <CheckCircleOutlined />, label: '已完成' },
  error: { color: 'error', icon: <ExclamationCircleOutlined />, label: '失败' },
}

const XHS_CATEGORY_LABELS: Record<string, string> = {
  large_enterprise: '大型企业',
  internet_platform: '互联网平台',
  insurance_finance: '保险/金融',
  large_commercial_organization: '大型商业单位',
  government: '党政机关',
  public_institution: '事业单位',
  public_official: '公务人员/机构',
  small_or_low_visibility: '小型或低曝光目标',
  other: '其他',
  unknown: '无法判断',
  manual: '手动指定',
}

const XHS_SELECTION_STATUS: Record<string, { label: string; color: string }> = {
  pending: { label: '等待判定', color: 'processing' },
  completed: { label: '判定完成', color: 'success' },
  fallback: { label: '保守降级', color: 'warning' },
  disabled: { label: '未启用', color: 'default' },
}

const WECHAT_CATEGORY_LABELS: Record<string, string> = {
  government_public_institution: '党政/公共机构',
  traditional_state_owned_enterprise: '传统国企',
  exchange_financial_infrastructure: '交易所/金融基础设施',
  broadcast_news_media: '广播电视/新闻媒体',
  education_research_healthcare: '教育/科研/医疗',
  mature_financial_institution: '成熟金融机构',
  traditional_large_enterprise: '传统大型企业',
  internet_consumer_brand: '互联网/消费品牌',
  new_or_lightweight_company: '新设/轻量公司',
  other: '其他',
  unknown: '无法判断',
  all: '全部目标',
}

const WECHAT_PRIORITY_LABELS: Record<string, { label: string; color: string }> = {
  high: { label: '高', color: 'error' },
  normal: { label: '普通', color: 'processing' },
  low: { label: '低', color: 'default' },
  skip: { label: '跳过', color: 'default' },
}

export default function TaskDetail() {
  const navigate = useNavigate()
  const { taskId, projectId: routeProjectId } = useParams<{ taskId: string; projectId?: string }>()

  const [loading, setLoading] = useState(true)
  const [task, setTask] = useState<Task | null>(null)
  const [findings, setFindings] = useState<UnifiedFinding[]>([])
  const [taskStats, setTaskStats] = useState<TaskStatsResponse | null>(null)
  const [activeTab, setActiveTab] = useState('findings')
  const [drawerOpen, setDrawerOpen] = useState(false)
  const [currentCopywriting, setCurrentCopywriting] = useState<FindingCopywriting | null>(null)
  const [copywritingLoading, setCopywritingLoading] = useState(false)

  // projectId 优先从路由取，否则从 task 数据取
  const projectId = routeProjectId || task?.project_id

  const fetchData = useCallback(async () => {
    if (!taskId || !projectId) return
    setLoading(true)
    try {
      const [taskData, findingsData, statsData] = await Promise.allSettled([
        getTask(projectId, taskId),
        listProjectFindings(projectId, { task_id: taskId, page: 1, page_size: 100 }),
        getTaskStats(taskId),
      ])
      if (taskData.status === 'fulfilled') setTask(taskData.value)
      if (findingsData.status === 'fulfilled') setFindings(findingsData.value.items)
      if (statsData.status === 'fulfilled') setTaskStats(statsData.value)
    } catch (e) {
      console.error(e)
      message.error('加载任务数据失败')
    } finally {
      setLoading(false)
    }
  }, [taskId, projectId])

  useEffect(() => { fetchData() }, [fetchData])

  // 轮询非终态任务
  useEffect(() => {
    if (!task || task.status === 'completed' || task.status === 'error') return
    const timer = setInterval(async () => {
      if (!taskId || !projectId) return
      try {
        const updated = await getTask(projectId, taskId)
        setTask(updated)
        if (updated.status === 'completed' || updated.status === 'error') {
          clearInterval(timer)
          fetchData()
        }
      } catch { /* ignore */ }
    }, 3000)
    return () => clearInterval(timer)
  }, [task?.status, taskId, fetchData])

  const handleDelete = () => {
    if (!taskId || !projectId) return
    Modal.confirm({
      title: '确认删除任务', okText: '删除', okType: 'danger', cancelText: '取消',
      content: '删除后将同时清除该任务下的所有信息节点和话术数据，无法恢复。',
      onOk: async () => {
        try { await deleteTask(projectId, taskId); message.success('任务已删除'); navigate(-1) }
        catch (e) { message.error(e instanceof Error ? e.message : '删除失败') }
      },
    })
  }

  const progress = task?.progress || {} as TaskProgress
  const xhsSelection = task?.task_type === 'company_scan'
    ? task.result?.xhs?.selection
    : undefined
  const wechatSelection = task?.task_type === 'company_scan'
    ? task.result?.wechat?.selection
    : undefined
  const scholarSummary = task?.task_type === 'company_scan'
    ? task.result?.scholar
    : undefined

  const SOURCE_ICONS: Record<string, string> = { web_tagging: '🌐', xhs: '📕', douyin: '🎵' }

  const findingsColumns: ColumnsType<UnifiedFinding> = [
    { title: '来源', key: 'source', width: 70, render: (_, r) => <span>{SOURCE_ICONS[r.source] || '📌'} {r.source}</span> },
    { title: '类型', dataIndex: 'type', key: 'type', width: 120, render: (v: string) => <Tag color="volcano">{FINDING_TYPE_ICONS[v] || '📌'} {v}</Tag> },
    { title: '标签', dataIndex: 'label', key: 'label', width: 160, render: (v: string | null) => <Text strong>{v || '-'}</Text> },
    { title: '值', dataIndex: 'value', key: 'value', width: 200, render: (v: string | null) => renderFindingValue(v, { copyable: true, maxWidth: 180 }) },
    { title: '关注度', dataIndex: 'attention_score', key: 'score', width: 80, align: 'center', render: (v: number) => <Tag color={v >= 70 ? 'error' : v >= 40 ? 'warning' : 'processing'}>{v}</Tag> },
    { title: '操作', key: 'action', width: 100, render: (_, r) => (
      <Button type="link" size="small" icon={<EyeOutlined />} onClick={async () => {
        setCopywritingLoading(true)
        setDrawerOpen(true)
        try {
          const cw = await getFindingCopywriting(r.finding_id)
          setCurrentCopywriting(cw)
        } catch { message.error('获取话术失败') }
        finally { setCopywritingLoading(false) }
      }}>话术</Button>
    ) },
  ]

  const xhsSelectionColumns: ColumnsType<XhsTargetDecision> = [
    {
      title: '目标', dataIndex: 'target_name', key: 'target_name', width: 220,
      render: (value: string, record) => (
        <Space orientation="vertical" size={0}>
          <Text strong>{value}</Text>
          <Text type="secondary" copyable={{ text: record.target_id }} style={{ fontSize: 11 }}>
            {record.target_id}
          </Text>
        </Space>
      ),
    },
    {
      title: '目标类型', dataIndex: 'target_category', key: 'target_category', width: 150,
      render: (value: string) => <Tag>{XHS_CATEGORY_LABELS[value] || value}</Tag>,
    },
    {
      title: '判定', dataIndex: 'should_collect_xhs', key: 'decision', width: 110,
      render: (value: boolean) => (
        <Tag color={value ? 'success' : 'default'}>{value ? '采集' : '跳过'}</Tag>
      ),
    },
    {
      title: '置信度', dataIndex: 'confidence', key: 'confidence', width: 140,
      render: (value: number) => (
        <Progress percent={Math.round((value || 0) * 100)} size="small" style={{ width: 100 }} />
      ),
    },
    {
      title: '依据', dataIndex: 'reason', key: 'reason', minWidth: 260,
      render: (value: string, record) => (
        <Space orientation="vertical" size={2}>
          <Text>{value || '-'}</Text>
          <Text type="secondary" style={{ fontSize: 11 }}>
            {record.source === 'ai' ? 'AI 判定' : record.source === 'manual' ? '手动名单' : '失败降级'}
          </Text>
        </Space>
      ),
    },
  ]

  const wechatSelectionColumns: ColumnsType<WechatTargetDecision> = [
    {
      title: '目标', dataIndex: 'target_name', key: 'target_name', width: 220,
      render: (value: string, record) => (
        <Space orientation="vertical" size={0}>
          <Text strong>{value}</Text>
          <Text type="secondary" copyable={{ text: record.target_id }} style={{ fontSize: 11 }}>
            {record.target_id}
          </Text>
        </Space>
      ),
    },
    {
      title: '机构类型', dataIndex: 'target_category', key: 'target_category', width: 180,
      render: (value: string) => <Tag>{WECHAT_CATEGORY_LABELS[value] || value}</Tag>,
    },
    {
      title: '优先级', dataIndex: 'collection_priority', key: 'priority', width: 100,
      render: (value: string | undefined, record) => {
        const resolved = value || (record.should_collect_wechat ? 'normal' : 'skip')
        const priority = WECHAT_PRIORITY_LABELS[resolved] || WECHAT_PRIORITY_LABELS.normal
        return <Tag color={priority.color}>{priority.label}</Tag>
      },
    },
    {
      title: '判定', dataIndex: 'should_collect_wechat', key: 'decision', width: 110,
      render: (value: boolean) => (
        <Tag color={value ? 'success' : 'default'}>{value ? '采集' : '跳过'}</Tag>
      ),
    },
    {
      title: '置信度', dataIndex: 'confidence', key: 'confidence', width: 140,
      render: (value: number) => (
        <Progress percent={Math.round((value || 0) * 100)} size="small" style={{ width: 100 }} />
      ),
    },
    {
      title: '依据', dataIndex: 'reason', key: 'reason', minWidth: 260,
      render: (value: string, record) => (
        <Space orientation="vertical" size={2}>
          <Text>{value || '-'}</Text>
          <Text type="secondary" style={{ fontSize: 11 }}>
            {record.source === 'ai' ? 'AI 判定' : record.source === 'all' ? '全部目标' : '失败降级'}
          </Text>
        </Space>
      ),
    },
  ]

  const renderXhsSelectionTab = () => {
    if (!xhsSelection) return <Empty description="任务完成后显示小红书目标判定结果" />
    const selectionStatus = XHS_SELECTION_STATUS[xhsSelection.status] || XHS_SELECTION_STATUS.pending
    return (
      <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
        <Row gutter={[16, 16]}>
          <Col xs={12} sm={6}><Statistic title="选择方式" value={xhsSelection.mode === 'auto' ? 'AI 自动判断' : '手动名单'} /></Col>
          <Col xs={12} sm={6}><Statistic title="纳入采集" value={xhsSelection.selected_count} /></Col>
          <Col xs={12} sm={6}><Statistic title="跳过目标" value={xhsSelection.skipped_count} /></Col>
          <Col xs={12} sm={6}><Statistic title="判定状态" valueRender={() => <Tag color={selectionStatus.color}>{selectionStatus.label}</Tag>} /></Col>
        </Row>
        <Space wrap>
          {xhsSelection.prompt_slug ? (
            <Button type="link" size="small" icon={<FileTextOutlined />} onClick={() => navigate('/prompts')}>
              在 Prompt 库查看 {xhsSelection.prompt_slug}
            </Button>
          ) : null}
          {xhsSelection.error ? <Text type="danger">判定异常：{xhsSelection.error}</Text> : null}
        </Space>
        {xhsSelection.unmatched_manual_targets.length > 0 ? (
          <Text type="warning">
            未匹配名单：{xhsSelection.unmatched_manual_targets.join('、')}
          </Text>
        ) : null}
        <Table
          dataSource={xhsSelection.decisions}
          rowKey="target_id"
          columns={xhsSelectionColumns}
          pagination={false}
          size="small"
          scroll={{ x: 900 }}
          locale={{ emptyText: '暂无目标判定记录' }}
        />
      </div>
    )
  }

  const renderWechatSelectionTab = () => {
    if (!wechatSelection) return <Empty description="任务完成后显示公众号目标判定结果" />
    const selectionStatus = XHS_SELECTION_STATUS[wechatSelection.status] || XHS_SELECTION_STATUS.pending
    return (
      <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
        <Row gutter={[16, 16]}>
          <Col xs={12} sm={6}><Statistic title="选择方式" value={wechatSelection.mode === 'auto' ? '机构优先自动分级' : '全部目标'} /></Col>
          <Col xs={12} sm={6}><Statistic title="纳入采集" value={wechatSelection.selected_count} /></Col>
          <Col xs={12} sm={6}><Statistic title="跳过目标" value={wechatSelection.skipped_count} /></Col>
          <Col xs={12} sm={6}><Statistic title="判定状态" valueRender={() => <Tag color={selectionStatus.color}>{selectionStatus.label}</Tag>} /></Col>
        </Row>
        <Space wrap>
          {wechatSelection.prompt_slug ? (
            <Button type="link" size="small" icon={<FileTextOutlined />} onClick={() => navigate('/prompts')}>
              在 Prompt 库查看 {wechatSelection.prompt_slug}
            </Button>
          ) : null}
          {wechatSelection.error ? <Text type="danger">判定异常：{wechatSelection.error}</Text> : null}
        </Space>
        <Table
          dataSource={wechatSelection.decisions}
          rowKey="target_id"
          columns={wechatSelectionColumns}
          pagination={false}
          size="small"
          scroll={{ x: 1060 }}
          locale={{ emptyText: '暂无目标判定记录' }}
        />
      </div>
    )
  }

  // Token 统计 — 完整表格展示
  const renderStatsTab = () => {
    if (!taskStats) return <Empty description="暂无统计数据" />
    const s = taskStats.stats
    return (
      <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
        <Row gutter={16}>
          <Col xs={12} sm={8} md={4}><Statistic title="总调用次数" value={s.total_calls} /></Col>
          <Col xs={12} sm={8} md={4}><Statistic title="总 Token" value={s.total_tokens} groupSeparator="," /></Col>
          <Col xs={12} sm={8} md={4}><Statistic title="输入 Token" value={s.total_input_tokens} groupSeparator="," /></Col>
          <Col xs={12} sm={8} md={4}><Statistic title="输出 Token" value={s.total_output_tokens} groupSeparator="," /></Col>
          <Col xs={12} sm={8} md={4}><Statistic title="总费用" prefix="¥" value={s.total_cost_yuan} precision={4} /></Col>
          <Col xs={12} sm={8} md={4}><Statistic title="累计耗时" value={(s.total_duration_ms / 1000).toFixed(1)} suffix="s" /></Col>
        </Row>

        {Object.keys(s.by_model).length > 0 && (
          <Card size="small" title={<Space><BarChartOutlined /> 模型用量</Space>}>
            <Table
              dataSource={Object.entries(s.by_model).map(([model, ms]) => ({ model, ...ms }))}
              rowKey="model" size="small" pagination={false}
              columns={[
                { title: '模型', dataIndex: 'model', key: 'model', render: (v: string) => <Tag>{v}</Tag> },
                { title: '调用次数', dataIndex: 'calls', key: 'calls' },
                { title: 'Token', dataIndex: 'total_tokens', key: 'tokens', render: (v: number) => v?.toLocaleString() },
                { title: '费用', dataIndex: 'cost_yuan', key: 'cost', render: (v: number) => <Tag color="gold"><DollarOutlined /> ¥{v?.toFixed(4)}</Tag> },
                { title: '占比', key: 'pct', width: 120, render: (_: unknown, r: { calls: number }) => <Progress percent={s.total_calls > 0 ? Math.round((r.calls / s.total_calls) * 100) : 0} size="small" format={p => `${p}%`} /> },
              ]}
            />
          </Card>
        )}

        {taskStats.agents && taskStats.agents.length > 0 && (
          <Card size="small" title="Agent 执行明细">
            <Table dataSource={taskStats.agents} rowKey="agent" size="small" pagination={false}
              columns={[
                { title: 'Agent', dataIndex: 'agent', key: 'agent', render: (v: string) => <Tag color="cyan">{v}</Tag> },
                { title: '调用次数', dataIndex: 'total_calls', key: 'calls' },
                { title: 'Token', dataIndex: 'total_tokens', key: 'tokens', render: (v: number) => v.toLocaleString() },
                { title: '费用', dataIndex: 'total_cost_yuan', key: 'cost', render: (v: number) => `¥${v.toFixed(4)}` },
                { title: '耗时', dataIndex: 'total_duration_ms', key: 'dur', render: (v: number) => `${(v / 1000).toFixed(1)}s` },
              ]}
            />
          </Card>
        )}
      </div>
    )
  }

  const statusInfo = task ? STATUS_MAP[task.status] || STATUS_MAP.pending : STATUS_MAP.pending

  return (
    <div className="task-detail page-container fade-in">
      <div className="page-header slide-up">
        <div>
          <Title level={2} className="page-title">任务详情</Title>
          <Paragraph className="page-description">查看任务执行结果、信息节点和话术内容</Paragraph>
        </div>
        <Space>
          <Button icon={<ArrowLeftOutlined />} onClick={() => navigate(-1)} className="hover-float">返回</Button>
          <Button icon={<ReloadOutlined />} onClick={fetchData} className="hover-float">刷新</Button>
          <Button danger icon={<DeleteOutlined />} className="hover-float" onClick={handleDelete}>删除任务</Button>
        </Space>
      </div>

      {loading ? <Card className="glass-card"><Skeleton active /></Card> : !task ? <Card className="glass-card"><Empty description="任务不存在" /></Card> : (
        <>
          {/* 任务概览 */}
          <Card className="glass-card slide-up stagger-1" size="small">
            <Row gutter={16}>
              <Col xs={12} sm={8} md={4}><Statistic title="任务类型" value={TASK_TYPE_LABELS[task.task_type] || task.task_type} /></Col>
              <Col xs={12} sm={8} md={4}><Statistic title="状态" valueRender={() => <Tag icon={statusInfo.icon} color={statusInfo.color}>{statusInfo.label}</Tag>} /></Col>
              <Col xs={12} sm={8} md={4}><Statistic title="Findings" value={findings.length} /></Col>
              <Col xs={12} sm={8} md={4}><Statistic title="耗时" value={task.elapsed_ms ? `${(task.elapsed_ms / 1000).toFixed(1)}s` : '-'} /></Col>
            </Row>
            {/* url_scan 进度 */}
            {task.task_type === 'url_scan' && progress.total_urls != null && (
              <Row gutter={16} style={{ marginTop: 12 }}>
                <Col xs={12} sm={6}><Statistic title="总 URL" value={progress.total_urls} /></Col>
                <Col xs={12} sm={6}><Statistic title="存活 URL" value={progress.alive_urls ?? '-'} /></Col>
                <Col xs={12} sm={6}><Statistic title="已扫描" value={progress.scanned_urls ?? '-'} /></Col>
                <Col xs={12} sm={6}><Statistic title="已生成话术" value={progress.total_copywritings ?? '-'} /></Col>
              </Row>
            )}
            {task.task_type === 'company_scan' && xhsSelection && (
              <Row gutter={16} style={{ marginTop: 12 }}>
                <Col xs={12} sm={6}><Statistic title="XHS 选择方式" value={xhsSelection.mode === 'auto' ? 'AI 自动' : '手动名单'} /></Col>
                <Col xs={12} sm={6}><Statistic title="XHS 纳入目标" value={xhsSelection.selected_count} /></Col>
                <Col xs={12} sm={6}><Statistic title="XHS 跳过目标" value={xhsSelection.skipped_count} /></Col>
              </Row>
            )}
            {task.task_type === 'company_scan' && wechatSelection && (
              <Row gutter={16} style={{ marginTop: 12 }}>
                <Col xs={12} sm={6}><Statistic title="公众号选择方式" value={wechatSelection.mode === 'auto' ? '机构优先分级' : '全部目标'} /></Col>
                <Col xs={12} sm={6}><Statistic title="公众号纳入目标" value={wechatSelection.selected_count} /></Col>
                <Col xs={12} sm={6}><Statistic title="公众号跳过目标" value={wechatSelection.skipped_count} /></Col>
              </Row>
            )}
            {task.task_type === 'company_scan' && scholarSummary?.direction && (
              <Row gutter={16} style={{ marginTop: 12 }}>
                <Col xs={24} sm={12}><Statistic title="学者研究方向" value={scholarSummary.direction} /></Col>
                <Col xs={24} sm={12}><Statistic title="方向来源" value={scholarSummary.direction_source === 'manual' ? '手动填写' : scholarSummary.direction_source === 'company_router' ? '公司路由画像' : '行业默认'} /></Col>
              </Row>
            )}
            {task.error && <div style={{ marginTop: 12 }}><Text type="danger">错误：{task.error}</Text></div>}
          </Card>

          {/* 主内容 Tab */}
          <Card className="glass-card slide-up stagger-2">
            <Tabs activeKey={activeTab} onChange={setActiveTab} items={[
              ...(task.task_type === 'company_scan' ? [{
                key: 'xhs-selection',
                label: <Space><FilterOutlined /> XHS 目标选择 <Tag>{xhsSelection?.decisions.length || 0}</Tag></Space>,
                children: renderXhsSelectionTab(),
              }, {
                key: 'wechat-selection',
                label: <Space><FilterOutlined /> 公众号目标选择 <Tag>{wechatSelection?.decisions.length || 0}</Tag></Space>,
                children: renderWechatSelectionTab(),
              }] : []),
              {
                key: 'findings',
                label: <Space><EyeOutlined /> Findings <Tag color="blue">{findings.length}</Tag></Space>,
                children: <Table dataSource={findings} rowKey="finding_id" columns={findingsColumns} pagination={{ pageSize: 10, showTotal: t => `共 ${t} 条` }} size="small" />,
              },
              {
                key: 'stats',
                label: <Space><BarChartOutlined /> Token 统计</Space>,
                children: renderStatsTab(),
              },
            ]} />
          </Card>
        </>
      )}

      <Drawer
        title={currentCopywriting ? <Space><span>{FINDING_TYPE_ICONS[currentCopywriting.finding_type] || '📌'}</span><span>{currentCopywriting.finding_label}</span><Tag color="blue">{CHANNEL_TYPE_LABELS[currentCopywriting.finding_channel] || currentCopywriting.finding_channel}</Tag></Space> : '话术详情'}
        open={drawerOpen} onClose={() => { setDrawerOpen(false); setCurrentCopywriting(null) }} width={720} destroyOnClose
      >
        {copywritingLoading ? <Skeleton active /> : currentCopywriting ? <CopywritingRenderer data={currentCopywriting} /> : <Empty description="暂无话术数据" />}
      </Drawer>
    </div>
  )
}
