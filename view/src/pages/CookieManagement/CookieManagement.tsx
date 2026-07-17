import { useEffect, useState } from 'react'
import { Card, Button, Table, Tag, Typography, Space, Modal, Form, Input, message, Tooltip, Popconfirm, Tabs, Switch } from 'antd'
import {
  SettingOutlined,
  PlusOutlined,
  DeleteOutlined,
  CheckCircleOutlined,
  CloseCircleOutlined,
  QuestionCircleOutlined,
  SyncOutlined,
  ThunderboltOutlined,
  EyeOutlined,
} from '@ant-design/icons'
import type { ColumnsType } from 'antd/es/table'
import {
  listXhsCookies,
  createXhsCookie,
  verifyXhsCookie,
  activateXhsCookie,
  deleteXhsCookie,
  getXhsCookieDetail,
  updateXhsCookie,
  getXhsRuntimeStatus,
  testXhsSigner,
  type XhsCookieAccount,
  type XhsRuntimeStatus,
} from '../../services/xhsService'
import {
  listDouyinCookies,
  createDouyinCookie,
  verifyDouyinCookie,
  activateDouyinCookie,
  deleteDouyinCookie,
  getDouyinCookieDetail,
  updateDouyinCookie,
  type DouyinCookieAccount,
} from '../../services/douyinService'
import './CookieManagement.css'

const { Title, Paragraph, Text } = Typography

function formatDate(value: string | undefined): string {
  if (!value) return '-'
  const d = new Date(value)
  if (Number.isNaN(d.getTime())) return value
  return d.toLocaleString()
}

