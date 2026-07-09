import { useState, useEffect, useCallback, useMemo } from 'react'
import {
  Tabs,
  Card,
  Row,
  Col,
  Statistic,
  Table,
  Tag,
  Typography,
  Space,
  Select,
  Button,
  Spin,
  Empty,
  Tooltip,
  Input,
  message,
  Segmented,
} from 'antd'
import type { ColumnsType } from 'antd/es/table'
import {
  DashboardOutlined,
  DollarOutlined,
  ThunderboltOutlined,
  FileTextOutlined,
  WarningOutlined,
  CheckCircleOutlined,
  CloseCircleOutlined,
  ClockCircleOutlined,
  SyncOutlined,
  ReloadOutlined,
  EyeOutlined,
  ApiOutlined,
  RobotOutlined,
  ExperimentOutlined,
} from '@ant-design/icons'
import {
  getHierarchy,
  getOverview,
  getScenarios,
  getTurns,
  queryLogs,
  taskTypeLabel,
  type OverviewData,
  type LogEntry,
  type LogQueryParams,
  type ScenarioStat,
  type TokenHierarchy,
  type TokenStats,
  type TokenTurn,
  type TokenTurnCall,
} from '../../services/observabilityService'
import { useSearchParams } from 'react-router-dom'
import './Observability.css'

const { Title, Text } = Typography

const LEVEL_COLORS: Record<string, string> = {
  debug: 'default',
  info: 'blue',
  notice: 'cyan',
  warning: 'orange',
  error: 'red',
}

const STATUS_CONFIG: Record<string, { color: string; icon: React.ReactNode }> = {
  completed: { color: 'green', icon: <CheckCircleOutlined /> },
  running: { color: 'processing', icon: <SyncOutlined spin /> },
  pending: { color: 'default', icon: <ClockCircleOutlined /> },
  error: { color: 'red', icon: <CloseCircleOutlined /> },
}

type HierarchyRow = {
  key: string
  level: '项目' | '任务' | '阶段'
  name: string
  project_id?: string
  task_id?: string
  phase?: string
  stats: TokenStats
  children?: HierarchyRow[]
}

