import { useCallback, useEffect, useMemo, useState, type ReactNode } from 'react'
import { useNavigate } from 'react-router-dom'
import { Row, Col, Card, Statistic, Progress, Tag, Button, Space, Tooltip, message, Table, Skeleton, Empty, Segmented, Typography, Alert } from 'antd'
import type { ColumnsType } from 'antd/es/table'
import {
  ProjectOutlined,
  RobotOutlined,
  FileTextOutlined,
  DatabaseOutlined,
  CloudServerOutlined,
  ThunderboltOutlined,
  ApiOutlined,
  ToolOutlined,
  ReloadOutlined,
  EyeOutlined,
  PlayCircleOutlined,
  DollarOutlined,
  BarChartOutlined,
  PlusOutlined,
  FileSearchOutlined,
  DownloadOutlined,
  SettingOutlined,
  WarningOutlined,
  CheckCircleOutlined,
  ClockCircleOutlined,
  SyncOutlined,
  CloseCircleOutlined,
  MobileOutlined,
} from '@ant-design/icons'
import {
  getOverview,
  getScenarios,
  getTurns,
  queryLogs,
  taskTypeLabel,
  type LogEntry,
  type OverviewData,
  type ScenarioStat,
  type TokenTurn,
  type TokenTurnCall,
} from '../../services/observabilityService'
import { listProjects, type Project } from '../../services/projectService'
import './Dashboard.css'

const { Text } = Typography

type BucketStats = {
  calls: number
  input_tokens: number
  output_tokens: number
  cost_yuan: number
}

type BucketEntry = BucketStats & {
  name: string
  total_tokens: number
}

type StatCardConfig = {
  title: string
  value: string | number
  note: string
  icon: ReactNode
  color: string
}

type LoadErrors = Partial<Record<'overview' | 'turns' | 'scenarios' | 'logs' | 'projects', string>>

type SummaryMetric = {
  label: string
  value: string | number
  hint: string
  color: string
}

const LEVEL_COLORS: Record<string, string> = {
  debug: 'default',
  info: 'blue',
  notice: 'cyan',
  warning: 'orange',
  error: 'red',
}

const STATUS_META: Record<string, { color: string; label: string; icon: ReactNode }> = {
  completed: { color: 'green', label: '已完成', icon: <CheckCircleOutlined /> },
  success: { color: 'green', label: '已完成', icon: <CheckCircleOutlined /> },
  done: { color: 'green', label: '已完成', icon: <CheckCircleOutlined /> },
  running: { color: 'processing', label: '运行中', icon: <SyncOutlined spin /> },
  processing: { color: 'processing', label: '运行中', icon: <SyncOutlined spin /> },
  pending: { color: 'default', label: '等待中', icon: <ClockCircleOutlined /> },
  queued: { color: 'default', label: '排队中', icon: <ClockCircleOutlined /> },
  error: { color: 'red', label: '失败', icon: <CloseCircleOutlined /> },
  failed: { color: 'red', label: '失败', icon: <CloseCircleOutlined /> },
  cancelled: { color: 'default', label: '已取消', icon: <CloseCircleOutlined /> },
}

function formatNumber(value = 0): string {
  return Math.round(value).toLocaleString()
}

function compactNumber(value = 0): string {
  if (value >= 1_000_000) return `${(value / 1_000_000).toFixed(1)}M`
  if (value >= 1_000) return `${(value / 1_000).toFixed(1)}K`
  return formatNumber(value)
}

function formatCost(value = 0): string {
  return `¥${value.toFixed(4)}`
}

function formatPercent(value = 0): string {
  return `${Math.round(value)}%`
}

function formatDuration(ms = 0): string {
  if (ms >= 60_000) return `${(ms / 60_000).toFixed(1)}m`
  if (ms >= 1_000) return `${(ms / 1_000).toFixed(1)}s`
  return `${Math.round(ms)}ms`
}

function formatDateTime(value?: string | number): string {
  if (!value) return '-'
  const date = typeof value === 'number' ? new Date(value * 1000) : new Date(value)
  if (Number.isNaN(date.getTime())) return '-'
  return date.toLocaleString('zh-CN', {
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
  })
}

function bucketEntries(bucket?: Record<string, BucketStats>, limit = 6): BucketEntry[] {
  return Object.entries(bucket ?? {})
    .map(([name, stats]) => ({
      name,
      ...stats,
      total_tokens: (stats.input_tokens || 0) + (stats.output_tokens || 0),
    }))
    .sort((a, b) => b.total_tokens - a.total_tokens)
    .slice(0, limit)
}