export default function CookieManagement() {
  const [activeTab, setActiveTab] = useState('xhs')

  // 小红书账号管理状态
  const [xhsCookies, setXhsCookies] = useState<XhsCookieAccount[]>([])
  const [xhsLoading, setXhsLoading] = useState(false)
  const [isXhsModalOpen, setIsXhsModalOpen] = useState(false)
  const [xhsForm] = Form.useForm()
  const [xhsSubmitting, setXhsSubmitting] = useState(false)
  const [verifyingAccount, setVerifyingAccount] = useState<string | null>(null)
  const [activatingAccount, setActivatingAccount] = useState<string | null>(null)
  const [xhsRuntimeStatus, setXhsRuntimeStatus] = useState<XhsRuntimeStatus | null>(null)
  const [xhsRuntimeChecking, setXhsRuntimeChecking] = useState(false)

  // 查看/编辑 Cookie 状态
  const [isEditModalOpen, setIsEditModalOpen] = useState(false)
  const [editForm] = Form.useForm()
  const [modalApi, modalContextHolder] = Modal.useModal()
  const [editSubmitting, setEditSubmitting] = useState(false)
  const [editingAccount, setEditingAccount] = useState<string | null>(null)
  const [editingPlatform, setEditingPlatform] = useState<'xhs' | 'douyin'>('xhs')
  const [loadingDetail, setLoadingDetail] = useState(false)

  // 抖音账号管理状态
  const [douyinCookies, setDouyinCookies] = useState<DouyinCookieAccount[]>([])
  const [douyinLoading, setDouyinLoading] = useState(false)
  const [isDouyinModalOpen, setIsDouyinModalOpen] = useState(false)
  const [douyinForm] = Form.useForm()
  const [douyinSubmitting, setDouyinSubmitting] = useState(false)
  const [douyinVerifyingAccount, setDouyinVerifyingAccount] = useState<string | null>(null)
  const [douyinActivatingAccount, setDouyinActivatingAccount] = useState<string | null>(null)

  // 加载小红书账号列表
  const fetchXhsCookies = async () => {
    setXhsLoading(true)
    try {
      const data = await listXhsCookies()
      setXhsCookies(data)
    } catch (e) {
      console.error('Failed to load XHS cookies:', e)
    } finally {
      setXhsLoading(false)
    }
  }

  const fetchXhsRuntimeStatus = async () => {
    try {
      const data = await getXhsRuntimeStatus()
      setXhsRuntimeStatus(data)
    } catch (e) {
      console.error('Failed to load XHS runtime status:', e)
    }
  }

  useEffect(() => {
    fetchXhsCookies()
    fetchXhsRuntimeStatus()
    fetchDouyinCookies()
  }, [])

  const handleXhsRuntimeCheck = async () => {
    setXhsRuntimeChecking(true)
    try {
      const result = await testXhsSigner({ verify_network: false })
      modalApi.info({
        title: result.ok ? '小红书运行时自检通过' : '小红书运行时自检失败',
        width: 720,
        content: (
          <pre className="cookie-runtime-result">
            {JSON.stringify(result, null, 2)}
          </pre>
        ),
      })
      await fetchXhsRuntimeStatus()
    } catch (e) {
      const msg = e instanceof Error ? e.message : '自检失败'
      message.error(msg)
    } finally {
      setXhsRuntimeChecking(false)
    }
  }

  // 加载抖音账号列表
  const fetchDouyinCookies = async () => {
    setDouyinLoading(true)
    try {
      const data = await listDouyinCookies()
      setDouyinCookies(data)
    } catch (e) {
      console.error('Failed to load Douyin cookies:', e)
    } finally {
      setDouyinLoading(false)
    }
  }

  // 添加小红书账号
  const handleAddXhsCookie = () => {
    xhsForm.resetFields()
    setIsXhsModalOpen(true)
  }

  const handleXhsSubmit = async () => {
    try {
      const values = await xhsForm.validateFields()
      setXhsSubmitting(true)
      await createXhsCookie({
        account_name: values.account_name,
        cookie_string: values.cookie_string,
      })
      setIsXhsModalOpen(false)
      message.success('账号添加成功')
      await fetchXhsCookies()
    } catch (e) {
      const msg = e instanceof Error ? e.message : '添加失败'
      message.error(msg)
    } finally {
      setXhsSubmitting(false)
    }
  }

  // 验证账号
  const handleVerifyCookie = async (accountName: string) => {
    setVerifyingAccount(accountName)
    try {
      const result = await verifyXhsCookie(accountName)
      if (result.is_valid) {
        message.success(`账号 ${accountName} 验证通过`)
      } else {
        message.warning(`账号 ${accountName} Cookie 已失效`)
      }
      await fetchXhsCookies()
      await fetchXhsRuntimeStatus()
    } catch (e) {
      const msg = e instanceof Error ? e.message : '验证失败'
      message.error(msg)
    } finally {
      setVerifyingAccount(null)
    }
  }

  // 激活账号
  const handleActivateCookie = async (accountName: string) => {
    setActivatingAccount(accountName)
    try {
      await activateXhsCookie(accountName)
      message.success(`账号 ${accountName} 已激活`)
      await fetchXhsCookies()
    } catch (e) {
      const msg = e instanceof Error ? e.message : '激活失败'
      message.error(msg)
    } finally {
      setActivatingAccount(null)
    }
  }

  // 删除账号
  const handleDeleteCookie = async (accountName: string) => {
    try {
      await deleteXhsCookie(accountName)
      message.success(`账号 ${accountName} 已删除`)
      await fetchXhsCookies()
    } catch (e) {
      const msg = e instanceof Error ? e.message : '删除失败'
      message.error(msg)
    }
  }

  // ============ 抖音账号操作 ============

  // 添加抖音账号
  const handleAddDouyinCookie = () => {
    douyinForm.resetFields()
    setIsDouyinModalOpen(true)
  }

  const handleDouyinSubmit = async () => {
    try {
      const values = await douyinForm.validateFields()
      setDouyinSubmitting(true)
      await createDouyinCookie({
        account_name: values.account_name,
        cookie_string: values.cookie_string,
      })
      setIsDouyinModalOpen(false)
      message.success('账号添加成功')
      await fetchDouyinCookies()
    } catch (e) {
      const msg = e instanceof Error ? e.message : '添加失败'
      message.error(msg)
    } finally {
      setDouyinSubmitting(false)
    }
  }

  // 验证抖音账号
  const handleVerifyDouyinCookie = async (accountName: string) => {
    setDouyinVerifyingAccount(accountName)
    try {
      const result = await verifyDouyinCookie(accountName)
      if (result.is_valid) {
        message.success(`账号 ${accountName} 验证通过`)
      } else {
        message.warning(`账号 ${accountName} Cookie 已失效`)
      }
      await fetchDouyinCookies()
    } catch (e) {
      const msg = e instanceof Error ? e.message : '验证失败'
      message.error(msg)
    } finally {
      setDouyinVerifyingAccount(null)
    }
  }

  // 激活抖音账号
  const handleActivateDouyinCookie = async (accountName: string) => {
    setDouyinActivatingAccount(accountName)
    try {
      await activateDouyinCookie(accountName)
      message.success(`账号 ${accountName} 已激活`)
      await fetchDouyinCookies()
    } catch (e) {
      const msg = e instanceof Error ? e.message : '激活失败'
      message.error(msg)
    } finally {
      setDouyinActivatingAccount(null)
    }
  }

  // 删除抖音账号
  const handleDeleteDouyinCookie = async (accountName: string) => {
    try {
      await deleteDouyinCookie(accountName)
      message.success(`账号 ${accountName} 已删除`)
      await fetchDouyinCookies()
    } catch (e) {
      const msg = e instanceof Error ? e.message : '删除失败'
      message.error(msg)
    }
  }

  // 查看/编辑 Cookie (通用)
  const handleViewCookie = async (accountName: string, platform: 'xhs' | 'douyin' = 'xhs') => {
    setEditingAccount(accountName)
    setEditingPlatform(platform)
    setLoadingDetail(true)
    setIsEditModalOpen(true)
    try {
      if (platform === 'xhs') {
        const detail = await getXhsCookieDetail(accountName)
        editForm.setFieldsValue({
          account_name: detail.account_name,
          cookie_string: detail.cookie_string,
          is_enabled: detail.is_enabled !== false,
        })
      } else {
        const detail = await getDouyinCookieDetail(accountName)
        editForm.setFieldsValue({
          account_name: detail.account_name,
          cookie_string: detail.cookie_string,
          is_enabled: undefined,
        })
      }
    } catch (e) {
      const msg = e instanceof Error ? e.message : '获取详情失败'
      message.error(msg)
      setIsEditModalOpen(false)
    } finally {
      setLoadingDetail(false)
    }
  }

  const handleEditSubmit = async () => {
    if (!editingAccount) return
    try {
      const values = await editForm.validateFields()
      setEditSubmitting(true)
      
      const updateData: { new_account_name?: string; cookie_string?: string; is_enabled?: boolean } = {}
      if (values.account_name !== editingAccount) {
        updateData.new_account_name = values.account_name
      }
      updateData.cookie_string = values.cookie_string
      
      if (editingPlatform === 'xhs') {
        updateData.is_enabled = values.is_enabled !== false
        await updateXhsCookie(editingAccount, updateData)
        await fetchXhsCookies()
        await fetchXhsRuntimeStatus()
      } else {
        await updateDouyinCookie(editingAccount, {
          new_account_name: updateData.new_account_name,
          cookie_string: updateData.cookie_string,
        })
        await fetchDouyinCookies()
      }
      
      setIsEditModalOpen(false)
      message.success('账号已更新')
    } catch (e) {
      const msg = e instanceof Error ? e.message : '更新失败'
      message.error(msg)
    } finally {
      setEditSubmitting(false)
    }
  }

  // 小红书账号表格列
  const xhsCookieColumns: ColumnsType<XhsCookieAccount> = [
    { 
      title: '账号名称', 
      dataIndex: 'account_name', 
      key: 'account_name',
      render: (name: string, record) => (
        <Space>
          <a onClick={() => handleViewCookie(name, 'xhs')} style={{ fontWeight: 600 }}>{name}</a>
          {record.is_active && <Tag color="blue">当前激活</Tag>}
        </Space>
      ),
    },
    {
      title: '状态',
      key: 'is_valid',
      width: 100,
      render: (_, record) => {
        if (record.is_valid === null) {
          return <Tag icon={<QuestionCircleOutlined />} color="default">未验证</Tag>
        }
        return record.is_valid 
          ? <Tag icon={<CheckCircleOutlined />} color="success">有效</Tag>
          : <Tag icon={<CloseCircleOutlined />} color="error">失效</Tag>
      },
    },
    {
      title: '账号池',
      key: 'pool',
      width: 170,
      render: (_, record) => {
        const cooldown = record.cooldown_until ? new Date(record.cooldown_until) : null
        const isCooling = Boolean(cooldown && cooldown.getTime() > Date.now())
        const isQuarantined = Boolean(record.quarantined_at || record.is_valid === false)
        return (
          <Space orientation="vertical" size={2}>
            <Space size={4} wrap>
              {record.is_enabled === false && <Tag color="default">停用</Tag>}
              {record.is_enabled !== false && !isCooling && !isQuarantined && <Tag color="green">可调度</Tag>}
              {isCooling && <Tag color="orange">冷却中</Tag>}
              {isQuarantined && <Tag color="volcano">已隔离</Tag>}
              {record.consecutive_failures > 0 && <Tag color="red">连续失败 {record.consecutive_failures}</Tag>}
            </Space>
            <Text type="secondary">
              {record.lease_count || 0} 次 / 成功 {record.success_count || 0} / 失败 {record.failure_count || 0}
            </Text>
            {record.quarantine_reason && (
              <Text type="secondary" ellipsis={{ tooltip: record.quarantine_reason }}>
                {record.quarantine_reason}
              </Text>
            )}
          </Space>
        )
      },
    },
    {
      title: '最后验证',
      dataIndex: 'last_verified_at',
      key: 'last_verified_at',
      width: 170,
      render: (val: string) => <Text type="secondary">{formatDate(val)}</Text>,
    },
    {
      title: '创建时间',
      dataIndex: 'created_at',
      key: 'created_at',
      width: 170,
      render: (val: string) => <Text type="secondary">{formatDate(val)}</Text>,
    },
    {
      title: '操作',
      key: 'action',
      width: 240,
      render: (_, record) => (
        <Space size="small">
          <Tooltip title="查看/编辑 Cookie">
            <Button
              size="small"
              icon={<EyeOutlined />}
              onClick={() => handleViewCookie(record.account_name, 'xhs')}
            />
          </Tooltip>
          <Tooltip title="验证Cookie">
            <Button
              size="small"
              icon={<SyncOutlined spin={verifyingAccount === record.account_name} />}
              onClick={() => handleVerifyCookie(record.account_name)}
              loading={verifyingAccount === record.account_name}
            />
          </Tooltip>
          <Tooltip title={record.is_active ? '已激活' : '激活此账号'}>
            <Button
              size="small"
              type={record.is_active ? 'primary' : 'default'}
              icon={<ThunderboltOutlined />}
              onClick={() => handleActivateCookie(record.account_name)}
              loading={activatingAccount === record.account_name}
              disabled={record.is_active}
            />
          </Tooltip>
          <Popconfirm
            title="确认删除"
            description={`确定要删除账号 ${record.account_name} 吗？`}
            onConfirm={() => handleDeleteCookie(record.account_name)}
            okText="删除"
            cancelText="取消"
          >
            <Tooltip title="删除账号">
              <Button size="small" danger icon={<DeleteOutlined />} />
            </Tooltip>
          </Popconfirm>
        </Space>
      ),
    },
  ]

  // 抖音账号表格列
  const douyinCookieColumns: ColumnsType<DouyinCookieAccount> = [
    { 
      title: '账号名称', 
      dataIndex: 'account_name', 
      key: 'account_name',
      render: (name: string, record) => (
        <Space>
          <a onClick={() => handleViewCookie(name, 'douyin')} style={{ fontWeight: 600 }}>{name}</a>
          {record.is_active && <Tag color="blue">当前激活</Tag>}
        </Space>
      ),
    },
    {
      title: '状态',
      key: 'is_valid',
      width: 100,
      render: (_, record) => {
        if (record.is_valid === null) {
          return <Tag icon={<QuestionCircleOutlined />} color="default">未验证</Tag>
        }
        return record.is_valid 
          ? <Tag icon={<CheckCircleOutlined />} color="success">有效</Tag>
          : <Tag icon={<CloseCircleOutlined />} color="error">失效</Tag>
      },
    },
    {
      title: '最后验证',
      dataIndex: 'last_verified_at',
      key: 'last_verified_at',
      width: 170,
      render: (val: string) => <Text type="secondary">{formatDate(val)}</Text>,
    },
    {
      title: '创建时间',
      dataIndex: 'created_at',
      key: 'created_at',
      width: 170,
      render: (val: string) => <Text type="secondary">{formatDate(val)}</Text>,
    },
    {
      title: '操作',
      key: 'action',
      width: 240,
      render: (_, record) => (
        <Space size="small">
          <Tooltip title="查看/编辑 Cookie">
            <Button
              size="small"
              icon={<EyeOutlined />}
              onClick={() => handleViewCookie(record.account_name, 'douyin')}
            />
          </Tooltip>
          <Tooltip title="验证Cookie">
            <Button
              size="small"
              icon={<SyncOutlined spin={douyinVerifyingAccount === record.account_name} />}
              onClick={() => handleVerifyDouyinCookie(record.account_name)}
              loading={douyinVerifyingAccount === record.account_name}
            />
          </Tooltip>
          <Tooltip title={record.is_active ? '已激活' : '激活此账号'}>
            <Button
              size="small"
              type={record.is_active ? 'primary' : 'default'}
              icon={<ThunderboltOutlined />}
              onClick={() => handleActivateDouyinCookie(record.account_name)}
              loading={douyinActivatingAccount === record.account_name}
              disabled={record.is_active}
            />
          </Tooltip>
          <Popconfirm
            title="确认删除"
            description={`确定要删除账号 ${record.account_name} 吗？`}
            onConfirm={() => handleDeleteDouyinCookie(record.account_name)}
            okText="删除"
            cancelText="取消"
          >
            <Tooltip title="删除账号">
              <Button size="small" danger icon={<DeleteOutlined />} />
            </Tooltip>
          </Popconfirm>
        </Space>
      ),
    },
  ]

  return (
    <>
    {modalContextHolder}
    <div className="cookie-management page-container fade-in">
      <div className="page-header slide-up">
        <div>
          <Title level={2} className="page-title">
            <SettingOutlined /> Cookie 管理
          </Title>
          <Paragraph className="page-description">管理各平台的 Cookie 凭证</Paragraph>
        </div>
      </div>

      <Card className="glass-card slide-up stagger-1">
        <Tabs
          activeKey={activeTab}
          onChange={setActiveTab}
          items={[
            {
              key: 'xhs',
              label: (
                <Space>
                  <img src="https://www.xiaohongshu.com/favicon.ico" alt="小红书" style={{ width: 14, height: 14 }} />
                  小红书
                </Space>
              ),
              children: (
                <>
                  <div className="cookie-section-header">
                    <Text type="secondary">
                      小红书账号池会按策略自动选择可用账号；激活账号作为兜底账号保留。
                    </Text>
                    <Space>
                      <Button
                        icon={<SyncOutlined spin={xhsRuntimeChecking} />}
                        onClick={handleXhsRuntimeCheck}
                        loading={xhsRuntimeChecking}
                      >
                        运行时自检
                      </Button>
                      <Button type="primary" icon={<PlusOutlined />} onClick={handleAddXhsCookie}>
                        添加账号
                      </Button>
                    </Space>
                  </div>
                  {xhsRuntimeStatus && (
                    <div className="cookie-runtime-bar">
                      <Space size={[8, 8]} wrap>
                        <Tag color={xhsRuntimeStatus.account_pool.enabled ? 'green' : 'default'}>
                          账号池 {xhsRuntimeStatus.account_pool.enabled ? '开启' : '关闭'}
                        </Tag>
                        <Tag color="blue">策略 {xhsRuntimeStatus.account_pool.strategy}</Tag>
                        <Tag color="blue">搜索轮换 {xhsRuntimeStatus.account_pool.search_pages_per_account} 页/账号</Tag>
                        <Tag color="purple">
                          单关键词 {xhsRuntimeStatus.account_pool.search_max_pages_per_keyword} 页 × {xhsRuntimeStatus.account_pool.search_page_size} 条
                        </Tag>
                        <Tag color="purple">单页重试 {xhsRuntimeStatus.account_pool.search_retries_per_page}</Tag>
                        <Tag color="cyan">
                          请求错峰 {xhsRuntimeStatus.account_pool.request_interval_min_seconds}–{xhsRuntimeStatus.account_pool.request_interval_max_seconds} 秒
                        </Tag>
                        <Tag color="orange">失败 {xhsRuntimeStatus.account_pool.max_consecutive_failures} 次隔离</Tag>
                        <Tag color="green">可用 {xhsRuntimeStatus.account_pool.usable}/{xhsRuntimeStatus.account_pool.total}</Tag>
                        {xhsRuntimeStatus.account_pool.cooling_down > 0 && (
                          <Tag color="orange">冷却 {xhsRuntimeStatus.account_pool.cooling_down}</Tag>
                        )}
                        {xhsRuntimeStatus.account_pool.quarantined > 0 && (
                          <Tag color="volcano">隔离 {xhsRuntimeStatus.account_pool.quarantined}</Tag>
                        )}
                        <Tag color={xhsRuntimeStatus.proxy_pool.enabled ? 'green' : 'default'}>
                          代理池 {xhsRuntimeStatus.proxy_pool.enabled ? xhsRuntimeStatus.proxy_pool.provider : '关闭'}
                        </Tag>
                      </Space>
                    </div>
                  )}
                  <Table
                    columns={xhsCookieColumns}
                    dataSource={xhsCookies}
                    rowKey="id"
                    loading={xhsLoading}
                    pagination={false}
                    className="custom-table"
                    locale={{ emptyText: '暂无小红书账号，请添加账号 Cookie' }}
                  />
                </>
              ),
            },
            // 后续可以添加其他平台的 Cookie 管理
            // {
            //   key: 'weibo',
            //   label: '微博',
            //   children: <div>微博 Cookie 管理</div>,
            // },
            {
              key: 'douyin',
              label: (
                <Space>
                  <img src="https://www.douyin.com/favicon.ico" alt="抖音" style={{ width: 14, height: 14 }} />
                  抖音
                </Space>
              ),
              children: (
                <>
                  <div className="cookie-section-header">
                    <Text type="secondary">
                      抖音账号用于社工信息采集和视觉分析，同一时间只能有一个账号处于激活状态。
                    </Text>
                    <Button type="primary" icon={<PlusOutlined />} onClick={handleAddDouyinCookie}>
                      添加账号
                    </Button>
                  </div>
                  <Table
                    columns={douyinCookieColumns}
                    dataSource={douyinCookies}
                    rowKey="id"
                    loading={douyinLoading}
                    pagination={false}
                    className="custom-table"
                    locale={{ emptyText: '暂无抖音账号，请添加账号 Cookie' }}
                  />
                </>
              ),
            },
          ]}
        />
      </Card>

      {/* 添加小红书账号 Modal */}
      <Modal
        title="添加小红书账号"
        open={isXhsModalOpen}
        onOk={handleXhsSubmit}
        onCancel={() => setIsXhsModalOpen(false)}
        confirmLoading={xhsSubmitting}
        destroyOnHidden
        width={600}
      >
        <Form form={xhsForm} layout="vertical">
          <Form.Item
            name="account_name"
            label="账号名称"
            rules={[{ required: true, message: '请输入账号名称' }]}
          >
            <Input placeholder="例如：work_account_1" />
          </Form.Item>
          <Form.Item
            name="cookie_string"
            label="Cookie 字符串"
            rules={[{ required: true, message: '请输入 Cookie 字符串' }]}
            extra="从浏览器开发者工具获取小红书的 Cookie 字符串"
          >
            <Input.TextArea
              placeholder="a1=xxx; webId=xxx; web_session=xxx; ..."
              rows={4}
            />
          </Form.Item>
          <div className="cookie-modal-tip">
            <Text type="secondary">
              <strong>获取方法：</strong>登录小红书网页版 → 打开开发者工具 (F12) → Application → Cookies → 复制所有 Cookie 值
            </Text>
          </div>
        </Form>
      </Modal>

      {/* 添加抖音账号 Modal */}
      <Modal
        title="添加抖音账号"
        open={isDouyinModalOpen}
        onOk={handleDouyinSubmit}
        onCancel={() => setIsDouyinModalOpen(false)}
        confirmLoading={douyinSubmitting}
        destroyOnHidden
        width={600}
      >
        <Form form={douyinForm} layout="vertical">
          <Form.Item
            name="account_name"
            label="账号名称"
            rules={[{ required: true, message: '请输入账号名称' }]}
          >
            <Input placeholder="例如：douyin_account_1" />
          </Form.Item>
          <Form.Item
            name="cookie_string"
            label="Cookie 字符串"
            rules={[{ required: true, message: '请输入 Cookie 字符串' }]}
            extra="从浏览器开发者工具获取抖音的 Cookie 字符串"
          >
            <Input.TextArea
              placeholder="sessionid=xxx; passport_csrf_token=xxx; ttwid=xxx; ..."
              rows={4}
            />
          </Form.Item>
          <div className="cookie-modal-tip">
            <Text type="secondary">
              <strong>获取方法：</strong>登录抖音网页版 (douyin.com) → 打开开发者工具 (F12) → Network → 刷新页面 → 点击任意请求 → Headers → Cookie
            </Text>
          </div>
        </Form>
      </Modal>

      {/* 查看/编辑 Cookie Modal */}
      <Modal
        title="查看/编辑 Cookie"
        open={isEditModalOpen}
        onOk={handleEditSubmit}
        onCancel={() => {
          setIsEditModalOpen(false)
          setEditingAccount(null)
        }}
        confirmLoading={editSubmitting}
        okText="保存修改"
        destroyOnHidden
        width={600}
      >
        {loadingDetail ? (
          <div style={{ textAlign: 'center', padding: '24px' }}>
            <SyncOutlined spin style={{ fontSize: 24 }} />
            <div style={{ marginTop: 8 }}>加载中...</div>
          </div>
        ) : (
          <Form form={editForm} layout="vertical">
            <Form.Item
              name="account_name"
              label="账号名称"
              rules={[{ required: true, message: '请输入账号名称' }]}
            >
              <Input placeholder="输入新的账号名称" />
            </Form.Item>
            <Form.Item
              name="cookie_string"
              label="Cookie 字符串"
              rules={[{ required: true, message: '请输入 Cookie 字符串' }]}
            >
              <Input.TextArea
                placeholder="a1=xxx; webId=xxx; web_session=xxx; ..."
                rows={6}
              />
            </Form.Item>
            {editingPlatform === 'xhs' && (
              <Form.Item
                name="is_enabled"
                label="账号池调度"
                valuePropName="checked"
              >
                <Switch checkedChildren="纳入" unCheckedChildren="停用" />
              </Form.Item>
            )}
            <div className="cookie-modal-tip">
              <Text type="secondary">
                修改 Cookie 后，建议点击「验证」按钮确认新 Cookie 是否有效。
              </Text>
            </div>
          </Form>
        )}
      </Modal>
    </div>
    </>
  )
}