export default function Observability() {
  const [searchParams] = useSearchParams()
  const scenarioParam = searchParams.get('task_type') || ''
  const [activeTab, setActiveTab] = useState(scenarioParam ? 'scenarios' : 'overview')
  const [overview, setOverview] = useState<OverviewData | null>(null)
  const [loading, setLoading] = useState(true)
  const [scenarios, setScenarios] = useState<ScenarioStat[]>([])
  const [scenarioLoading, setScenarioLoading] = useState(false)
  const [turnLoading, setTurnLoading] = useState(false)
  const [turns, setTurns] = useState<TokenTurn[]>([])
  const [turnScope, setTurnScope] = useState<'all' | 'project' | 'task'>('all')
  const [turnProjectId, setTurnProjectId] = useState('')
  const [turnTaskId, setTurnTaskId] = useState('')
  const [hierarchyLoading, setHierarchyLoading] = useState(false)
  const [hierarchy, setHierarchy] = useState<TokenHierarchy | null>(null)
  const [logLoading, setLogLoading] = useState(false)
  const [logs, setLogs] = useState<LogEntry[]>([])
  const [logTotal, setLogTotal] = useState(0)
  const [logPage, setLogPage] = useState(1)
  const [logFilters, setLogFilters] = useState<LogQueryParams>({
    page: 1,
    page_size: 20,
    min_level: '',
    source: '',
  })

  const fetchOverview = useCallback(async () => {
    setLoading(true)
    try {
      const data = await getOverview()
      setOverview(data)
    } catch (e) {
      message.error('加载观测数据失败')
    } finally {
      setLoading(false)
    }
  }, [])

  const fetchLogs = useCallback(async (params?: Partial<LogQueryParams>) => {
    setLogLoading(true)
    try {
      const mergedParams = { ...logFilters, ...params }
      const data = await queryLogs(mergedParams)
      setLogs(data.items)
      setLogTotal(data.total)
      setLogPage(mergedParams.page || 1)
    } catch (e) {
      message.error('加载日志失败')
    } finally {
      setLogLoading(false)
    }
  }, [logFilters])

  const fetchTurns = useCallback(async () => {
    setTurnLoading(true)
    try {
      const data = await getTurns({
        limit: 100,
        project_id: turnScope !== 'all' ? turnProjectId.trim() : undefined,
        task_id: turnScope === 'task' ? turnTaskId.trim() : undefined,
      })
      setTurns(data.items)
    } catch (e) {
      message.error('加载轮次数据失败')
    } finally {
      setTurnLoading(false)
    }
  }, [turnProjectId, turnScope, turnTaskId])

  const fetchHierarchy = useCallback(async () => {
    setHierarchyLoading(true)
    try {
      const data = await getHierarchy()
      setHierarchy(data)
    } catch (e) {
      message.error('加载层级数据失败')
    } finally {
      setHierarchyLoading(false)
    }
  }, [])

  const fetchScenarios = useCallback(async () => {
    setScenarioLoading(true)
    try {
      const data = await getScenarios()
      setScenarios(data.items)
    } catch (e) {
      message.error('加载场景数据失败')
    } finally {
      setScenarioLoading(false)
    }
  }, [])

  useEffect(() => {
    fetchOverview()
  }, [fetchOverview])

  useEffect(() => {
    if (activeTab === 'logs') {
      fetchLogs()
    }
    if (activeTab === 'turns') {
      fetchTurns()
    }
    if (activeTab === 'hierarchy') {
      fetchHierarchy()
    }
    if (activeTab === 'scenarios') {
      fetchScenarios()
    }
  }, [activeTab, fetchHierarchy, fetchLogs, fetchTurns, fetchScenarios])

  const formatCost = (v: number) => `¥${v.toFixed(4)}`
  const formatTokens = (v: number) => v >= 1000 ? `${(v / 1000).toFixed(1)}K` : String(v)
  const formatDuration = (ms = 0) => {
    if (ms >= 60000) return `${(ms / 60000).toFixed(1)}m`
    if (ms >= 1000) return `${(ms / 1000).toFixed(1)}s`
    return `${Math.round(ms)}ms`
  }
  const formatTime = (ts: number) => new Date(ts * 1000).toLocaleString('zh-CN', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', second: '2-digit' })

  const hierarchyRows = useMemo<HierarchyRow[]>(() => {
    if (!hierarchy) return []
    return Object.entries(hierarchy.projects ?? {}).map(([projectId, project]) => ({
      key: `project:${projectId}`,
      level: '项目',
      name: projectId,
      project_id: projectId,
      stats: project.stats,
      children: Object.entries(project.tasks ?? {}).map(([taskId, task]) => ({
        key: `task:${projectId}:${taskId}`,
        level: '任务',
        name: taskId,
        project_id: projectId,
        task_id: taskId,
        stats: task.stats,
        children: Object.entries(task.phases ?? {}).map(([phase, stats]) => ({
          key: `phase:${projectId}:${taskId}:${phase}`,
          level: '阶段',
          name: phase,
          project_id: projectId,
          task_id: taskId,
          phase,
          stats,
        })),
      })),
    }))
  }, [hierarchy])

  const renderOverview = () => {
    if (!overview) return <Empty description="暂无数据" />
    const { token, tasks, logs: logStats } = overview

    return (
      <div className="tab-content">
        <Row gutter={[16, 16]} style={{ marginBottom: 24 }}>
          <Col xs={24} sm={12} lg={6}>
            <Card className="glass-card stat-card">
              <Statistic title="累计调用" value={token.total_calls} prefix={<ApiOutlined />} styles={{ content: { fontWeight: 700 } }} />
            </Card>
          </Col>
          <Col xs={24} sm={12} lg={6}>
            <Card className="glass-card stat-card">
              <Statistic title="累计 Token" value={token.total_tokens} prefix={<ThunderboltOutlined />} formatter={(v) => formatTokens(Number(v))} styles={{ content: { fontWeight: 700 } }} />
            </Card>
          </Col>
          <Col xs={24} sm={12} lg={6}>
            <Card className="glass-card stat-card">
              <Statistic title="累计费用" value={token.total_cost_yuan} prefix={<DollarOutlined />} precision={4} styles={{ content: { fontWeight: 700, color: '#faad14' } }} />
            </Card>
          </Col>
          <Col xs={24} sm={12} lg={6}>
            <Card className="glass-card stat-card">
              <Statistic title="任务总数" value={tasks.total} prefix={<ExperimentOutlined />} styles={{ content: { fontWeight: 700 } }} />
            </Card>
          </Col>
        </Row>

        <Row gutter={[16, 16]} style={{ marginBottom: 24 }}>
          <Col xs={24} lg={12}>
            <Card className="glass-card" title={<Space><RobotOutlined />模型用量分布</Space>} size="small">
              {Object.keys(token.by_model).length === 0 ? <Empty description="暂无模型数据" image={Empty.PRESENTED_IMAGE_SIMPLE} /> : (
                <Table
                  dataSource={Object.entries(token.by_model).map(([model, stats]) => ({ model, ...stats }))}
                  columns={[
                    { title: '模型', dataIndex: 'model', key: 'model', render: (t: string) => <Tag>{t}</Tag> },
                    { title: '调用次数', dataIndex: 'calls', key: 'calls' },
                    { title: '输入 Token', dataIndex: 'input_tokens', key: 'input', render: (v: number) => formatTokens(v) },
                    { title: '输出 Token', dataIndex: 'output_tokens', key: 'output', render: (v: number) => formatTokens(v) },
                    { title: '费用', dataIndex: 'cost_yuan', key: 'cost', render: (v: number) => <Text type="warning">{formatCost(v)}</Text> },
                  ] as ColumnsType<any>}
                  rowKey="model" pagination={false} size="small"
                />
              )}
            </Card>
          </Col>
          <Col xs={24} lg={12}>
            <Card className="glass-card" title={<Space><DashboardOutlined />任务状态分布</Space>} size="small">
              {Object.keys(tasks.by_status).length === 0 ? <Empty description="暂无任务" image={Empty.PRESENTED_IMAGE_SIMPLE} /> : (
                <Row gutter={[16, 16]}>
                  {Object.entries(tasks.by_status).map(([status, count]) => {
                    const cfg = STATUS_CONFIG[status] || { color: 'default', icon: <ClockCircleOutlined /> }
                    return (
                      <Col span={12} key={status}>
                        <Card size="small" className="glass-card">
                          <Statistic
                            title={status}
                            value={count}
                            prefix={cfg.icon}
                            styles={{ content: { fontSize: 20, fontWeight: 700 } }}
                          />
                        </Card>
                      </Col>
                    )
                  })}
                </Row>
              )}
            </Card>
          </Col>
        </Row>

        <Row gutter={[16, 16]}>
          <Col xs={24} lg={12}>
            <Card className="glass-card" title={<Space><WarningOutlined />近期告警/错误</Space>} size="small">
              {logStats.recent_warn_error.length === 0 ? <Empty description="近期无告警" image={Empty.PRESENTED_IMAGE_SIMPLE} /> : (
                <Table
                  dataSource={logStats.recent_warn_error}
                  columns={[
                    { title: '时间', dataIndex: 'ts', key: 'ts', width: 140, render: (v: number) => <Text type="secondary" style={{ fontSize: 12 }}>{formatTime(v)}</Text> },
                    { title: '级别', dataIndex: 'level', key: 'level', width: 80, render: (l: string) => <Tag color={LEVEL_COLORS[l] || 'default'}>{l}</Tag> },
                    { title: '来源', dataIndex: 'source', key: 'source', width: 120, render: (s: string) => <Text code style={{ fontSize: 11 }}>{s}</Text> },
                    { title: '消息', dataIndex: 'message', key: 'message', ellipsis: true },
                  ] as ColumnsType<LogEntry>}
                  rowKey="log_id" pagination={false} size="small"
                />
              )}
            </Card>
          </Col>
          <Col xs={24} lg={12}>
            <Card className="glass-card" title={<Space><CloseCircleOutlined />近期失败任务</Space>} size="small">
              {overview.recent_failed_tasks.length === 0 ? <Empty description="近期无失败" image={Empty.PRESENTED_IMAGE_SIMPLE} /> : (
                <Table
                  dataSource={overview.recent_failed_tasks}
                  columns={[
                    { title: '任务 ID', dataIndex: 'task_id', key: 'tid', width: 120, render: (v: string) => <Text code style={{ fontSize: 11 }}>{v?.slice(0, 8)}</Text> },
                    { title: '类型', dataIndex: 'task_type', key: 'type', width: 100, render: (t: string) => <Tag>{t}</Tag> },
                    { title: '错误', dataIndex: 'error', key: 'error', ellipsis: true },
                  ] as ColumnsType<any>}
                  rowKey="task_id" pagination={false} size="small"
                />
              )}
            </Card>
          </Col>
        </Row>
      </div>
    )
  }

  const turnCallColumns: ColumnsType<TokenTurnCall> = [
    { title: '#', dataIndex: 'call_index', key: 'idx', width: 56, render: (v) => <Text type="secondary">{v}</Text> },
    { title: '时间', dataIndex: 'timestamp', key: 'time', width: 130, render: (v) => <Text type="secondary" style={{ fontSize: 12 }}>{formatTime(v)}</Text> },
    { title: '模型', dataIndex: 'model', key: 'model', width: 160, render: (v) => v ? <Tag>{v}</Tag> : '-' },
    {
      title: 'Agent / 阶段',
      key: 'agent_phase',
      render: (_, row) => (
        <Space wrap size={[0, 4]}>
          {row.agent ? <Tag color="purple">{row.agent}</Tag> : null}
          {row.phase ? <Tag color="cyan">{row.phase}</Tag> : null}
          {!row.agent && !row.phase ? '-' : null}
        </Space>
      ),
    },
    {
      title: 'Token',
      dataIndex: 'total_tokens',
      key: 'tokens',
      width: 130,
      render: (v, row) => (
        <Space direction="vertical" size={0}>
          <Text strong>{formatTokens(v)}</Text>
          <Text type="secondary" style={{ fontSize: 12 }}>{formatTokens(row.input_tokens)} / {formatTokens(row.output_tokens)}</Text>
        </Space>
      ),
    },
    { title: '费用', dataIndex: 'cost_yuan', key: 'cost', width: 100, render: (v) => <Text type="warning">{formatCost(v)}</Text> },
    { title: '耗时', dataIndex: 'duration_ms', key: 'duration', width: 90, render: (v) => formatDuration(v) },
    { title: 'Run', dataIndex: 'run_id', key: 'run_id', width: 130, render: (v) => v ? <Tooltip title={v}><Text code style={{ fontSize: 11 }}>{v.slice(0, 10)}</Text></Tooltip> : '-' },
  ]

  const turnColumns: ColumnsType<TokenTurn> = [
    { title: '结束时间', dataIndex: 'ended_at', key: 'ended_at', width: 130, render: (v) => <Text type="secondary" style={{ fontSize: 12 }}>{formatTime(v)}</Text> },
    {
      title: '项目 / 任务',
      key: 'scope',
      width: 190,
      render: (_, row) => (
        <Space direction="vertical" size={0}>
          <Tooltip title={row.project_id || '无项目'}><Text code style={{ fontSize: 11 }}>{row.project_id ? row.project_id.slice(0, 14) : '-'}</Text></Tooltip>
          <Tooltip title={row.task_id || '无任务'}><Text type="secondary" style={{ fontSize: 12 }}>{row.task_id ? row.task_id.slice(0, 14) : '-'}</Text></Tooltip>
        </Space>
      ),
    },
    {
      title: 'Token',
      dataIndex: 'total_tokens',
      key: 'tokens',
      width: 130,
      render: (v, row) => (
        <Space direction="vertical" size={0}>
          <Text strong>{formatTokens(v)}</Text>
          <Text type="secondary" style={{ fontSize: 12 }}>{formatTokens(row.total_input_tokens)} / {formatTokens(row.total_output_tokens)}</Text>
        </Space>
      ),
    },
    { title: '调用', dataIndex: 'total_calls', key: 'calls', width: 80 },
    {
      title: '模型',
      key: 'models',
      render: (_, row) => {
        const models = Object.keys(row.by_model || {})
        return models.length ? models.slice(0, 3).map(model => <Tag key={model}>{model}</Tag>) : '-'
      },
    },
    {
      title: 'Agent / 阶段',
      key: 'agent_phase',
      render: (_, row) => (
        <Space wrap size={[0, 4]}>
          {Object.keys(row.by_agent || {}).slice(0, 2).map(agent => <Tag color="purple" key={agent}>{agent}</Tag>)}
          {Object.keys(row.by_phase || {}).slice(0, 2).map(phase => <Tag color="cyan" key={phase}>{phase}</Tag>)}
        </Space>
      ),
    },
    { title: '费用', dataIndex: 'total_cost_yuan', key: 'cost', width: 100, render: (v) => <Text type="warning">{formatCost(v)}</Text> },
    { title: '耗时', dataIndex: 'total_duration_ms', key: 'duration', width: 90, render: (v) => formatDuration(v) },
  ]

  const hierarchyColumns: ColumnsType<HierarchyRow> = [
    { title: '层级', dataIndex: 'level', key: 'level', width: 90, render: (v) => <Tag color={v === '项目' ? 'blue' : v === '任务' ? 'purple' : 'cyan'}>{v}</Tag> },
    {
      title: '名称',
      dataIndex: 'name',
      key: 'name',
      render: (v, row) => (
        <Tooltip title={v}>
          <Text code={row.level !== '阶段'}>{String(v).slice(0, row.level === '阶段' ? 28 : 18)}</Text>
        </Tooltip>
      ),
    },
    { title: '调用', dataIndex: ['stats', 'total_calls'], key: 'calls', width: 90 },
    { title: 'Token', dataIndex: ['stats', 'total_tokens'], key: 'tokens', width: 110, render: (v) => formatTokens(v) },
    {
      title: '输入 / 输出',
      key: 'io',
      width: 150,
      render: (_, row) => `${formatTokens(row.stats.total_input_tokens)} / ${formatTokens(row.stats.total_output_tokens)}`,
    },
    { title: '费用', dataIndex: ['stats', 'total_cost_yuan'], key: 'cost', width: 110, render: (v) => <Text type="warning">{formatCost(v)}</Text> },
    { title: 'Agent', key: 'agents', width: 90, render: (_, row) => Object.keys(row.stats.by_agent || {}).length },
  ]

  const renderTurns = () => (
    <div className="tab-content">
      <Card className="glass-card" style={{ marginBottom: 16 }}>
        <Space wrap>
          <Segmented
            value={turnScope}
            onChange={(v) => setTurnScope(v as 'all' | 'project' | 'task')}
            options={[
              { label: '全局', value: 'all' },
              { label: '项目', value: 'project' },
              { label: '任务', value: 'task' },
            ]}
          />
          {turnScope !== 'all' ? (
            <Input
              style={{ width: 220 }}
              placeholder="项目 ID"
              value={turnProjectId}
              onChange={(e) => setTurnProjectId(e.target.value)}
            />
          ) : null}
          {turnScope === 'task' ? (
            <Input
              style={{ width: 220 }}
              placeholder="任务 ID"
              value={turnTaskId}
              onChange={(e) => setTurnTaskId(e.target.value)}
            />
          ) : null}
          <Button type="primary" icon={<ReloadOutlined />} onClick={fetchTurns} loading={turnLoading}>查询</Button>
        </Space>
      </Card>
      <Card className="glass-card" title={<Space><ThunderboltOutlined />Token 轮次明细</Space>}>
        <Table<TokenTurn>
          loading={turnLoading}
          dataSource={turns}
          columns={turnColumns}
          rowKey="turn_key"
          size="small"
          pagination={{ pageSize: 10, showSizeChanger: true }}
          scroll={{ x: 980 }}
          expandable={{
            rowExpandable: (row) => (row.calls?.length ?? 0) > 0,
            expandedRowRender: (row) => (
              <Table<TokenTurnCall>
                className="observability-sub-table"
                dataSource={row.calls ?? []}
                columns={turnCallColumns}
                rowKey={(call) => `${row.turn_key}-${call.call_index}-${call.run_id || call.timestamp}`}
                size="small"
                pagination={false}
                scroll={{ x: 900 }}
              />
            ),
          }}
          locale={{ emptyText: <Empty description="暂无轮次记录" image={Empty.PRESENTED_IMAGE_SIMPLE} /> }}
        />
      </Card>
    </div>
  )

  const logColumns: ColumnsType<LogEntry> = [
    { title: '时间', dataIndex: 'ts', key: 'ts', width: 150, render: (v) => <Text type="secondary" style={{ fontSize: 12 }}>{formatTime(v)}</Text> },
    { title: '级别', dataIndex: 'level', key: 'level', width: 80, render: (l) => <Tag color={LEVEL_COLORS[l] || 'default'}>{l}</Tag> },
    { title: '来源', dataIndex: 'source', key: 'source', width: 140, render: (s) => <Text code style={{ fontSize: 11 }}>{s}</Text> },
    { title: '事件', dataIndex: 'event', key: 'event', width: 140, render: (e) => e ? <Tag>{e}</Tag> : '-' },
    { title: '消息', dataIndex: 'message', key: 'message', ellipsis: true },
    { title: '任务', dataIndex: 'task_id', key: 'tid', width: 100, render: (v) => v ? <Tooltip title={v}><Text code style={{ fontSize: 11 }}>{v.slice(0, 8)}</Text></Tooltip> : '-' },
  ]

  const renderLogs = () => (
    <div className="tab-content">
      <Card className="glass-card" style={{ marginBottom: 16 }}>
        <Space wrap>
          <Select
            style={{ width: 120 }}
            placeholder="最低级别"
            allowClear
            value={logFilters.min_level || undefined}
            onChange={(v) => setLogFilters(prev => ({ ...prev, min_level: v || '' }))}
            options={[
              { value: 'debug', label: 'Debug' },
              { value: 'info', label: 'Info' },
              { value: 'notice', label: 'Notice' },
              { value: 'warning', label: 'Warning' },
              { value: 'error', label: 'Error' },
            ]}
          />
          <Select
            style={{ width: 160 }}
            placeholder="来源模块"
            allowClear
            value={logFilters.source || undefined}
            onChange={(v) => setLogFilters(prev => ({ ...prev, source: v || '' }))}
            options={[
              { value: 'task_runner', label: 'task_runner' },
              { value: 'xhs_pipeline', label: 'xhs_pipeline' },
              { value: 'douyin_pipeline', label: 'douyin_pipeline' },
              { value: 'url_scan_pipeline', label: 'url_scan_pipeline' },
              { value: 'company_scan_pipeline', label: 'company_scan' },
              { value: 'web_tagging_pipeline', label: 'web_tagging' },
              { value: 'external', label: 'external' },
            ]}
          />
          <Input
            style={{ width: 160 }}
            placeholder="任务 ID"
            value={logFilters.task_id || ''}
            onChange={(e) => setLogFilters(prev => ({ ...prev, task_id: e.target.value }))}
          />
          <Button type="primary" icon={<ReloadOutlined />} onClick={() => fetchLogs({ page: 1 })}>查询</Button>
        </Space>
      </Card>
      <Card className="glass-card">
        <Table
          loading={logLoading}
          dataSource={logs}
          columns={logColumns}
          rowKey="log_id"
          pagination={{
            current: logPage,
            total: logTotal,
            pageSize: logFilters.page_size || 20,
            showSizeChanger: true,
            showTotal: (t) => `共 ${t} 条`,
            onChange: (p, ps) => {
              setLogFilters(prev => ({ ...prev, page: p, page_size: ps }))
              fetchLogs({ page: p, page_size: ps })
            },
          }}
          size="small"
        />
      </Card>
    </div>
  )

  const renderHierarchy = () => (
    <div className="tab-content">
      <Row gutter={[16, 16]} style={{ marginBottom: 16 }}>
        <Col xs={24} sm={12} lg={6}>
          <Card className="glass-card stat-card">
            <Statistic title="全局调用" value={hierarchy?.global.total_calls ?? 0} prefix={<ApiOutlined />} />
          </Card>
        </Col>
        <Col xs={24} sm={12} lg={6}>
          <Card className="glass-card stat-card">
            <Statistic title="全局 Token" value={hierarchy?.global.total_tokens ?? 0} formatter={(v) => formatTokens(Number(v))} prefix={<ThunderboltOutlined />} />
          </Card>
        </Col>
        <Col xs={24} sm={12} lg={6}>
          <Card className="glass-card stat-card">
            <Statistic title="全局费用" value={hierarchy?.global.total_cost_yuan ?? 0} precision={4} prefix={<DollarOutlined />} />
          </Card>
        </Col>
        <Col xs={24} sm={12} lg={6}>
          <Card className="glass-card stat-card">
            <Statistic title="项目节点" value={hierarchyRows.length} prefix={<ExperimentOutlined />} />
          </Card>
        </Col>
      </Row>
      <Card
        className="glass-card"
        title={<Space><ExperimentOutlined />Token 层级钻取</Space>}
        extra={<Button icon={<ReloadOutlined />} onClick={fetchHierarchy} loading={hierarchyLoading}>刷新</Button>}
      >
        <Table<HierarchyRow>
          loading={hierarchyLoading}
          dataSource={hierarchyRows}
          columns={hierarchyColumns}
          rowKey="key"
          size="small"
          pagination={false}
          scroll={{ x: 760 }}
          locale={{ emptyText: <Empty description="暂无层级数据" image={Empty.PRESENTED_IMAGE_SIMPLE} /> }}
        />
      </Card>
    </div>
  )

  const scenarioColumns: ColumnsType<ScenarioStat> = [
    {
      title: '任务场景',
      dataIndex: 'task_type',
      key: 'task_type',
      render: (v: string) => (
        <Space direction="vertical" size={0}>
          <Text strong>{taskTypeLabel(v)}</Text>
          <Text type="secondary" style={{ fontSize: 12 }}>{v}</Text>
        </Space>
      ),
    },
    {
      title: '任务数',
      key: 'tasks',
      width: 220,
      render: (_, row) => {
        const entries = Object.entries(row.tasks.by_status ?? {}).sort((a, b) => b[1] - a[1])
        return (
          <Space direction="vertical" size={2}>
            <Text strong>{row.tasks.total}</Text>
            <Space wrap size={[0, 4]}>
              {entries.length === 0 ? <Text type="secondary">-</Text> : entries.map(([status, count]) => {
                const cfg = STATUS_CONFIG[status] || { color: 'default', icon: <ClockCircleOutlined /> }
                return <Tag color={cfg.color} key={status}>{status} {count}</Tag>
              })}
            </Space>
          </Space>
        )
      },
    },
    { title: '调用', dataIndex: ['token', 'total_calls'], key: 'calls', width: 90 },
    { title: 'Token', dataIndex: ['token', 'total_tokens'], key: 'tokens', width: 120, render: (v) => formatTokens(v) },
    {
      title: '输入 / 输出',
      key: 'io',
      width: 150,
      render: (_, row) => `${formatTokens(row.token.total_input_tokens)} / ${formatTokens(row.token.total_output_tokens)}`,
    },
    { title: '费用', dataIndex: ['token', 'total_cost_yuan'], key: 'cost', width: 110, render: (v) => <Text type="warning">{formatCost(v)}</Text> },
  ]

  const renderScenarios = () => (
    <div className="tab-content">
      <Card
        className="glass-card"
        title={<Space><ApiOutlined />任务场景用量分布</Space>}
        extra={<Button icon={<ReloadOutlined />} onClick={fetchScenarios} loading={scenarioLoading}>刷新</Button>}
      >
        <Table<ScenarioStat>
          loading={scenarioLoading}
          dataSource={scenarios}
          columns={scenarioColumns}
          rowKey="task_type"
          size="small"
          pagination={false}
          scroll={{ x: 820 }}
          rowClassName={(row) => (scenarioParam && row.task_type === scenarioParam ? 'scenario-row-active' : '')}
          locale={{ emptyText: <Empty description="暂无场景数据" image={Empty.PRESENTED_IMAGE_SIMPLE} /> }}
        />
      </Card>
    </div>
  )

  const tabs = [
    { key: 'overview', label: <Space><DashboardOutlined />总览</Space>, children: loading ? <Spin size="large" style={{ display: 'block', margin: '100px auto' }} /> : renderOverview() },
    { key: 'scenarios', label: <Space><ApiOutlined />任务场景</Space>, children: renderScenarios() },
    { key: 'turns', label: <Space><ThunderboltOutlined />轮次明细</Space>, children: renderTurns() },
    { key: 'logs', label: <Space><FileTextOutlined />日志查询</Space>, children: renderLogs() },
    { key: 'hierarchy', label: <Space><ExperimentOutlined />层级钻取</Space>, children: renderHierarchy() },
  ]

  return (
    <div className="observability-page page-container fade-in">
      <div className="page-header slide-up">
        <div>
          <Title level={2} className="page-title"><EyeOutlined /> 系统观测</Title>
          <Text type="secondary">Token 费用、任务状态、结构化日志统一看板</Text>
        </div>
        <Button icon={<ReloadOutlined />} onClick={fetchOverview} loading={loading}>刷新</Button>
      </div>
      <Tabs activeKey={activeTab} onChange={setActiveTab} items={tabs} size="large" />
    </div>
  )
}