function errorText(error: unknown): string {
  if (error instanceof Error) return error.message
  return '请求失败'
}

function SummaryStrip({ items }: { items: SummaryMetric[] }) {
  return (
    <div className="dashboard-summary-strip">
      {items.map((item) => (
        <div className="summary-metric" key={item.label}>
          <div className="summary-metric-head">
            <span className="summary-dot" style={{ background: item.color }} />
            <span>{item.label}</span>
          </div>
          <div className="summary-metric-value">{item.value}</div>
          <div className="summary-metric-hint">{item.hint}</div>
        </div>
      ))}
    </div>
  )
}

function TokenSplit({
  input,
  output,
  total,
}: {
  input: number
  output: number
  total: number
}) {
  const inputPercent = total > 0 ? Math.round((input / total) * 100) : 0
  const outputPercent = total > 0 ? Math.round((output / total) * 100) : 0

  return (
    <div className="token-split">
      <div className="token-split-row">
        <span>输入 Token</span>
        <strong>{formatNumber(input)}</strong>
      </div>
      <Progress percent={inputPercent} strokeColor="#1677ff" showInfo={false} />
      <div className="token-split-row">
        <span>输出 Token</span>
        <strong>{formatNumber(output)}</strong>
      </div>
      <Progress percent={outputPercent} strokeColor="#fa8c16" showInfo={false} />
      <div className="token-split-foot">
        <span>输入占比 {formatPercent(inputPercent)}</span>
        <span>输出占比 {formatPercent(outputPercent)}</span>
      </div>
    </div>
  )
}

function MetricBars({
  entries,
  metric,
  total,
}: {
  entries: BucketEntry[]
  metric: 'tokens' | 'calls'
  total: number
}) {
  if (entries.length === 0) {
    return <Empty description="暂无分布数据" image={Empty.PRESENTED_IMAGE_SIMPLE} />
  }

  const maxValue = Math.max(1, ...entries.map((item) => metric === 'tokens' ? item.total_tokens : item.calls))

  return (
    <div className="metric-bars">
      {entries.map((item) => {
        const value = metric === 'tokens' ? item.total_tokens : item.calls
        const percent = Math.max(4, Math.round((value / maxValue) * 100))
        const share = total > 0 ? Math.round((value / total) * 100) : 0
        return (
          <div className="metric-bar-row" key={item.name}>
            <div className="metric-bar-head">
              <Tooltip title={item.name}>
                <span className="metric-name">{item.name}</span>
              </Tooltip>
              <span className="metric-value">
                {metric === 'tokens' ? compactNumber(value) : formatNumber(value)}
              </span>
            </div>
            <div className="metric-bar-track">
              <div className="metric-bar-fill" style={{ width: `${percent}%` }} />
            </div>
            <div className="metric-bar-foot">
              <span>{item.calls} 次调用</span>
              <span>{formatPercent(share)} · {formatCost(item.cost_yuan)}</span>
            </div>
          </div>
        )
      })}
    </div>
  )
}

