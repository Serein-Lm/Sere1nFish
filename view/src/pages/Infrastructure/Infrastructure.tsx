import { useCallback, useEffect, useMemo, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import {
  Alert,
  Button,
  Card,
  Col,
  Descriptions,
  Drawer,
  Form,
  Input,
  InputNumber,
  Modal,
  Popconfirm,
  Row,
  Select,
  Space,
  Statistic,
  Table,
  Tabs,
  Tag,
  Tooltip,
  Typography,
  message,
} from 'antd'
import {
  ApiOutlined,
  CheckCircleOutlined,
  CloudServerOutlined,
  CopyOutlined,
  DeleteOutlined,
  EditOutlined,
  EyeOutlined,
  KeyOutlined,
  LinkOutlined,
  PauseCircleOutlined,
  PlayCircleOutlined,
  PlusOutlined,
  ReloadOutlined,
  SafetyCertificateOutlined,
  StopOutlined,
} from '@ant-design/icons'
import type { ColumnsType, TablePaginationConfig } from 'antd/es/table'
import {
  cancelDistributedWork,
  createNodeBootstrap,
  deleteProxyProfile,
  getBrowserPoolStatus,
  getDistributedOverview,
  getDistributedWork,
  listDistributedWork,
  listProxyProfiles,
  listScanNodes,
  rotateScanNodeToken,
  saveProxyProfile,
  setScanNodeStatus,
  testProxyProfile,
  type BootstrapInput,
  type BootstrapResult,
  type BrowserPoolStatus,
  type DistributedOverview,
  type DistributedWorkItem,
  type NodeStatus,
  type ProxyProfile,
  type ProxyProfileInput,
  type ScanNode,
} from '../../services/distributedScanService'
import './Infrastructure.css'

const { Text, Title } = Typography

const NODE_STATUS: Record<NodeStatus, { color: string; label: string }> = {
  online: { color: 'success', label: '在线' },
  draining: { color: 'warning', label: '排空中' },
  offline: { color: 'default', label: '离线' },
  disabled: { color: 'error', label: '已禁用' },
}

const WORK_STATUS: Record<string, { color: string; label: string }> = {
  queued: { color: 'processing', label: '排队' },
  leased: { color: 'cyan', label: '已租用' },
  running: { color: 'blue', label: '执行中' },
  retry: { color: 'warning', label: '待重试' },
  completed: { color: 'success', label: '已完成' },
  failed: { color: 'error', label: '失败' },
  cancelled: { color: 'default', label: '已取消' },
}

const CAPABILITY_LABELS: Record<string, string> = {
  http_probe: 'HTTP 探活',
  browser_probe: '浏览器探活',
}

function formatDate(value?: string): string {
  if (!value) return '-'
  const date = new Date(value)
  return Number.isNaN(date.getTime()) ? '-' : date.toLocaleString('zh-CN', { hour12: false })
}

function parseLabels(value?: string): Record<string, string> {
  if (!value?.trim()) return {}
  const parsed = JSON.parse(value)
  if (!parsed || Array.isArray(parsed) || typeof parsed !== 'object') {
    throw new Error('标签必须是 JSON 对象')
  }
  return Object.fromEntries(
    Object.entries(parsed).map(([key, item]) => [key, String(item)]),
  )
}

function errorMessage(error: unknown): string {
  if (!(error instanceof Error)) return '请求失败'
  try {
    const payload = JSON.parse(error.message) as { detail?: string | { error?: string } }
    if (typeof payload.detail === 'string') return payload.detail
    if (payload.detail && typeof payload.detail === 'object') return payload.detail.error || error.message
  } catch {
    return error.message
  }
  return error.message
}

export default function Infrastructure() {
  const navigate = useNavigate()
  const [messageApi, contextHolder] = message.useMessage()
  const [loading, setLoading] = useState(true)
  const [refreshing, setRefreshing] = useState(false)
  const [loadError, setLoadError] = useState('')
  const [overview, setOverview] = useState<DistributedOverview | null>(null)
  const [nodes, setNodes] = useState<ScanNode[]>([])
  const [work, setWork] = useState<DistributedWorkItem[]>([])
  const [workTotal, setWorkTotal] = useState(0)
  const [proxies, setProxies] = useState<ProxyProfile[]>([])
  const [browser, setBrowser] = useState<BrowserPoolStatus | null>(null)
  const [workPage, setWorkPage] = useState(1)
  const [workPageSize, setWorkPageSize] = useState(20)
  const [workStatus, setWorkStatus] = useState('')
  const [workKind, setWorkKind] = useState('')
  const [bootstrapOpen, setBootstrapOpen] = useState(false)
  const [bootstrapResult, setBootstrapResult] = useState<BootstrapResult | null>(null)
  const [bootstrapSubmitting, setBootstrapSubmitting] = useState(false)
  const [bootstrapForm] = Form.useForm()
  const [rotatedToken, setRotatedToken] = useState<{ node_id: string; node_token: string } | null>(null)
  const [workDetail, setWorkDetail] = useState<DistributedWorkItem | null>(null)
  const [workDetailLoading, setWorkDetailLoading] = useState(false)
  const [proxyOpen, setProxyOpen] = useState(false)
  const [editingProxy, setEditingProxy] = useState<ProxyProfile | null>(null)
  const [proxySubmitting, setProxySubmitting] = useState(false)
  const [testingProxy, setTestingProxy] = useState('')
  const [proxyForm] = Form.useForm()

  const loadData = useCallback(async (quiet = false) => {
    if (quiet) setRefreshing(true)
    else setLoading(true)
    const responses = await Promise.allSettled([
      getDistributedOverview(),
      listScanNodes(),
      listDistributedWork({ page: workPage, pageSize: workPageSize, status: workStatus, kind: workKind }),
      listProxyProfiles(),
      getBrowserPoolStatus(),
    ])
    const failures: string[] = []
    const [overviewResponse, nodesResponse, workResponse, proxyResponse, browserResponse] = responses
    if (overviewResponse.status === 'fulfilled') setOverview(overviewResponse.value)
    else failures.push(errorMessage(overviewResponse.reason))
    if (nodesResponse.status === 'fulfilled') setNodes(nodesResponse.value.items)
    else failures.push(errorMessage(nodesResponse.reason))
    if (workResponse.status === 'fulfilled') {
      setWork(workResponse.value.items)
      setWorkTotal(workResponse.value.total)
    } else failures.push(errorMessage(workResponse.reason))
    if (proxyResponse.status === 'fulfilled') setProxies(proxyResponse.value.items)
    else failures.push(errorMessage(proxyResponse.reason))
    if (browserResponse.status === 'fulfilled') setBrowser(browserResponse.value)
    else failures.push(errorMessage(browserResponse.reason))
    setLoadError([...new Set(failures)].join('；'))
    setLoading(false)
    setRefreshing(false)
  }, [workKind, workPage, workPageSize, workStatus])

  useEffect(() => {
    void loadData()
    const timer = window.setInterval(() => void loadData(true), 15000)
    return () => window.clearInterval(timer)
  }, [loadData])

  const runNodeAction = async (node: ScanNode, status: NodeStatus) => {
    try {
      await setScanNodeStatus(node.node_id, status)
      messageApi.success('节点状态已更新')
      await loadData(true)
    } catch (error) {
      messageApi.error(errorMessage(error))
    }
  }

  const submitBootstrap = async (values: { display_name: string; capabilities: string[]; labels?: string; expires_minutes: number }) => {
    setBootstrapSubmitting(true)
    try {
      const input: BootstrapInput = {
        display_name: values.display_name,
        allowed_capabilities: values.capabilities,
        labels: parseLabels(values.labels),
        expires_minutes: values.expires_minutes,
      }
      const result = await createNodeBootstrap(input)
      setBootstrapResult(result)
      setBootstrapOpen(false)
      bootstrapForm.resetFields()
    } catch (error) {
      messageApi.error(errorMessage(error))
    } finally {
      setBootstrapSubmitting(false)
    }
  }

  const rotateToken = async (node: ScanNode) => {
    try {
      setRotatedToken(await rotateScanNodeToken(node.node_id))
    } catch (error) {
      messageApi.error(errorMessage(error))
    }
  }

  const openWorkDetail = async (item: DistributedWorkItem) => {
    setWorkDetailLoading(true)
    setWorkDetail(item)
    try {
      setWorkDetail(await getDistributedWork(item.work_item_id))
    } catch (error) {
      messageApi.error(errorMessage(error))
    } finally {
      setWorkDetailLoading(false)
    }
  }

  const openProxyEditor = (profile?: ProxyProfile) => {
    proxyForm.resetFields()
    setEditingProxy(profile || null)
    proxyForm.setFieldsValue(profile ? {
      name: profile.name,
      dns_mode: profile.dns_mode,
      max_concurrency: profile.max_concurrency,
      failure_threshold: profile.failure_threshold,
      cooldown_seconds: profile.cooldown_seconds,
      status: profile.status,
      labels: JSON.stringify(profile.labels || {}, null, 2),
      bypass_classes: profile.bypass_classes || [],
    } : {
      dns_mode: 'remote',
      max_concurrency: 8,
      failure_threshold: 3,
      cooldown_seconds: 300,
      status: 'active',
      labels: '{}',
      bypass_classes: ['control_plane', 'object_storage'],
    })
    setProxyOpen(true)
  }

  const submitProxy = async (values: Record<string, unknown>) => {
    setProxySubmitting(true)
    try {
      const input: ProxyProfileInput = {
        name: String(values.name || ''),
        endpoint: values.endpoint ? String(values.endpoint) : undefined,
        username: values.username === undefined ? undefined : String(values.username),
        password: values.password === undefined ? undefined : String(values.password),
        dns_mode: values.dns_mode as 'remote' | 'local',
        max_concurrency: Number(values.max_concurrency),
        failure_threshold: Number(values.failure_threshold),
        cooldown_seconds: Number(values.cooldown_seconds),
        bypass_classes: Array.isArray(values.bypass_classes) ? values.bypass_classes.map(String) : [],
        labels: parseLabels(String(values.labels || '')),
        status: values.status as 'active' | 'disabled',
      }
      await saveProxyProfile(input, editingProxy?.profile_id)
      messageApi.success(editingProxy ? '代理配置已更新' : '代理配置已创建')
      setProxyOpen(false)
      proxyForm.resetFields()
      await loadData(true)
    } catch (error) {
      messageApi.error(errorMessage(error))
    } finally {
      setProxySubmitting(false)
    }
  }

  const nodeColumns = useMemo<ColumnsType<ScanNode>>(() => [
    {
      title: '节点',
      key: 'node',
      width: 210,
      render: (_, item) => <div className="infra-primary-cell"><Text strong>{item.display_name}</Text><Text type="secondary" copyable>{item.node_id}</Text></div>,
    },
    {
      title: '状态', dataIndex: 'status', width: 84,
      render: (status: NodeStatus) => <Tag color={NODE_STATUS[status]?.color}>{NODE_STATUS[status]?.label || status}</Tag>,
    },
    {
      title: '能力与容量', key: 'capacity', width: 230,
      render: (_, item) => <Space size={[4, 4]} wrap>{Object.keys(item.capabilities || {}).map(capability => {
        const key = `${capability}_slots`
        return <Tag key={capability}>{CAPABILITY_LABELS[capability] || capability} {item.usage?.[key] || 0}/{item.capacity?.[key] || 0}</Tag>
      })}</Space>,
    },
    {
      title: '标签', dataIndex: 'labels', width: 170,
      render: (labels: Record<string, string>) => <Space size={[4, 4]} wrap>{Object.entries(labels || {}).map(([key, value]) => <Tag key={key}>{key}: {value}</Tag>)}</Space>,
    },
    { title: '版本', dataIndex: 'version', width: 80, render: (value: string) => value || '-' },
    { title: '最后心跳', dataIndex: 'last_heartbeat_at', width: 165, render: formatDate },
    {
      title: '操作', key: 'actions', width: 120,
      render: (_, item) => <Space size={4}>
        {item.status === 'online' ? <Tooltip title="停止领取新任务"><Button aria-label="停止领取新任务" icon={<PauseCircleOutlined />} onClick={() => void runNodeAction(item, 'draining')} /></Tooltip>
          : item.status !== 'disabled' && <Tooltip title="恢复节点"><Button aria-label="恢复节点" icon={<PlayCircleOutlined />} onClick={() => void runNodeAction(item, 'online')} /></Tooltip>}
        <Popconfirm title="轮换后必须更新节点身份文件，确认继续？" onConfirm={() => void rotateToken(item)}><Tooltip title="轮换节点令牌"><Button aria-label="轮换节点令牌" icon={<KeyOutlined />} /></Tooltip></Popconfirm>
        {item.status !== 'disabled' && <Popconfirm title="禁用后节点将无法鉴权" onConfirm={() => void runNodeAction(item, 'disabled')}><Tooltip title="禁用节点"><Button aria-label="禁用节点" danger icon={<StopOutlined />} /></Tooltip></Popconfirm>}
      </Space>,
    },
  ], [loadData, messageApi])

  const workColumns = useMemo<ColumnsType<DistributedWorkItem>>(() => [
    {
      title: '工作项', dataIndex: 'work_item_id', width: 220,
      render: (value: string) => <Text copyable ellipsis className="infra-mono">{value}</Text>,
    },
    { title: '类型', dataIndex: 'kind', width: 130, render: (value: string) => CAPABILITY_LABELS[value] || value },
    { title: '状态', dataIndex: 'status', width: 100, render: (value: string) => <Tag color={WORK_STATUS[value]?.color}>{WORK_STATUS[value]?.label || value}</Tag> },
    { title: '优先级', dataIndex: 'priority', width: 80 },
    { title: '尝试', key: 'attempt', width: 80, render: (_, item) => `${item.attempt || 0}/${item.max_attempts || 0}` },
    { title: '节点', key: 'node', width: 180, render: (_, item) => item.lease?.node_id || '-' },
    { title: 'Project', dataIndex: 'project_id', width: 180, render: (value: string) => value || '-' },
    { title: '创建时间', dataIndex: 'created_at', width: 180, render: formatDate },
    {
      title: '操作', key: 'actions', fixed: 'right', width: 100,
      render: (_, item) => <Space size={4}>
        <Tooltip title="查看工作项"><Button aria-label="查看工作项" icon={<EyeOutlined />} onClick={() => void openWorkDetail(item)} /></Tooltip>
        {!['completed', 'failed', 'cancelled'].includes(item.status) && <Popconfirm title="确认取消该工作项？" onConfirm={async () => {
          try { await cancelDistributedWork(item.work_item_id); await loadData(true) } catch (error) { messageApi.error(errorMessage(error)) }
        }}><Tooltip title="取消工作项"><Button aria-label="取消工作项" danger icon={<StopOutlined />} /></Tooltip></Popconfirm>}
      </Space>,
    },
  ], [loadData, messageApi])

  const proxyColumns = useMemo<ColumnsType<ProxyProfile>>(() => [
    { title: '名称', dataIndex: 'name', width: 180, render: (value: string) => <Text strong>{value}</Text> },
    { title: '地址', dataIndex: 'endpoint_hint', width: 220, render: (value: string) => <Text className="infra-mono">{value || '-'}</Text> },
    { title: '状态', dataIndex: 'status', width: 90, render: (value: string) => <Tag color={value === 'active' ? 'success' : 'default'}>{value === 'active' ? '启用' : '禁用'}</Tag> },
    { title: 'DNS', dataIndex: 'dns_mode', width: 100, render: (value: string) => value === 'remote' ? '远端解析' : '本地解析' },
    { title: '租约', key: 'leases', width: 100, render: (_, item) => `${item.active_leases || 0}/${item.max_concurrency}` },
    { title: '连续失败', dataIndex: 'consecutive_failures', width: 100, render: (value: number) => value || 0 },
    { title: '冷却至', dataIndex: 'cooldown_until', width: 180, render: formatDate },
    {
      title: '操作', key: 'actions', fixed: 'right', width: 140,
      render: (_, item) => <Space size={4}>
        <Tooltip title="测试代理"><Button aria-label="测试代理" loading={testingProxy === item.profile_id} icon={<CheckCircleOutlined />} onClick={async () => {
          setTestingProxy(item.profile_id)
          try { const result = await testProxyProfile(item.profile_id); messageApi.success(`代理可用，${result.latency_ms} ms`) } catch (error) { messageApi.error(errorMessage(error)) } finally { setTestingProxy(''); await loadData(true) }
        }} /></Tooltip>
        <Tooltip title="编辑代理"><Button aria-label="编辑代理" icon={<EditOutlined />} onClick={() => openProxyEditor(item)} /></Tooltip>
        <Popconfirm title="确认删除该代理配置？" onConfirm={async () => {
          try { await deleteProxyProfile(item.profile_id); await loadData(true) } catch (error) { messageApi.error(errorMessage(error)) }
        }}><Tooltip title="删除代理"><Button aria-label="删除代理" danger icon={<DeleteOutlined />} /></Tooltip></Popconfirm>
      </Space>,
    },
  ], [loadData, messageApi, testingProxy])

  const browserColumns: ColumnsType<BrowserPoolStatus['containers'][number]> = [
    { title: '容器', key: 'container', render: (_, item) => item.name || item.container_id?.slice(0, 12) || '-' },
    { title: '状态', dataIndex: 'status', width: 100, render: (value: string) => <Tag color={value === 'busy' ? 'processing' : value === 'idle' ? 'success' : 'default'}>{value || '-'}</Tag> },
    { title: '用途', dataIndex: 'purpose', render: (value: string) => value || '-' },
    { title: '任务', dataIndex: 'task_id', render: (value: string) => value || '-' },
    { title: '最近使用', dataIndex: 'last_used', width: 180, render: formatDate },
  ]

  const controlPlaneUrl = `${window.location.origin}/api/v1`
  const tabs = [
    {
      key: 'nodes',
      label: `扫描节点 (${nodes.length})`,
      children: <>
        <div className="infra-toolbar"><Text type="secondary">节点从公网主动连接控制面，无需开放节点业务端口。</Text><Button type="primary" icon={<PlusOutlined />} onClick={() => setBootstrapOpen(true)}>注册节点</Button></div>
        <Table rowKey="node_id" columns={nodeColumns} dataSource={nodes} loading={loading} scroll={{ x: 1060 }} pagination={{ pageSize: 20, hideOnSinglePage: true }} locale={{ emptyText: '尚未注册扫描节点' }} />
      </>,
    },
    {
      key: 'work',
      label: `工作队列 (${workTotal})`,
      children: <>
        <div className="infra-toolbar infra-filter-toolbar"><Space wrap>
          <Select aria-label="工作状态" value={workStatus} onChange={value => { setWorkStatus(value); setWorkPage(1) }} options={[{ value: '', label: '全部状态' }, ...Object.entries(WORK_STATUS).map(([value, item]) => ({ value, label: item.label }))]} />
          <Select aria-label="工作类型" value={workKind} onChange={value => { setWorkKind(value); setWorkPage(1) }} options={[{ value: '', label: '全部类型' }, ...Object.entries(CAPABILITY_LABELS).map(([value, label]) => ({ value, label }))]} />
        </Space></div>
        <Table rowKey="work_item_id" columns={workColumns} dataSource={work} loading={loading} scroll={{ x: 1250 }} pagination={{ current: workPage, pageSize: workPageSize, total: workTotal, showSizeChanger: true, showTotal: total => `共 ${total} 项` }} onChange={(pagination: TablePaginationConfig) => { setWorkPage(pagination.current || 1); setWorkPageSize(pagination.pageSize || 20) }} locale={{ emptyText: '暂无分布式工作项' }} />
      </>,
    },
    {
      key: 'proxies',
      label: `SOCKS5 (${proxies.length})`,
      children: <>
        <div className="infra-toolbar"><Text type="secondary">凭据加密存储，只在节点工作租约中短时下发。</Text><Button type="primary" icon={<PlusOutlined />} onClick={() => openProxyEditor()}>添加代理</Button></div>
        <Table rowKey="profile_id" columns={proxyColumns} dataSource={proxies} loading={loading} scroll={{ x: 1050 }} pagination={{ pageSize: 20, hideOnSinglePage: true }} locale={{ emptyText: '尚未配置 SOCKS5 代理' }} />
      </>,
    },
    {
      key: 'chrome',
      label: `本机 Chrome (${browser?.total || 0})`,
      children: <>
        {browser?.error && <Alert type="warning" showIcon title="Chrome 池状态读取失败" description={browser.error} />}
        <Table rowKey={item => item.container_id || item.name || `${item.task_id || 'chrome'}-${item.last_used || ''}`} columns={browserColumns} dataSource={browser?.containers || []} loading={loading} pagination={{ pageSize: 20, hideOnSinglePage: true }} locale={{ emptyText: '当前没有 Chrome 容器' }} />
      </>,
    },
  ]

  return (
    <div className="infrastructure page-container fade-in">
      {contextHolder}
      <div className="infra-header">
        <div><Title level={2}><CloudServerOutlined /> 基础设施</Title><Text type="secondary">本机与分布式扫描资源</Text></div>
        <Tooltip title="刷新"><Button aria-label="刷新" icon={<ReloadOutlined spin={refreshing} />} onClick={() => void loadData(true)} /></Tooltip>
      </div>

      {loadError && <Alert className="infra-alert" type="error" showIcon closable title="部分状态读取失败" description={loadError} />}
      {overview && !overview.runtime.enabled && <Alert className="infra-alert" type="info" showIcon title="分布式执行未启用，本机扫描逻辑保持不变" action={<Button size="small" icon={<ApiOutlined />} onClick={() => navigate('/settings/config')}>配置</Button>} />}

      <Row gutter={[12, 12]} className="infra-stats">
        <Col xs={12} lg={6}><Card size="small"><Statistic title="在线节点" value={overview?.nodes.online || 0} suffix={`/ ${overview?.nodes.total || 0}`} prefix={<CloudServerOutlined />} /></Card></Col>
        <Col xs={12} lg={6}><Card size="small"><Statistic title="活动工作项" value={overview?.work.active || 0} suffix={`/ ${overview?.work.total || 0}`} prefix={<ApiOutlined />} /></Card></Col>
        <Col xs={12} lg={6}><Card size="small"><Statistic title="代理活动租约" value={overview?.proxies.active_leases || 0} suffix={`/ ${overview?.proxies.total || 0}`} prefix={<SafetyCertificateOutlined />} /></Card></Col>
        <Col xs={12} lg={6}><Card size="small"><Statistic title="本机 Chrome" value={browser?.busy || 0} suffix={`/ ${browser?.total || 0}`} prefix={<LinkOutlined />} /></Card></Col>
      </Row>

      <Card className="infra-main-panel" styles={{ body: { paddingTop: 4 } }}><Tabs items={tabs} destroyOnHidden={false} /></Card>

      <Modal title="生成节点注册凭据" open={bootstrapOpen} onCancel={() => setBootstrapOpen(false)} onOk={() => bootstrapForm.submit()} confirmLoading={bootstrapSubmitting} destroyOnHidden>
        <Form form={bootstrapForm} layout="vertical" initialValues={{ capabilities: ['http_probe', 'browser_probe'], expires_minutes: 30, labels: '{}' }} onFinish={submitBootstrap}>
          <Form.Item name="display_name" label="节点名称" rules={[{ required: true, message: '请输入节点名称' }]}><Input placeholder="hangzhou-01" /></Form.Item>
          <Form.Item name="capabilities" label="允许能力" rules={[{ required: true }]}><Select mode="multiple" options={Object.entries(CAPABILITY_LABELS).map(([value, label]) => ({ value, label }))} /></Form.Item>
          <Form.Item name="labels" label="标签 JSON" rules={[{ validator: async (_, value) => { parseLabels(value) } }]}><Input.TextArea rows={3} spellCheck={false} /></Form.Item>
          <Form.Item name="expires_minutes" label="有效分钟"><InputNumber min={5} max={1440} precision={0} /></Form.Item>
        </Form>
      </Modal>

      <Modal title="节点注册凭据" open={Boolean(bootstrapResult)} onCancel={() => setBootstrapResult(null)} footer={<Button type="primary" onClick={() => setBootstrapResult(null)}>完成</Button>} width={720}>
        {bootstrapResult && <Descriptions bordered size="small" column={1}>
          <Descriptions.Item label="控制面"><Text copyable>{controlPlaneUrl}</Text></Descriptions.Item>
          <Descriptions.Item label="Bootstrap Token"><Text copyable={{ icon: <CopyOutlined /> }} className="infra-secret">{bootstrapResult.bootstrap_token}</Text></Descriptions.Item>
          <Descriptions.Item label="过期时间">{formatDate(bootstrapResult.expires_at)}</Descriptions.Item>
          <Descriptions.Item label="环境变量"><pre className="infra-code">{`SCAN_CONTROL_PLANE_URL=${controlPlaneUrl}\nSCAN_NODE_BOOTSTRAP_TOKEN=${bootstrapResult.bootstrap_token}\nSCAN_NODE_NAME=${bootstrapResult.display_name}`}</pre></Descriptions.Item>
        </Descriptions>}
      </Modal>

      <Modal title="新节点令牌" open={Boolean(rotatedToken)} onCancel={() => setRotatedToken(null)} footer={<Button type="primary" onClick={() => setRotatedToken(null)}>完成</Button>}>
        {rotatedToken && <Descriptions bordered size="small" column={1}><Descriptions.Item label="节点">{rotatedToken.node_id}</Descriptions.Item><Descriptions.Item label="Token"><Text copyable className="infra-secret">{rotatedToken.node_token}</Text></Descriptions.Item></Descriptions>}
      </Modal>

      <Drawer title="工作项详情" size="large" open={Boolean(workDetail)} onClose={() => setWorkDetail(null)} loading={workDetailLoading}>
        {workDetail && <><Descriptions bordered size="small" column={1}><Descriptions.Item label="工作项">{workDetail.work_item_id}</Descriptions.Item><Descriptions.Item label="状态">{WORK_STATUS[workDetail.status]?.label || workDetail.status}</Descriptions.Item><Descriptions.Item label="错误">{workDetail.last_error?.message || workDetail.cancel_reason || '-'}</Descriptions.Item></Descriptions><pre className="infra-json">{JSON.stringify({ payload: workDetail.payload, progress: workDetail.progress, result: workDetail.result, artifacts: workDetail.artifacts }, null, 2)}</pre></>}
      </Drawer>

      <Modal title={editingProxy ? '编辑 SOCKS5 代理' : '添加 SOCKS5 代理'} open={proxyOpen} onCancel={() => { setProxyOpen(false); proxyForm.resetFields() }} onOk={() => proxyForm.submit()} confirmLoading={proxySubmitting} destroyOnHidden width={680}>
        <Form form={proxyForm} layout="vertical" onFinish={submitProxy}>
          <Row gutter={12}><Col span={12}><Form.Item name="name" label="名称" rules={[{ required: true }]}><Input /></Form.Item></Col><Col span={12}><Form.Item name="status" label="状态"><Select options={[{ value: 'active', label: '启用' }, { value: 'disabled', label: '禁用' }]} /></Form.Item></Col></Row>
          <Form.Item name="endpoint" label={editingProxy ? `SOCKS5 地址（留空保持 ${editingProxy.endpoint_hint}）` : 'SOCKS5 地址'} rules={editingProxy ? [] : [{ required: true }]}><Input placeholder="socks5://127.0.0.1:1080" /></Form.Item>
          <Row gutter={12}><Col span={12}><Form.Item name="username" label="用户名（留空保持/不设置）"><Input autoComplete="off" /></Form.Item></Col><Col span={12}><Form.Item name="password" label="密码（留空保持/不设置）"><Input.Password autoComplete="new-password" /></Form.Item></Col></Row>
          <Row gutter={12}><Col span={8}><Form.Item name="dns_mode" label="DNS"><Select options={[{ value: 'remote', label: '远端解析' }, { value: 'local', label: '本地解析' }]} /></Form.Item></Col><Col span={8}><Form.Item name="max_concurrency" label="最大并发"><InputNumber min={1} max={500} precision={0} /></Form.Item></Col><Col span={8}><Form.Item name="failure_threshold" label="失败阈值"><InputNumber min={1} max={20} precision={0} /></Form.Item></Col></Row>
          <Form.Item name="cooldown_seconds" label="冷却秒数"><InputNumber min={10} max={86400} precision={0} /></Form.Item>
          <Form.Item name="bypass_classes" label="绕过类别"><Select mode="tags" tokenSeparators={[',']} /></Form.Item>
          <Form.Item name="labels" label="标签 JSON" rules={[{ validator: async (_, value) => { parseLabels(value) } }]}><Input.TextArea rows={3} spellCheck={false} /></Form.Item>
        </Form>
      </Modal>
    </div>
  )
}