export default function Dashboard() {
  const navigate = useNavigate()
  const [overview, setOverview] = useState<OverviewData | null>(null)
  const [turns, setTurns] = useState<TokenTurn[]>([])
  const [scenarios, setScenarios] = useState<ScenarioStat[]>([])
  const [logs, setLogs] = useState<LogEntry[]>([])
  const [projects, setProjects] = useState<Project[]>([])
  const [projectTotal, setProjectTotal] = useState(0)
  const [loading, setLoading] = useState(true)
  const [bucketView, setBucketView] = useState<'model' | 'agent' | 'phase'>('model')
  const [loadErrors, setLoadErrors] = useState<LoadErrors>({})
  const [lastUpdatedAt, setLastUpdatedAt] = useState<number | null>(null)

  const fetchDashboard = useCallback(async (): Promise<boolean> => {
    setLoading(true)
    try {
      const [overviewResult, turnResult, scenarioResult, logResult, projectResult] = await Promise.allSettled([
        getOverview(),
        getTurns({ limit: 24 }),
        getScenarios(),
        queryLogs({ page: 1, page_size: 8, min_level: 'warning' }),
        listProjects({ page: 1, page_size: 8 }),
      ])

      const nextErrors: LoadErrors = {}

      if (overviewResult.status === 'fulfilled') {
        setOverview(overviewResult.value)
      } else {
        nextErrors.overview = errorText(overviewResult.reason)
      }

      if (turnResult.status === 'fulfilled') {
        setTurns(turnResult.value.items)
      } else {
        nextErrors.turns = errorText(turnResult.reason)
      }

      if (scenarioResult.status === 'fulfilled') {
        setScenarios(scenarioResult.value.items)
      } else {
        nextErrors.scenarios = errorText(scenarioResult.reason)
      }

      if (logResult.status === 'fulfilled') {
        setLogs(logResult.value.items)
      } else {
        nextErrors.logs = errorText(logResult.reason)
      }

      if (projectResult.status === 'fulfilled') {
        setProjects(projectResult.value.items)
        setProjectTotal(projectResult.value.total)
      } else {
        nextErrors.projects = errorText(projectResult.reason)
      }

      setLoadErrors(nextErrors)
      setLastUpdatedAt(Date.now())
      return Object.keys(nextErrors).length === 0
    } catch (error) {
      console.error('加载仪表盘数据失败:', error)
      message.error('加载仪表盘数据失败')
      setLoadErrors({ overview: errorText(error) })
      return false
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    void fetchDashboard()
  }, [fetchDashboard])

  const handleRefresh = async () => {
    const ok = await fetchDashboard()
    if (ok) {
      message.success('数据已刷新')
    } else {
      message.warning('部分数据加载失败，已保留可用数据')
    }
  }

  const token = overview?.token
  const tasks = overview?.tasks
  const warnErrorCount = useMemo(() => {
    const byLevel = overview?.logs.by_level ?? {}
    return (byLevel.warning || 0) + (byLevel.error || 0)
  }, [overview])

  const taskStatus = tasks?.by_status ?? {}
  const completedCount = (taskStatus.completed || 0) + (taskStatus.success || 0) + (taskStatus.done || 0)
  const runningCount = (taskStatus.running || 0) + (taskStatus.processing || 0)
  const failedCount = (taskStatus.error || 0) + (taskStatus.failed || 0)
  const totalTokens = token?.total_tokens ?? 0
  const totalCalls = token?.total_calls ?? 0
  const avgTokensPerCall = totalCalls > 0 ? Math.round(totalTokens / totalCalls) : 0
  const taskCompletionRate = tasks?.total ? Math.round((completedCount / tasks.total) * 100) : 0
  const topModel = useMemo(() => bucketEntries(token?.by_model, 1)[0], [token])
  const lastTurn = turns[0]
  const loadErrorEntries = Object.entries(loadErrors)

  const summaryMetrics = useMemo<SummaryMetric[]>(() => [
    {
      label: 'Token 总量',
      value: compactNumber(totalTokens),
      hint: `${formatNumber(token?.total_input_tokens ?? 0)} 输入 / ${formatNumber(token?.total_output_tokens ?? 0)} 输出`,
      color: '#d48806',
    },
    {
      label: 'LLM 调用',
      value: formatNumber(totalCalls),
      hint: totalCalls > 0 ? `平均 ${formatNumber(avgTokensPerCall)} Token/次` : '暂无调用记录',
      color: '#722ed1',
    },
    {
      label: '任务完成率',
      value: formatPercent(taskCompletionRate),
      hint: `${completedCount} 完成 / ${runningCount} 运行 / ${failedCount} 失败`,
      color: failedCount > 0 ? '#cf1322' : '#389e0d',
    },
    {
      label: 'Top 模型',
      value: topModel?.name ?? '-',
      hint: topModel ? `${compactNumber(topModel.total_tokens)} Token · ${topModel.calls} 次` : '暂无模型分布',
      color: '#1677ff',
    },
    {
      label: '最近轮次',
      value: lastTurn ? compactNumber(lastTurn.total_tokens) : '-',
      hint: lastTurn ? formatDateTime(lastTurn.ended_at) : '暂无轮次记录',
      color: '#13a8a8',
    },
    {
      label: '最后刷新',
      value: lastUpdatedAt ? formatDateTime(lastUpdatedAt / 1000) : '-',
      hint: loadErrorEntries.length > 0 ? `${loadErrorEntries.length} 个数据源异常` : '所有数据源正常',
      color: loadErrorEntries.length > 0 ? '#d46b08' : '#52c41a',
    },
  ], [
    avgTokensPerCall,
    completedCount,
    failedCount,
    lastTurn,
    lastUpdatedAt,
    loadErrorEntries.length,
    runningCount,
    taskCompletionRate,
    token,
    topModel,
    totalCalls,
    totalTokens,
  ])

  const statsCards = useMemo<StatCardConfig[]>(() => {
    const avgDuration = totalCalls > 0 ? (token?.total_duration_ms ?? 0) / totalCalls : 0
    return [
      { title: '项目总数', value: projectTotal, note: '来自项目 API', icon: <ProjectOutlined />, color: '#1677ff' },
      { title: '任务总数', value: tasks?.total ?? 0, note: '全局任务状态', icon: <FileTextOutlined />, color: '#13a8a8' },
      { title: 'LLM 调用', value: totalCalls, note: '历史记录 + 当前运行', icon: <ApiOutlined />, color: '#722ed1' },
      { title: 'Token 总量', value: compactNumber(totalTokens), note: '输入 + 输出', icon: <ThunderboltOutlined />, color: '#d48806' },
      { title: '累计费用', value: formatCost(token?.total_cost_yuan ?? 0), note: '按模型计价估算', icon: <DollarOutlined />, color: '#cf1322' },
      { title: '平均耗时', value: formatDuration(avgDuration), note: '单次 LLM 平均', icon: <ClockCircleOutlined />, color: '#389e0d' },
      { title: '最近轮次', value: turns.length, note: '最多展示 24 条', icon: <BarChartOutlined />, color: '#0958d9' },
      { title: '告警/错误', value: warnErrorCount, note: '内存日志聚合', icon: <WarningOutlined />, color: '#d46b08' },
      { title: 'Agent 数', value: Object.keys(token?.by_agent ?? {}).length, note: '有 token 记录的 agent', icon: <RobotOutlined />, color: '#531dab' },
    ]
  }, [projectTotal, tasks, token, totalCalls, totalTokens, turns.length, warnErrorCount])

  const activeBucketEntries = useMemo(() => {
    if (bucketView === 'agent') return bucketEntries(token?.by_agent)
    if (bucketView === 'phase') return bucketEntries(token?.by_phase)
    return bucketEntries(token?.by_model)
  }, [bucketView, token])

  const activeBucketTotal = totalTokens

  const taskStatusEntries = useMemo(() => {
    return Object.entries(tasks?.by_status ?? {}).sort((a, b) => b[1] - a[1])
  }, [tasks])

  const turnColumns: ColumnsType<TokenTurn> = [
    {
      title: '时间',
      dataIndex: 'ended_at',
      key: 'ended_at',
      width: 120,
      render: (value: number) => <Text type="secondary">{formatDateTime(value)}</Text>,
    },
    {
      title: '项目 / 任务',
      key: 'scope',
      width: 180,
      render: (_, record) => (
        <Space orientation="vertical" size={0}>
          <Tooltip title={record.project_id || '无项目'}>
            <Text code>{record.project_id ? record.project_id.slice(0, 12) : '-'}</Text>
          </Tooltip>
          <Tooltip title={record.task_id || '无任务'}>
            <Text type="secondary">{record.task_id ? record.task_id.slice(0, 12) : '-'}</Text>
          </Tooltip>
        </Space>
      ),
    },
    {
      title: 'Token',
      dataIndex: 'total_tokens',
      key: 'total_tokens',
      width: 120,
      render: (value: number, record) => (
        <Space orientation="vertical" size={0}>
          <Text strong>{formatNumber(value)}</Text>
          <Text type="secondary">{formatNumber(record.total_input_tokens)} / {formatNumber(record.total_output_tokens)}</Text>
        </Space>
      ),
    },
    {
      title: '模型',
      key: 'models',
      render: (_, record) => {
        const models = Object.keys(record.by_model)
        return models.length === 0 ? '-' : models.slice(0, 2).map((model) => <Tag key={model}>{model}</Tag>)
      },
    },
    {
      title: 'Agent / 阶段',
      key: 'agent_phase',
      render: (_, record) => {
        const agents = Object.keys(record.by_agent)
        const phases = Object.keys(record.by_phase)
        return (
          <Space wrap size={[0, 4]}>
            {agents.slice(0, 2).map((agent) => <Tag color="purple" key={agent}>{agent}</Tag>)}
            {phases.slice(0, 2).map((phase) => <Tag color="cyan" key={phase}>{phase}</Tag>)}
            {agents.length === 0 && phases.length === 0 ? '-' : null}
          </Space>
        )
      },
    },
    {
      title: '费用',
      dataIndex: 'total_cost_yuan',
      key: 'cost',
      width: 100,
      render: (value: number) => <Text type="warning">{formatCost(value)}</Text>,
    },
    {
      title: '耗时',
      dataIndex: 'total_duration_ms',
      key: 'duration',
      width: 90,
      render: (value: number) => formatDuration(value),
    },
  ]

  const turnCallColumns: ColumnsType<TokenTurnCall> = [
    {
      title: '#',
      dataIndex: 'call_index',
      key: 'idx',
      width: 56,
      render: (value: number) => <Text type="secondary">{value}</Text>,
    },
    {
      title: '时间',
      dataIndex: 'timestamp',
      key: 'timestamp',
      width: 116,
      render: (value: number) => <Text type="secondary">{formatDateTime(value)}</Text>,
    },
    {
      title: '模型',
      dataIndex: 'model',
      key: 'model',
      render: (value: string) => value ? <Tag>{value}</Tag> : '-',
    },
    {
      title: 'Agent / 阶段',
      key: 'scope',
      render: (_, record) => (
        <Space wrap size={[0, 4]}>
          {record.agent ? <Tag color="purple">{record.agent}</Tag> : null}
          {record.phase ? <Tag color="cyan">{record.phase}</Tag> : null}
          {!record.agent && !record.phase ? '-' : null}
        </Space>
      ),
    },
    {
      title: 'Token',
      dataIndex: 'total_tokens',
      key: 'tokens',
      width: 136,
      render: (value: number, record) => (
        <Space orientation="vertical" size={0}>
          <Text strong>{formatNumber(value)}</Text>
          <Text type="secondary">{formatNumber(record.input_tokens)} / {formatNumber(record.output_tokens)}</Text>
        </Space>
      ),
    },
    {
      title: '费用',
      dataIndex: 'cost_yuan',
      key: 'cost',
      width: 96,
      render: (value: number) => <Text type="warning">{formatCost(value)}</Text>,
    },
    {
      title: '耗时',
      dataIndex: 'duration_ms',
      key: 'duration',
      width: 84,
      render: (value: number) => formatDuration(value),
    },
    {
      title: 'Run',
      dataIndex: 'run_id',
      key: 'run_id',
      width: 120,
      render: (value: string) => value ? <Tooltip title={value}><Text code>{value.slice(0, 10)}</Text></Tooltip> : '-',
    },
  ]

  const logColumns: ColumnsType<LogEntry> = [
    {
      title: '时间',
      dataIndex: 'ts',
      key: 'ts',
      width: 110,
      render: (value: number) => <Text type="secondary">{formatDateTime(value)}</Text>,
    },
    {
      title: '级别',
      dataIndex: 'level',
      key: 'level',
      width: 76,
      render: (level: string) => <Tag color={LEVEL_COLORS[level] || 'default'}>{level}</Tag>,
    },
    {
      title: '来源',
      dataIndex: 'source',
      key: 'source',
      width: 120,
      render: (source: string) => <Text code>{source}</Text>,
    },
    {
      title: '消息',
      dataIndex: 'message',
      key: 'message',
      ellipsis: true,
    },
  ]

  const projectColumns: ColumnsType<Project> = [
    {
      title: '项目',
      dataIndex: 'name',
      key: 'name',
      render: (name: string, record) => (
        <Space orientation="vertical" size={0}>
          <Text strong>{name}</Text>
          <Text type="secondary">{record.description || '暂无描述'}</Text>
        </Space>
      ),
    },
    {
      title: '创建时间',
      dataIndex: 'created_at',
      key: 'created_at',
      width: 140,
      render: (value: string) => formatDateTime(value),
    },
    {
      title: '更新时间',
      dataIndex: 'updated_at',
      key: 'updated_at',
      width: 140,
      render: (value: string) => formatDateTime(value),
    },
    {
      title: '操作',
      key: 'action',
      width: 96,
      render: (_, record) => (
        <Tooltip title="查看项目">
          <Button type="text" size="small" icon={<EyeOutlined />} onClick={() => navigate(`/projects/${record.id}`)} />
        </Tooltip>
      ),
    },
  ]

  const scenarioColumns: ColumnsType<ScenarioStat> = [
    {
      title: '任务场景',
      dataIndex: 'task_type',
      key: 'task_type',
      render: (value: string) => (
        <Space orientation="vertical" size={0}>
          <Text strong>{taskTypeLabel(value)}</Text>
          <Text type="secondary">{value}</Text>
        </Space>
      ),
    },
    {
      title: '任务数',
      key: 'tasks',
      width: 200,
      render: (_, record) => {
        const entries = Object.entries(record.tasks.by_status ?? {}).sort((a, b) => b[1] - a[1])
        return (
          <Space orientation="vertical" size={2}>
            <Text strong>{record.tasks.total}</Text>
            <Space wrap size={[0, 4]}>
              {entries.length === 0 ? <Text type="secondary">-</Text> : entries.map(([status, count]) => {
                const meta = STATUS_META[status] || { color: 'default', label: status, icon: <ClockCircleOutlined /> }
                return <Tag color={meta.color} key={status}>{meta.label} {count}</Tag>
              })}
            </Space>
          </Space>
        )
      },
    },
    {
      title: 'Token',
      key: 'tokens',
      width: 140,
      render: (_, record) => (
        <Space orientation="vertical" size={0}>
          <Text strong>{formatNumber(record.token.total_tokens)}</Text>
          <Text type="secondary">{formatNumber(record.token.total_input_tokens)} / {formatNumber(record.token.total_output_tokens)}</Text>
        </Space>
      ),
    },
    {
      title: '调用',
      key: 'calls',
      width: 80,
      render: (_, record) => formatNumber(record.token.total_calls),
    },
    {
      title: '费用',
      key: 'cost',
      width: 100,
      render: (_, record) => <Text type="warning">{formatCost(record.token.total_cost_yuan)}</Text>,
    },
  ]

  return (
    <div className="dashboard-container page-container fade-in">
      <div className="page-header dashboard-hero slide-up">
        <div className="hero-copy">
          <div className="hero-badge">全局观测</div>
          <h1 className="page-title">仪表盘</h1>
          <p className="page-description">项目、任务、Token 轮次和运行日志的实时运营视图</p>
        </div>
        <div className="hero-actions">
          <Text type="secondary" className="dashboard-refresh-time">
            {lastUpdatedAt ? `更新 ${formatDateTime(lastUpdatedAt / 1000)}` : '等待数据'}
          </Text>
          <Button icon={<ReloadOutlined />} onClick={handleRefresh} loading={loading} type="primary">
            刷新数据
          </Button>
        </div>
      </div>

      {loadErrorEntries.length > 0 ? (
        <Alert
          className="dashboard-alert"
          type="warning"
          showIcon
          message="部分数据源加载失败"
          description={loadErrorEntries.map(([key, text]) => `${key}: ${text}`).join('；')}
        />
      ) : null}

      <SummaryStrip items={summaryMetrics} />

      <Row gutter={[16, 16]} className="dashboard-stat-grid slide-up stagger-1">
        {statsCards.map((stat) => (
          <Col xs={24} sm={12} md={8} xl={8} key={stat.title}>
            <Card className="stat-card glass-card hover-float">
              <div className="stat-content">
                <div className="stat-icon" style={{ background: `${stat.color}16`, color: stat.color }}>
                  {stat.icon}
                </div>
                <div className="stat-info">
                  <div className="stat-title">{stat.title}</div>
                  <Statistic value={stat.value} styles={{ content: { color: 'var(--text-primary)', fontSize: 24, fontWeight: 700 } }} />
                  <div className="stat-trend">
                    <span className="trend-label">{stat.note}</span>
                  </div>
                </div>
              </div>
            </Card>
          </Col>
        ))}
      </Row>

      <Row gutter={[16, 16]} className="dashboard-row">
        <Col xs={24} lg={14}>
          <Card
            title={<Space><BarChartOutlined />Token 用量分布</Space>}
            className="glass-card hover-lift dashboard-panel"
            extra={
              <Segmented
                size="small"
                value={bucketView}
                onChange={(value) => setBucketView(value as 'model' | 'agent' | 'phase')}
                options={[
                  { label: '模型', value: 'model' },
                  { label: 'Agent', value: 'agent' },
                  { label: '阶段', value: 'phase' },
                ]}
              />
            }
          >
            {loading ? <Skeleton active /> : (
              <div className="token-panel-stack">
                <div className="token-distribution-summary">
                  <div className="token-total-block">
                    <span>累计 Token</span>
                    <strong>{formatNumber(totalTokens)}</strong>
                    <small>{formatNumber(totalCalls)} 次 LLM 调用 · {formatCost(token?.total_cost_yuan ?? 0)}</small>
                  </div>
                  <TokenSplit
                    input={token?.total_input_tokens ?? 0}
                    output={token?.total_output_tokens ?? 0}
                    total={totalTokens}
                  />
                </div>
                <MetricBars entries={activeBucketEntries} metric="tokens" total={activeBucketTotal} />
              </div>
            )}
          </Card>
        </Col>

        <Col xs={24} lg={10}>
          <Card title={<Space><CloudServerOutlined />任务与日志状态</Space>} className="glass-card hover-lift dashboard-panel">
            {loading ? <Skeleton active /> : (
              <div className="status-grid">
                <div className="status-block">
                  <div className="section-title">任务状态</div>
                  <div className="status-overview">
                    <div>
                      <span>完成率</span>
                      <strong>{formatPercent(taskCompletionRate)}</strong>
                    </div>
                    <div>
                      <span>运行中</span>
                      <strong>{runningCount}</strong>
                    </div>
                    <div>
                      <span>失败</span>
                      <strong>{failedCount}</strong>
                    </div>
                  </div>
                  {taskStatusEntries.length === 0 ? <Empty description="暂无任务状态" image={Empty.PRESENTED_IMAGE_SIMPLE} /> : (
                    <div className="status-list">
                      {taskStatusEntries.map(([status, count]) => {
                        const meta = STATUS_META[status] || { color: 'default', label: status, icon: <ClockCircleOutlined /> }
                        const percent = tasks?.total ? Math.round((count / tasks.total) * 100) : 0
                        return (
                          <div className="status-item" key={status}>
                            <div className="status-head">
                              <Tag color={meta.color} icon={meta.icon}>{meta.label}</Tag>
                              <Text strong>{count}</Text>
                            </div>
                            <Progress percent={percent} size="small" showInfo={false} />
                          </div>
                        )
                      })}
                    </div>
                  )}
                </div>
                <div className="status-block">
                  <div className="section-title">日志级别</div>
                  <div className="log-level-grid">
                    {Object.entries(overview?.logs.by_level ?? {}).length === 0 ? (
                      <Empty description="暂无日志统计" image={Empty.PRESENTED_IMAGE_SIMPLE} />
                    ) : Object.entries(overview?.logs.by_level ?? {}).map(([level, count]) => (
                      <div className="log-level-item" key={level}>
                        <Tag color={LEVEL_COLORS[level] || 'default'}>{level}</Tag>
                        <Text strong>{count}</Text>
                      </div>
                    ))}
                  </div>
                </div>
              </div>
            )}
          </Card>
        </Col>
      </Row>

      <Row gutter={[16, 16]} className="dashboard-row">
        <Col xs={24}>
          <Card
            title={<Space><ApiOutlined />任务场景</Space>}
            className="glass-card hover-lift dashboard-panel"
            extra={<Text type="secondary">按场景聚合 Token 与任务，点击行查看该场景轮次</Text>}
          >
            <Table<ScenarioStat>
              loading={loading}
              dataSource={scenarios}
              columns={scenarioColumns}
              rowKey="task_type"
              size="small"
              pagination={false}
              scroll={{ x: 720 }}
              onRow={(record) => ({
                style: { cursor: 'pointer' },
                onClick: () => navigate(`/observability?task_type=${encodeURIComponent(record.task_type)}`),
              })}
              locale={{ emptyText: <Empty description="暂无场景数据" image={Empty.PRESENTED_IMAGE_SIMPLE} /> }}
            />
          </Card>
        </Col>
      </Row>

      <Row gutter={[16, 16]} className="dashboard-row">
        <Col xs={24} xl={16}>
          <Card
            title={<Space><ThunderboltOutlined />最近 Token 轮次</Space>}
            className="glass-card hover-lift dashboard-panel"
            extra={<Button type="link" size="small" icon={<EyeOutlined />} onClick={() => navigate('/observability')}>系统观测</Button>}
          >
            <Table<TokenTurn>
              loading={loading}
              dataSource={turns}
              columns={turnColumns}
              rowKey="turn_key"
              size="small"
              pagination={{ pageSize: 8, showSizeChanger: false }}
              scroll={{ x: 860 }}
              expandable={{
                rowExpandable: (record) => (record.calls?.length ?? 0) > 0,
                expandedRowRender: (record) => (
                  <Table<TokenTurnCall>
                    className="turn-call-table"
                    dataSource={record.calls ?? []}
                    columns={turnCallColumns}
                    rowKey={(call) => `${record.turn_key}-${call.call_index}-${call.run_id || call.timestamp}`}
                    size="small"
                    pagination={false}
                    scroll={{ x: 820 }}
                  />
                ),
              }}
              locale={{ emptyText: <Empty description="暂无轮次记录" image={Empty.PRESENTED_IMAGE_SIMPLE} /> }}
            />
          </Card>
        </Col>

        <Col xs={24} xl={8}>
          <Card title={<Space><WarningOutlined />近期告警/错误</Space>} className="glass-card hover-lift dashboard-panel">
            <Table<LogEntry>
              loading={loading}
              dataSource={logs}
              columns={logColumns}
              rowKey="log_id"
              size="small"
              pagination={false}
              scroll={{ x: 520 }}
              locale={{ emptyText: <Empty description="近期无告警" image={Empty.PRESENTED_IMAGE_SIMPLE} /> }}
            />
          </Card>
        </Col>
      </Row>

      <Row gutter={[16, 16]} className="dashboard-row">
        <Col xs={24} xl={16}>
          <Card
            title={<Space><ProjectOutlined />最近项目</Space>}
            className="glass-card hover-lift dashboard-panel"
            extra={<Button type="link" size="small" onClick={() => navigate('/projects')}>全部项目</Button>}
          >
            <Table<Project>
              loading={loading}
              dataSource={projects}
              columns={projectColumns}
              rowKey="id"
              size="small"
              pagination={false}
              locale={{ emptyText: <Empty description="暂无项目" image={Empty.PRESENTED_IMAGE_SIMPLE} /> }}
            />
          </Card>
        </Col>

        <Col xs={24} xl={8}>
          <Card title={<Space><DatabaseOutlined />资源覆盖</Space>} className="glass-card hover-lift dashboard-panel">
            {loading ? <Skeleton active /> : (
              <div className="resource-stats">
                <div className="resource-item">
                  <div className="flex-between">
                    <span className="resource-label">模型覆盖</span>
                    <span className="resource-value">{Object.keys(token?.by_model ?? {}).length}</span>
                  </div>
                  <Progress percent={Math.min(100, Object.keys(token?.by_model ?? {}).length * 20)} strokeColor="#1677ff" showInfo={false} />
                </div>
                <div className="resource-item">
                  <div className="flex-between">
                    <span className="resource-label">Agent 覆盖</span>
                    <span className="resource-value">{Object.keys(token?.by_agent ?? {}).length}</span>
                  </div>
                  <Progress percent={Math.min(100, Object.keys(token?.by_agent ?? {}).length * 12)} strokeColor="#722ed1" showInfo={false} />
                </div>
                <div className="resource-item">
                  <div className="flex-between">
                    <span className="resource-label">阶段覆盖</span>
                    <span className="resource-value">{Object.keys(token?.by_phase ?? {}).length}</span>
                  </div>
                  <Progress percent={Math.min(100, Object.keys(token?.by_phase ?? {}).length * 14)} strokeColor="#13a8a8" showInfo={false} />
                </div>
                <div className="resource-item">
                  <div className="flex-between">
                    <span className="resource-label">运行健康度</span>
                    <span className="resource-value">{warnErrorCount === 0 ? '稳定' : `${warnErrorCount} 条告警`}</span>
                  </div>
                  <Progress percent={warnErrorCount === 0 ? 100 : Math.max(10, 100 - warnErrorCount * 8)} strokeColor={warnErrorCount === 0 ? '#52c41a' : '#faad14'} showInfo={false} />
                </div>
              </div>
            )}
          </Card>
        </Col>
      </Row>

      <Row gutter={[16, 16]} className="dashboard-row">
        <Col span={24}>
          <Card title={<Space><ToolOutlined />快速操作</Space>} className="glass-card dashboard-panel">
            <div className="quick-actions">
              <button className="quick-action-btn" onClick={() => navigate('/projects')}>
                <PlusOutlined /> 创建新项目
              </button>
              <button className="quick-action-btn" onClick={() => navigate('/phone-control')}>
                <MobileOutlined /> 手机控制
              </button>
              <button className="quick-action-btn" onClick={() => navigate('/ai-tools')}>
                <RobotOutlined /> AI 工具
              </button>
              <button className="quick-action-btn" onClick={() => navigate('/observability')}>
                <FileSearchOutlined /> 系统观测
              </button>
              <button className="quick-action-btn" onClick={() => navigate('/settings/config')}>
                <SettingOutlined /> 配置管理
              </button>
              <button className="quick-action-btn" onClick={() => message.info('请在项目详情页导出对应数据')}>
                <DownloadOutlined /> 导出数据
              </button>
              <button className="quick-action-btn" onClick={() => navigate('/agents')}>
                <PlayCircleOutlined /> Agent 管理
              </button>
            </div>
          </Card>
        </Col>
      </Row>
    </div>
  )
}
