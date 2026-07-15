import { useEffect, useState } from 'react'
import {
  Card, Button, Typography, Space, Modal, Form, Input, message, Tooltip,
  Popconfirm, Switch, Tabs, Tag, Empty, Alert, Spin, InputNumber, Divider,
} from 'antd'
import {
  SettingOutlined, PlusOutlined, DeleteOutlined, EditOutlined,
  RobotOutlined, ToolOutlined, LineChartOutlined, SendOutlined,
  EyeOutlined, EyeInvisibleOutlined, CopyOutlined,
  LockOutlined, DingdingOutlined, CodeOutlined, SyncOutlined,
} from '@ant-design/icons'
import {
  getAllConfig, setLLMConfig, deleteLLMConfig,
  setConfigSection, getConfigRevealStatus, revealConfig, setConfigRevealPassword,
  listToolConfigs, setToolConfig, deleteToolConfig, testToolConfig,
  setLangSmithConfig, toggleLangSmith, deleteLangSmithConfig,
  setLangfuseConfig, toggleLangfuse, deleteLangfuseConfig,
  listDingTalkBots, setDingTalkBot, toggleDingTalkBot, testDingTalkBot, deleteDingTalkBot,
  type AllConfig, type ToolConfig, type LLMConfig, type ConfigSection,
  type LangSmithConfig, type LangfuseConfig,
  type DingTalkBot, type DingTalkBotConfig, type ConfigRevealStatus,
} from '../../services/configService'
import { type CurrentUser } from '../../services/authService'
import './ConfigManagement.css'

const { Title, Paragraph } = Typography

// 预定义的工具列表
const KNOWN_TOOLS = [
  { name: 'tianyancha', label: '天眼查', icon: '🔍' },
  { name: 'hunter', label: '奇安信 Hunter', icon: '🎯' },
  { name: 'bocha', label: '博查', icon: '📊' },
  { name: 'fofa', label: 'FOFA', icon: '🛰️' },
]

const CONFIG_SECTIONS = [
  { key: 'bailian', label: '百炼 AIGC', description: '图片编辑、文生视频、图生视频' },
  { key: 'cosyvoice', label: 'CosyVoice TTS', description: '声音复刻、语音合成' },
  { key: 'chrome_docker', label: 'Chrome Docker', description: '后端浏览器容器池' },
  { key: 'easytier', label: 'EasyTier 组网', description: '公网 peer、虚拟网段、ADB 扫描和下载链接' },
  { key: 'notifications', label: '通知 Hook', description: '统一通知入口、默认通道、事件级路由策略' },
  { key: 'mobile', label: '手机 Agent', description: 'ADB 超时、视频流、执行参数' },
  { key: 'runtime', label: '运行时参数', description: '模型运行采样、超时参数' },
  { key: 'mcpServers', label: 'MCP Servers', description: 'MCP 服务连接配置' },
  { key: 'logging', label: '日志配置', description: '运行日志等级和输出' },
  { key: 'xhs_crawler', label: '小红书采集', description: '采集、账号池、签名脚本、代理池运行参数' },
  { key: 'douyin_crawler', label: '抖音采集', description: '采集运行参数' },
]

export default function ConfigManagement() {
  const [loading, setLoading] = useState(true)
  const [config, setConfig] = useState<AllConfig | null>(null)
  const [tools, setTools] = useState<ToolConfig[]>([])
  const [dingtalkBots, setDingtalkBots] = useState<DingTalkBot[]>([])
  const [isAdmin, setIsAdmin] = useState(false)
  const [revealStatus, setRevealStatus] = useState<ConfigRevealStatus | null>(null)
  const [revealedConfig, setRevealedConfig] = useState<AllConfig | null>(null)
  const [revealedTools, setRevealedTools] = useState<ToolConfig[] | null>(null)
  const [revealedDingtalkBots, setRevealedDingtalkBots] = useState<DingTalkBot[] | null>(null)

  // 显示/隐藏密钥
  const [visibleKeys, setVisibleKeys] = useState<Record<string, boolean>>({})

  // 明文查看二级密码
  const [revealModalOpen, setRevealModalOpen] = useState(false)
  const [revealSubmitting, setRevealSubmitting] = useState(false)
  const [revealForm] = Form.useForm()
  const [secondaryModalOpen, setSecondaryModalOpen] = useState(false)
  const [secondarySubmitting, setSecondarySubmitting] = useState(false)
  const [secondaryForm] = Form.useForm()

  // LLM 配置 Modal
  const [llmModalOpen, setLlmModalOpen] = useState(false)
  const [llmForm] = Form.useForm()
  const [llmSubmitting, setLlmSubmitting] = useState(false)

  // 工具配置 Modal
  const [toolModalOpen, setToolModalOpen] = useState(false)
  const [toolForm] = Form.useForm()
  const [toolSubmitting, setToolSubmitting] = useState(false)
  const [editingTool, setEditingTool] = useState<string | null>(null)

  // LangSmith Modal
  const [langsmithModalOpen, setLangsmithModalOpen] = useState(false)
  const [langsmithForm] = Form.useForm()
  const [langsmithSubmitting, setLangsmithSubmitting] = useState(false)

  // Langfuse Modal
  const [langfuseModalOpen, setLangfuseModalOpen] = useState(false)
  const [langfuseForm] = Form.useForm()
  const [langfuseSubmitting, setLangfuseSubmitting] = useState(false)

  // 钉钉机器人 Modal
  const [dingtalkModalOpen, setDingtalkModalOpen] = useState(false)
  const [dingtalkForm] = Form.useForm()
  const [dingtalkSubmitting, setDingtalkSubmitting] = useState(false)
  const [editingBot, setEditingBot] = useState<string | null>(null)
  const [testingBot, setTestingBot] = useState<string | null>(null)
  const [testingTool, setTestingTool] = useState<string | null>(null)

  // 通用配置段 Modal
  const [sectionModalOpen, setSectionModalOpen] = useState(false)
  const [sectionSubmitting, setSectionSubmitting] = useState(false)
  const [editingSection, setEditingSection] = useState<typeof CONFIG_SECTIONS[number] | null>(null)
  const [sectionJson, setSectionJson] = useState('{}')

  // 检查权限
  useEffect(() => {
    const userInfo = localStorage.getItem('userInfo')
    if (userInfo) {
      try {
        const user: CurrentUser = JSON.parse(userInfo)
        setIsAdmin(user.is_admin || user.role === 'admin')
      } catch {
        setIsAdmin(false)
      }
    }
  }, [])

  // 加载配置
  const fetchConfig = async () => {
    setLoading(true)
    try {
      const [allConfig, toolsData, botsData] = await Promise.all([
        getAllConfig(),
        listToolConfigs(),
        listDingTalkBots(),
      ])
      setConfig(allConfig)
      setTools(toolsData.tools)
      setDingtalkBots(botsData.bots)
      setRevealedConfig(null)
      setRevealedTools(null)
      setRevealedDingtalkBots(null)
      setVisibleKeys({})
    } catch (e) {
      console.error('Failed to load config:', e)
      message.error('加载配置失败')
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    fetchConfig()
  }, [])

  useEffect(() => {
    if (!isAdmin) {
      setRevealStatus(null)
      return
    }
    getConfigRevealStatus()
      .then(setRevealStatus)
      .catch(() => setRevealStatus(null))
  }, [isAdmin])

  const toolsRecordToList = (record?: Record<string, { api_key: string }>): ToolConfig[] => {
    if (!record) return []
    return Object.entries(record).map(([toolName, value]) => ({
      tool_name: toolName,
      api_key: value?.api_key || '',
      has_key: Boolean(value?.api_key),
    }))
  }

  const dingtalkRecordToList = (record?: Record<string, Partial<DingTalkBot>>): DingTalkBot[] => {
    if (!record) return []
    return Object.entries(record).map(([botName, value]) => ({
      bot_name: botName,
      access_token: value?.access_token || '',
      secret: value?.secret || '',
      keyword: value?.keyword || '',
      enabled: value?.enabled ?? true,
      has_token: Boolean(value?.access_token),
      has_outgoing_secret: Boolean(value?.has_outgoing_secret),
      stream_enabled: value?.stream_enabled ?? false,
      client_id: value?.client_id || '',
      client_secret: value?.client_secret || '',
      has_client_secret: Boolean(value?.client_secret || value?.has_client_secret),
      ai_card_streaming: value?.ai_card_streaming ?? true,
      public_base_url: value?.public_base_url || '',
      reconnect_seconds: value?.reconnect_seconds || 5,
      stream_state: value?.stream_state || 'stopped',
      stream_connected: value?.stream_connected ?? false,
      stream_last_error: value?.stream_last_error || '',
      stream_last_connected_at: value?.stream_last_connected_at,
    }))
  }

  const activeConfig = revealedConfig || config
  const activeTools = revealedTools || tools
  const activeDingtalkBots = revealedDingtalkBots || dingtalkBots
  const configUnlocked = Boolean(revealedConfig)

  // 切换密钥可见性
  const toggleKeyVisibility = (key: string) => {
    setVisibleKeys(prev => ({ ...prev, [key]: !prev[key] }))
  }

  // 复制到剪贴板
  const copyToClipboard = (text: string) => {
    navigator.clipboard.writeText(text)
    message.success('已复制到剪贴板')
  }

  // 遮蔽显示
  const maskValue = (value: string, visible: boolean) => {
    if (!value) return '-'
    if (visible) return value
    return value.length > 8 ? `${value.slice(0, 4)}...${value.slice(-4)}` : '••••••••'
  }

  const openRevealModal = () => {
    if (!isAdmin) {
      message.warning('只有管理员可以查看明文配置')
      return
    }
    if (revealStatus && !revealStatus.configured) {
      secondaryForm.resetFields()
      setSecondaryModalOpen(true)
      return
    }
    revealForm.resetFields()
    setRevealModalOpen(true)
  }

  const handleRevealSubmit = async () => {
    try {
      const values = await revealForm.validateFields()
      setRevealSubmitting(true)
      const revealed = await revealConfig(values.password)
      setRevealedConfig(revealed)
      setRevealedTools(toolsRecordToList(revealed.tools))
      setRevealedDingtalkBots(dingtalkRecordToList(revealed.dingtalk))
      setVisibleKeys({})
      setRevealModalOpen(false)
      message.success('明文配置已解锁')
    } catch (e) {
      const msg = e instanceof Error ? e.message : '解锁失败'
      message.error(msg)
    } finally {
      setRevealSubmitting(false)
    }
  }

  const handleHideRevealed = () => {
    setRevealedConfig(null)
    setRevealedTools(null)
    setRevealedDingtalkBots(null)
    setVisibleKeys({})
    message.success('已隐藏明文配置')
  }

  const handleSecondarySubmit = async () => {
    try {
      const values = await secondaryForm.validateFields()
      setSecondarySubmitting(true)
      await setConfigRevealPassword(values.current_password, values.new_password)
      setSecondaryModalOpen(false)
      secondaryForm.resetFields()
      const status = await getConfigRevealStatus()
      setRevealStatus(status)
      message.success('二级密码已更新')
    } catch (e) {
      const msg = e instanceof Error ? e.message : '保存失败'
      message.error(msg)
    } finally {
      setSecondarySubmitting(false)
    }
  }

  const renderSecretActions = (visibilityKey: string, value: string) => {
    if (!configUnlocked) {
      return (
        <Tooltip title={revealStatus?.configured ? '输入二级密码查看明文' : '先设置二级密码'}>
          <Button size="small" icon={<LockOutlined />} onClick={openRevealModal} disabled={!isAdmin} />
        </Tooltip>
      )
    }
    return (
      <>
        <Tooltip title={visibleKeys[visibilityKey] ? '隐藏' : '显示'}>
          <Button
            size="small"
            icon={visibleKeys[visibilityKey] ? <EyeInvisibleOutlined /> : <EyeOutlined />}
            onClick={() => toggleKeyVisibility(visibilityKey)}
          />
        </Tooltip>
        <Tooltip title="复制">
          <Button size="small" icon={<CopyOutlined />} onClick={() => copyToClipboard(value)} />
        </Tooltip>
      </>
    )
  }

  // ============ LLM 配置操作 ============
  const handleEditLLM = () => {
    if (activeConfig?.llm) {
      llmForm.setFieldsValue({
        api_key: '',
        base_url: activeConfig.llm.base_url,
        default_model: activeConfig.llm.default_model,
        vision_model: activeConfig.llm.vision_model,
        mobile_planner_model: activeConfig.llm.mobile_planner_model,
        mobile_executor_model: activeConfig.llm.mobile_executor_model,
        mobile_screen_model: activeConfig.llm.mobile_screen_model,
        mobile_chat_model: activeConfig.llm.mobile_chat_model,
      })
    } else {
      llmForm.resetFields()
    }
    setLlmModalOpen(true)
  }

  const handleLLMSubmit = async () => {
    try {
      const values = await llmForm.validateFields()
      setLlmSubmitting(true)
      const payload: Partial<LLMConfig> = {}
      if (values.api_key) payload.api_key = values.api_key
      if (values.base_url) payload.base_url = values.base_url
      if (values.default_model) payload.default_model = values.default_model
      if (values.vision_model) payload.vision_model = values.vision_model
      if (values.mobile_planner_model) payload.mobile_planner_model = values.mobile_planner_model
      if (values.mobile_executor_model) payload.mobile_executor_model = values.mobile_executor_model
      if (values.mobile_screen_model) payload.mobile_screen_model = values.mobile_screen_model
      if (values.mobile_chat_model) payload.mobile_chat_model = values.mobile_chat_model
      await setLLMConfig(payload)
      setLlmModalOpen(false)
      message.success('LLM 配置已更新')
      await fetchConfig()
    } catch (e) {
      const msg = e instanceof Error ? e.message : '保存失败'
      message.error(msg)
    } finally {
      setLlmSubmitting(false)
    }
  }

  const handleDeleteLLM = async () => {
    try {
      await deleteLLMConfig()
      message.success('LLM 配置已删除')
      await fetchConfig()
    } catch (e) {
      const msg = e instanceof Error ? e.message : '删除失败'
      message.error(msg)
    }
  }

  // ============ 工具配置操作 ============
  const handleAddTool = () => {
    setEditingTool(null)
    toolForm.resetFields()
    setToolModalOpen(true)
  }

  const handleEditTool = (toolName: string, _apiKey: string) => {
    setEditingTool(toolName)
    toolForm.setFieldsValue({ tool_name: toolName, api_key: '' })
    setToolModalOpen(true)
  }

  const handleToolSubmit = async () => {
    try {
      const values = await toolForm.validateFields()
      setToolSubmitting(true)
      const toolName = editingTool || values.tool_name
      await setToolConfig(toolName, values.api_key)
      setToolModalOpen(false)
      message.success(`工具 ${toolName} 配置已保存`)
      await fetchConfig()
    } catch (e) {
      const msg = e instanceof Error ? e.message : '保存失败'
      message.error(msg)
    } finally {
      setToolSubmitting(false)
    }
  }

  const handleTestTool = async (toolName: string) => {
    setTestingTool(toolName)
    try {
      const result = await testToolConfig(toolName)
      if (result.ok) {
        message.success(result.message || 'API Key 有效')
      } else {
        message.error(result.message || 'API Key 无效')
      }
    } catch (e) {
      const msg = e instanceof Error ? e.message : '测试失败'
      message.error(msg)
    } finally {
      setTestingTool(null)
    }
  }

  const handleDeleteTool = async (toolName: string) => {
    try {
      await deleteToolConfig(toolName)
      message.success(`工具 ${toolName} 配置已删除`)
      await fetchConfig()
    } catch (e) {
      const msg = e instanceof Error ? e.message : '删除失败'
      message.error(msg)
    }
  }

  // ============ LangSmith 配置操作 ============
  const handleEditLangSmith = () => {
    if (activeConfig?.langsmith) {
      langsmithForm.setFieldsValue({
        api_key: '',
        project: activeConfig.langsmith.project,
        endpoint: activeConfig.langsmith.endpoint,
      })
    } else {
      langsmithForm.resetFields()
      langsmithForm.setFieldsValue({ endpoint: 'https://api.smith.langchain.com' })
    }
    setLangsmithModalOpen(true)
  }

  const handleLangSmithSubmit = async () => {
    try {
      const values = await langsmithForm.validateFields()
      setLangsmithSubmitting(true)
      const payload: Partial<LangSmithConfig> = {}
      if (values.api_key) payload.api_key = values.api_key
      if (values.project) payload.project = values.project
      if (values.endpoint) payload.endpoint = values.endpoint
      await setLangSmithConfig(payload)
      setLangsmithModalOpen(false)
      message.success('LangSmith 配置已更新')
      await fetchConfig()
    } catch (e) {
      const msg = e instanceof Error ? e.message : '保存失败'
      message.error(msg)
    } finally {
      setLangsmithSubmitting(false)
    }
  }

  const handleToggleLangSmith = async (enabled: boolean) => {
    try {
      await toggleLangSmith(enabled)
      message.success(enabled ? 'LangSmith 已启用' : 'LangSmith 已禁用')
      await fetchConfig()
    } catch (e) {
      const msg = e instanceof Error ? e.message : '操作失败'
      message.error(msg)
    }
  }

  const handleDeleteLangSmith = async () => {
    try {
      await deleteLangSmithConfig()
      message.success('LangSmith 配置已删除')
      await fetchConfig()
    } catch (e) {
      const msg = e instanceof Error ? e.message : '删除失败'
      message.error(msg)
    }
  }

  // ============ Langfuse 配置操作 ============
  const handleEditLangfuse = () => {
    if (activeConfig?.langfuse) {
      langfuseForm.setFieldsValue({
        secret_key: '',
        public_key: '',
        base_url: activeConfig.langfuse.base_url,
      })
    } else {
      langfuseForm.resetFields()
      langfuseForm.setFieldsValue({ base_url: 'https://cloud.langfuse.com' })
    }
    setLangfuseModalOpen(true)
  }

  const handleLangfuseSubmit = async () => {
    try {
      const values = await langfuseForm.validateFields()
      setLangfuseSubmitting(true)
      const payload: Partial<LangfuseConfig> = {}
      if (values.secret_key) payload.secret_key = values.secret_key
      if (values.public_key) payload.public_key = values.public_key
      if (values.base_url) payload.base_url = values.base_url
      await setLangfuseConfig(payload)
      setLangfuseModalOpen(false)
      message.success('Langfuse 配置已更新')
      await fetchConfig()
    } catch (e) {
      const msg = e instanceof Error ? e.message : '保存失败'
      message.error(msg)
    } finally {
      setLangfuseSubmitting(false)
    }
  }

  const handleToggleLangfuse = async (enabled: boolean) => {
    try {
      await toggleLangfuse(enabled)
      message.success(enabled ? 'Langfuse 已启用' : 'Langfuse 已禁用')
      await fetchConfig()
    } catch (e) {
      const msg = e instanceof Error ? e.message : '操作失败'
      message.error(msg)
    }
  }

  const handleDeleteLangfuse = async () => {
    try {
      await deleteLangfuseConfig()
      message.success('Langfuse 配置已删除')
      await fetchConfig()
    } catch (e) {
      const msg = e instanceof Error ? e.message : '删除失败'
      message.error(msg)
    }
  }

  // ============ 钉钉机器人操作 ============
  const handleAddDingTalk = () => {
    setEditingBot(null)
    dingtalkForm.resetFields()
    dingtalkForm.setFieldsValue({
      stream_enabled: true,
      ai_card_streaming: true,
      reconnect_seconds: 5,
    })
    setDingtalkModalOpen(true)
  }

  const handleEditDingTalk = (bot: DingTalkBot) => {
    setEditingBot(bot.bot_name)
    dingtalkForm.setFieldsValue({
      bot_name: bot.bot_name,
      access_token: '',
      secret: '',
      keyword: bot.keyword,
      outgoing_app_secret: '',
      stream_enabled: bot.stream_enabled,
      client_id: bot.client_id,
      client_secret: '',
      ai_card_streaming: bot.ai_card_streaming,
      public_base_url: bot.public_base_url,
      reconnect_seconds: bot.reconnect_seconds || 5,
    })
    setDingtalkModalOpen(true)
  }

  const handleDingTalkSubmit = async () => {
    try {
      const values = await dingtalkForm.validateFields()
      setDingtalkSubmitting(true)
      const botName = editingBot || values.bot_name
      const payload: DingTalkBotConfig = {}
      if (values.access_token) payload.access_token = values.access_token
      if (values.secret) payload.secret = values.secret
      if (values.keyword !== undefined) payload.keyword = values.keyword
      if (values.outgoing_app_secret) payload.outgoing_app_secret = values.outgoing_app_secret
      payload.stream_enabled = Boolean(values.stream_enabled)
      if (values.client_id !== undefined) payload.client_id = values.client_id
      if (values.client_secret) payload.client_secret = values.client_secret
      payload.ai_card_streaming = Boolean(values.ai_card_streaming)
      if (values.public_base_url !== undefined) payload.public_base_url = values.public_base_url
      if (values.reconnect_seconds !== undefined) payload.reconnect_seconds = values.reconnect_seconds
      await setDingTalkBot(botName, payload)
      setDingtalkModalOpen(false)
      message.success(`钉钉机器人 ${botName} 配置已保存`)
      await fetchConfig()
    } catch (e) {
      const msg = e instanceof Error ? e.message : '保存失败'
      message.error(msg)
    } finally {
      setDingtalkSubmitting(false)
    }
  }

  const handleToggleDingTalk = async (botName: string, enabled: boolean) => {
    try {
      await toggleDingTalkBot(botName, enabled)
      message.success(enabled ? `机器人 ${botName} 已启用` : `机器人 ${botName} 已禁用`)
      await fetchConfig()
    } catch (e) {
      const msg = e instanceof Error ? e.message : '操作失败'
      message.error(msg)
    }
  }

  const handleTestDingTalk = async (botName: string) => {
    setTestingBot(botName)
    try {
      const result = await testDingTalkBot(botName)
      if (result.ok) {
        message.success(result.message || '测试消息发送成功')
      } else {
        message.error(result.message || '测试失败')
      }
    } catch (e) {
      const msg = e instanceof Error ? e.message : '测试失败'
      message.error(msg)
    } finally {
      setTestingBot(null)
    }
  }

  const handleDeleteDingTalk = async (botName: string) => {
    try {
      await deleteDingTalkBot(botName)
      message.success(`钉钉机器人 ${botName} 配置已删除`)
      await fetchConfig()
    } catch (e) {
      const msg = e instanceof Error ? e.message : '删除失败'
      message.error(msg)
    }
  }

  // 获取工具显示信息
  const getToolInfo = (toolName: string) => {
    const known = KNOWN_TOOLS.find(t => t.name === toolName)
    return known || { name: toolName, label: toolName, icon: '🔧' }
  }

  const asConfigSection = (value: unknown): ConfigSection => {
    if (value && typeof value === 'object' && !Array.isArray(value)) {
      return value as ConfigSection
    }
    return {}
  }

  const getSectionConfig = (sectionKey: string): ConfigSection => {
    const direct = activeConfig?.[sectionKey as keyof AllConfig]
    if (direct && typeof direct === 'object' && !Array.isArray(direct)) {
      return direct as ConfigSection
    }
    return asConfigSection(activeConfig?.configs?.[sectionKey])
  }

  const formatSectionPreview = (sectionConfig: ConfigSection) => {
    const text = JSON.stringify(sectionConfig, null, 2)
    if (text.length <= 520) return text
    return `${text.slice(0, 520)}\n...`
  }

  const handleEditSection = (section: typeof CONFIG_SECTIONS[number]) => {
    setEditingSection(section)
    setSectionJson(JSON.stringify(getSectionConfig(section.key), null, 2))
    setSectionModalOpen(true)
  }

  const handleSectionSubmit = async () => {
    if (!editingSection) return
    let parsed: unknown
    try {
      parsed = JSON.parse(sectionJson || '{}')
    } catch {
      message.error('JSON 格式不正确')
      return
    }
    if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) {
      message.error('配置段必须是 JSON 对象')
      return
    }

    setSectionSubmitting(true)
    try {
      await setConfigSection(editingSection.key, parsed as ConfigSection)
      setSectionModalOpen(false)
      message.success(`${editingSection.label} 配置已保存`)
      await fetchConfig()
    } catch (e) {
      const msg = e instanceof Error ? e.message : '保存失败'
      message.error(msg)
    } finally {
      setSectionSubmitting(false)
    }
  }

  if (loading) {
    return (
      <div className="config-management page-container fade-in">
        <div style={{ display: 'flex', justifyContent: 'center', alignItems: 'center', minHeight: 400 }}>
          <Spin size="large" />
        </div>
      </div>
    )
  }

  // Tab 内容
  const tabItems = [
    {
      key: 'llm',
      label: <span><RobotOutlined /> LLM 配置</span>,
      children: (
        <Card className="glass-card">
          <div className="config-section-header">
            <div className="config-section-title">
              <RobotOutlined />
              <span>大模型配置</span>
            </div>
            <Space>
              <Button icon={<EditOutlined />} onClick={handleEditLLM} disabled={!isAdmin}>
                {activeConfig?.llm?.api_key ? '编辑' : '配置'}
              </Button>
              {activeConfig?.llm?.api_key && (
                <Popconfirm title="确认删除 LLM 配置？" onConfirm={handleDeleteLLM} okText="删除" cancelText="取消">
                  <Button danger icon={<DeleteOutlined />} disabled={!isAdmin}>删除</Button>
                </Popconfirm>
              )}
            </Space>
          </div>
          {activeConfig?.llm?.api_key ? (
            <div>
              <div className="config-item">
                <div className="config-item-info">
                  <div className="config-item-label">API Key</div>
                  <div className="config-item-value">{maskValue(activeConfig.llm.api_key, visibleKeys['llm_key'])}</div>
                </div>
                <div className="config-item-actions">
                  {renderSecretActions('llm_key', activeConfig.llm.api_key)}
                </div>
              </div>
              <div className="config-item">
                <div className="config-item-info">
                  <div className="config-item-label">Base URL</div>
                  <div className="config-item-value">{activeConfig.llm.base_url || '-'}</div>
                </div>
              </div>
              <div className="config-item">
                <div className="config-item-info">
                  <div className="config-item-label">默认模型</div>
                  <div className="config-item-value">{activeConfig.llm.default_model || '-'}</div>
                </div>
              </div>
              <div className="config-item">
                <div className="config-item-info">
                  <div className="config-item-label">视觉模型</div>
                  <div className="config-item-value">{activeConfig.llm.vision_model || '-'}</div>
                </div>
              </div>
              <div className="config-item">
                <div className="config-item-info">
                  <div className="config-item-label">手机规划模型</div>
                  <div className="config-item-value">{activeConfig.llm.mobile_planner_model || activeConfig.llm.default_model || '-'}</div>
                </div>
              </div>
              <div className="config-item">
                <div className="config-item-info">
                  <div className="config-item-label">手机执行模型</div>
                  <div className="config-item-value">{activeConfig.llm.mobile_executor_model || activeConfig.llm.vision_model || '-'}</div>
                </div>
              </div>
              <div className="config-item">
                <div className="config-item-info">
                  <div className="config-item-label">手机读屏模型</div>
                  <div className="config-item-value">{activeConfig.llm.mobile_screen_model || activeConfig.llm.mobile_executor_model || activeConfig.llm.vision_model || '-'}</div>
                </div>
              </div>
              <div className="config-item">
                <div className="config-item-info">
                  <div className="config-item-label">手机聊天模型</div>
                  <div className="config-item-value">{activeConfig.llm.mobile_chat_model || activeConfig.llm.default_model || '-'}</div>
                </div>
              </div>
            </div>
          ) : (
            <Empty description="暂未配置 LLM" image={Empty.PRESENTED_IMAGE_SIMPLE} />
          )}
        </Card>
      ),
    },
    {
      key: 'tools',
      label: <span><ToolOutlined /> 工具 API</span>,
      children: (
        <Card className="glass-card">
          <div className="config-section-header">
            <div className="config-section-title">
              <ToolOutlined />
              <span>工具 API Key</span>
            </div>
            <Button type="primary" icon={<PlusOutlined />} onClick={handleAddTool} disabled={!isAdmin}>
              添加工具
            </Button>
          </div>
          {activeTools.length > 0 ? (
            <div className="tool-list">
              {activeTools.map(tool => {
                const info = getToolInfo(tool.tool_name)
                return (
                  <div key={tool.tool_name} className="tool-item">
                    <div className="tool-info">
                      <div className="tool-icon">{info.icon}</div>
                      <div>
                        <div className="tool-name">{info.label}</div>
                        <div className="tool-key">
                          {maskValue(tool.api_key, visibleKeys[`tool_${tool.tool_name}`])}
                        </div>
                      </div>
                    </div>
                    <div className="tool-actions">
                      {renderSecretActions(`tool_${tool.tool_name}`, tool.api_key)}
                      <Tooltip title="测试有效性">
                        <Button size="small" icon={<SendOutlined />}
                          loading={testingTool === tool.tool_name}
                          onClick={() => handleTestTool(tool.tool_name)} disabled={!isAdmin} />
                      </Tooltip>
                      <Tooltip title="编辑">
                        <Button size="small" icon={<EditOutlined />}
                          onClick={() => handleEditTool(tool.tool_name, tool.api_key)} disabled={!isAdmin} />
                      </Tooltip>
                      <Popconfirm title={`确认删除 ${info.label} 配置？`}
                        onConfirm={() => handleDeleteTool(tool.tool_name)} okText="删除" cancelText="取消">
                        <Tooltip title="删除">
                          <Button size="small" danger icon={<DeleteOutlined />} disabled={!isAdmin} />
                        </Tooltip>
                      </Popconfirm>
                    </div>
                  </div>
                )
              })}
            </div>
          ) : (
            <Empty description="暂无工具配置" image={Empty.PRESENTED_IMAGE_SIMPLE} />
          )}
        </Card>
      ),
    },
    {
      key: 'tracking',
      label: <span><LineChartOutlined /> 追踪配置</span>,
      children: (
        <Space direction="vertical" size="large" style={{ width: '100%' }}>
          {/* LangSmith */}
          <Card className="glass-card tracking-card">
            <div className="tracking-header">
              <div className="tracking-title">
                <LineChartOutlined style={{ color: '#1890ff' }} />
                <span className="tracking-name">LangSmith</span>
                <Tag color={activeConfig?.langsmith?.enabled ? 'success' : 'default'}>
                  {activeConfig?.langsmith?.enabled ? '已启用' : '未启用'}
                </Tag>
              </div>
              <Space>
                <Switch checked={activeConfig?.langsmith?.enabled} onChange={handleToggleLangSmith}
                  checkedChildren="开" unCheckedChildren="关" disabled={!isAdmin} />
                <Button icon={<EditOutlined />} onClick={handleEditLangSmith} disabled={!isAdmin}>编辑</Button>
                {activeConfig?.langsmith?.api_key && (
                  <Popconfirm title="确认删除 LangSmith 配置？" onConfirm={handleDeleteLangSmith}>
                    <Button danger icon={<DeleteOutlined />} disabled={!isAdmin}>删除</Button>
                  </Popconfirm>
                )}
              </Space>
            </div>
            {activeConfig?.langsmith?.api_key ? (
              <div className="tracking-fields">
                <div className="tracking-field">
                  <span className="tracking-field-label">API Key</span>
                  <Space>
                    <span className="tracking-field-value">
                      {maskValue(activeConfig.langsmith.api_key, visibleKeys['langsmith_key'])}
                    </span>
                    {renderSecretActions('langsmith_key', activeConfig.langsmith.api_key)}
                  </Space>
                </div>
                <div className="tracking-field">
                  <span className="tracking-field-label">项目</span>
                  <span className="tracking-field-value">{activeConfig.langsmith.project || '-'}</span>
                </div>
                <div className="tracking-field">
                  <span className="tracking-field-label">端点</span>
                  <span className="tracking-field-value">{activeConfig.langsmith.endpoint || '-'}</span>
                </div>
              </div>
            ) : (
              <Empty description="暂未配置 LangSmith" image={Empty.PRESENTED_IMAGE_SIMPLE} />
            )}
          </Card>

          {/* Langfuse */}
          <Card className="glass-card tracking-card">
            <div className="tracking-header">
              <div className="tracking-title">
                <LineChartOutlined style={{ color: '#52c41a' }} />
                <span className="tracking-name">Langfuse</span>
                <Tag color={activeConfig?.langfuse?.enabled ? 'success' : 'default'}>
                  {activeConfig?.langfuse?.enabled ? '已启用' : '未启用'}
                </Tag>
              </div>
              <Space>
                <Switch checked={activeConfig?.langfuse?.enabled} onChange={handleToggleLangfuse}
                  checkedChildren="开" unCheckedChildren="关" disabled={!isAdmin} />
                <Button icon={<EditOutlined />} onClick={handleEditLangfuse} disabled={!isAdmin}>编辑</Button>
                {activeConfig?.langfuse?.secret_key && (
                  <Popconfirm title="确认删除 Langfuse 配置？" onConfirm={handleDeleteLangfuse}>
                    <Button danger icon={<DeleteOutlined />} disabled={!isAdmin}>删除</Button>
                  </Popconfirm>
                )}
              </Space>
            </div>
            {activeConfig?.langfuse?.secret_key ? (
              <div className="tracking-fields">
                <div className="tracking-field">
                  <span className="tracking-field-label">Secret Key</span>
                  <Space>
                    <span className="tracking-field-value">
                      {maskValue(activeConfig.langfuse.secret_key, visibleKeys['langfuse_secret'])}
                    </span>
                    {renderSecretActions('langfuse_secret', activeConfig.langfuse.secret_key)}
                  </Space>
                </div>
                <div className="tracking-field">
                  <span className="tracking-field-label">Public Key</span>
                  <Space>
                    <span className="tracking-field-value">
                      {maskValue(activeConfig.langfuse.public_key, visibleKeys['langfuse_public'])}
                    </span>
                    {renderSecretActions('langfuse_public', activeConfig.langfuse.public_key)}
                  </Space>
                </div>
                <div className="tracking-field">
                  <span className="tracking-field-label">Base URL</span>
                  <span className="tracking-field-value">{activeConfig.langfuse.base_url || '-'}</span>
                </div>
              </div>
            ) : (
              <Empty description="暂未配置 Langfuse" image={Empty.PRESENTED_IMAGE_SIMPLE} />
            )}
          </Card>
        </Space>
      ),
    },
    {
      key: 'sections',
      label: <span><CodeOutlined /> 运行配置</span>,
      children: (
        <Card className="glass-card">
          <div className="config-section-header">
            <div className="config-section-title">
              <CodeOutlined />
              <span>运行配置段</span>
            </div>
          </div>
          <div className="section-config-list">
            {CONFIG_SECTIONS.map(section => {
              const sectionConfig = getSectionConfig(section.key)
              const configured = Object.keys(sectionConfig).length > 0
              return (
                <div key={section.key} className="section-config-item">
                  <div className="section-config-main">
                    <div className="section-config-title-row">
                      <span className="section-config-name">{section.label}</span>
                      <Tag color={configured ? 'success' : 'default'}>
                        {configured ? '已配置' : '未配置'}
                      </Tag>
                    </div>
                    <div className="section-config-description">{section.description}</div>
                    {configured ? (
                      <pre className="section-config-preview">{formatSectionPreview(sectionConfig)}</pre>
                    ) : (
                      <div className="section-config-empty">{"{}"}</div>
                    )}
                  </div>
                  <Button icon={<EditOutlined />} onClick={() => handleEditSection(section)} disabled={!isAdmin}>
                    编辑
                  </Button>
                </div>
              )
            })}
          </div>
        </Card>
      ),
    },
    {
      key: 'dingtalk',
      label: <span><DingdingOutlined /> 钉钉机器人</span>,
      children: (
        <Card className="glass-card">
          <div className="config-section-header">
            <div className="config-section-title">
              <DingdingOutlined />
              <span>钉钉机器人配置</span>
            </div>
            <Button type="primary" icon={<PlusOutlined />} onClick={handleAddDingTalk} disabled={!isAdmin}>
              添加机器人
            </Button>
          </div>
          {activeDingtalkBots.length > 0 ? (
            <div className="tool-list">
              {activeDingtalkBots.map(bot => (
                <div key={bot.bot_name} className="dingtalk-bot-item">
                  <div className="bot-info">
                    <div className="bot-icon">
                      <DingdingOutlined style={{ fontSize: 20, color: '#1890ff' }} />
                    </div>
                    <div className="bot-details">
                      <div className="bot-name">
                        {bot.bot_name}
                        <Tag color={bot.enabled ? 'success' : 'default'} style={{ marginLeft: 8 }}>
                          {bot.enabled ? '已启用' : '已禁用'}
                        </Tag>
                        {bot.stream_enabled && (
                          <Tag color={bot.stream_connected ? 'processing' : bot.stream_state === 'reconnecting' ? 'warning' : 'default'}>
                            Stream {bot.stream_connected ? '已连接' : bot.stream_state === 'reconnecting' ? '重连中' : '未连接'}
                          </Tag>
                        )}
                      </div>
                      <div className="bot-meta">
                        {bot.has_token && <span>Token: {maskValue(bot.access_token, visibleKeys[`dingtalk_${bot.bot_name}_token`])}</span>}
                        {bot.client_id && <span style={{ marginLeft: bot.has_token ? 16 : 0 }}>Client ID: {bot.client_id}</span>}
                        {bot.keyword && <span style={{ marginLeft: 16 }}>关键词: {bot.keyword}</span>}
                      </div>
                      {bot.has_client_secret && (
                        <div className="bot-meta">
                          <Space size={4}>
                            <span>
                              Client Secret: {maskValue(
                                bot.client_secret || '********',
                                visibleKeys[`dingtalk_${bot.bot_name}_client_secret`],
                              )}
                            </span>
                            {renderSecretActions(
                              `dingtalk_${bot.bot_name}_client_secret`,
                              bot.client_secret || '',
                            )}
                          </Space>
                        </div>
                      )}
                      {bot.stream_last_error && (
                        <div className="bot-stream-error" title={bot.stream_last_error}>
                          {bot.stream_last_error}
                        </div>
                      )}
                    </div>
                  </div>
                  <div className="bot-actions">
                    <Switch
                      size="small"
                      checked={bot.enabled}
                      onChange={(checked) => handleToggleDingTalk(bot.bot_name, checked)}
                      disabled={!isAdmin}
                    />
                    {bot.has_token && renderSecretActions(`dingtalk_${bot.bot_name}_token`, bot.access_token)}
                    <Tooltip title="刷新连接状态">
                      <Button size="small" icon={<SyncOutlined />} onClick={fetchConfig} />
                    </Tooltip>
                    <Tooltip title={bot.has_token ? '测试 Webhook 通知' : '未配置 Webhook Access Token'}>
                      <Button size="small" icon={<SendOutlined />}
                        loading={testingBot === bot.bot_name}
                        onClick={() => handleTestDingTalk(bot.bot_name)}
                        disabled={!isAdmin || !bot.enabled || !bot.has_token} />
                    </Tooltip>
                    <Tooltip title="编辑">
                      <Button size="small" icon={<EditOutlined />}
                        onClick={() => handleEditDingTalk(bot)} disabled={!isAdmin} />
                    </Tooltip>
                    <Popconfirm title={`确认删除机器人 ${bot.bot_name}？`}
                      onConfirm={() => handleDeleteDingTalk(bot.bot_name)} okText="删除" cancelText="取消">
                      <Tooltip title="删除">
                        <Button size="small" danger icon={<DeleteOutlined />} disabled={!isAdmin} />
                      </Tooltip>
                    </Popconfirm>
                  </div>
                </div>
              ))}
            </div>
          ) : (
            <Empty description="暂无钉钉机器人配置" image={Empty.PRESENTED_IMAGE_SIMPLE} />
          )}
        </Card>
      ),
    },
  ]

  return (
    <div className="config-management page-container fade-in">
      <div className="page-header slide-up">
        <div>
          <Title level={2} className="page-title">
            <SettingOutlined /> 系统配置
          </Title>
          <Paragraph className="page-description">管理 LLM、工具 API Key 和追踪服务配置</Paragraph>
        </div>
        {isAdmin && (
          <Space className="config-security-actions" wrap>
            {configUnlocked ? (
              <Button icon={<EyeInvisibleOutlined />} onClick={handleHideRevealed}>
                隐藏明文
              </Button>
            ) : (
              <Button type="primary" icon={<EyeOutlined />} onClick={openRevealModal}>
                查看明文
              </Button>
            )}
            <Button
              icon={<LockOutlined />}
              onClick={() => {
                secondaryForm.resetFields()
                setSecondaryModalOpen(true)
              }}
            >
              设置二级密码
            </Button>
          </Space>
        )}
      </div>

      {!isAdmin && (
        <Alert
          type="warning"
          showIcon
          icon={<LockOutlined />}
          title="权限受限"
          description="您当前为普通用户，只能查看配置，无法进行修改操作。如需修改请联系管理员。"
          style={{ marginBottom: 24 }}
          className="slide-up stagger-1"
        />
      )}

      {isAdmin && configUnlocked && (
        <Alert
          type="info"
          showIcon
          title="明文配置已临时解锁"
          description="离开页面、刷新配置或点击隐藏明文后会恢复脱敏显示。"
          className="config-unlocked-alert slide-up stagger-1"
        />
      )}

      <div className="slide-up stagger-2">
        <Tabs items={tabItems} size="large" />
      </div>

      {/* 明文查看 Modal */}
      <Modal
        title="查看明文配置"
        open={revealModalOpen}
        onOk={handleRevealSubmit}
        onCancel={() => setRevealModalOpen(false)}
        confirmLoading={revealSubmitting}
        destroyOnHidden
        width={420}
      >
        <Alert
          type="warning"
          showIcon
          title="仅管理员可查看明文"
          description="请输入配置明文查看二级密码。明文只在当前页面临时展示，不会通过普通配置接口返回。"
          style={{ marginBottom: 16 }}
        />
        <Form form={revealForm} layout="vertical">
          <Form.Item name="password" label="二级密码" rules={[{ required: true, message: '请输入二级密码' }]}>
            <Input.Password autoFocus placeholder="输入二级密码" />
          </Form.Item>
        </Form>
      </Modal>

      {/* 二级密码 Modal */}
      <Modal
        title="设置配置明文查看二级密码"
        open={secondaryModalOpen}
        onOk={handleSecondarySubmit}
        onCancel={() => setSecondaryModalOpen(false)}
        confirmLoading={secondarySubmitting}
        destroyOnHidden
        width={460}
      >
        <Alert
          type="info"
          showIcon
          title={revealStatus?.configured ? '修改二级密码' : '首次设置二级密码'}
          description={revealStatus?.configured ? '请输入当前二级密码后设置新二级密码。' : '首次设置时，当前密码填写管理员登录密码。'}
          style={{ marginBottom: 16 }}
        />
        <Form form={secondaryForm} layout="vertical">
          <Form.Item
            name="current_password"
            label={revealStatus?.configured ? '当前二级密码' : '管理员登录密码'}
            rules={[{ required: true, message: '请输入当前密码' }]}
          >
            <Input.Password placeholder={revealStatus?.configured ? '输入当前二级密码' : '输入管理员登录密码'} />
          </Form.Item>
          <Form.Item
            name="new_password"
            label="新二级密码"
            rules={[
              { required: true, message: '请输入新二级密码' },
              { min: 8, message: '新二级密码至少 8 位' },
            ]}
          >
            <Input.Password placeholder="至少 8 位" />
          </Form.Item>
          <Form.Item
            name="confirm_password"
            label="确认新二级密码"
            dependencies={['new_password']}
            rules={[
              { required: true, message: '请再次输入新二级密码' },
              ({ getFieldValue }) => ({
                validator(_, value) {
                  if (!value || getFieldValue('new_password') === value) {
                    return Promise.resolve()
                  }
                  return Promise.reject(new Error('两次输入的新二级密码不一致'))
                },
              }),
            ]}
          >
            <Input.Password placeholder="再次输入新二级密码" />
          </Form.Item>
        </Form>
      </Modal>

      {/* LLM 配置 Modal */}
      <Modal title="LLM 配置" open={llmModalOpen} onOk={handleLLMSubmit}
        onCancel={() => setLlmModalOpen(false)} confirmLoading={llmSubmitting} destroyOnHidden width={640}>
        <Form form={llmForm} layout="vertical">
          <Form.Item name="api_key" label="API Key" extra="留空则不修改">
            <Input.Password placeholder="输入 API Key" />
          </Form.Item>
          <Form.Item name="base_url" label="Base URL">
            <Input placeholder="https://api.openai.com/v1" />
          </Form.Item>
          <Form.Item name="default_model" label="默认模型">
            <Input placeholder="gpt-4" />
          </Form.Item>
          <Form.Item name="vision_model" label="视觉模型">
            <Input placeholder="gpt-4o" />
          </Form.Item>
          <Form.Item name="mobile_planner_model" label="手机规划模型" extra="留空则回退到默认模型">
            <Input placeholder="如 qwen3-max" />
          </Form.Item>
          <Form.Item name="mobile_executor_model" label="手机执行模型" extra="留空则回退到视觉模型">
            <Input placeholder="如 qwen-vl-max" />
          </Form.Item>
          <Form.Item name="mobile_screen_model" label="手机读屏模型" extra="留空则回退到手机执行模型">
            <Input placeholder="如 qwen-vl-max" />
          </Form.Item>
          <Form.Item name="mobile_chat_model" label="手机聊天模型" extra="留空则回退到默认模型">
            <Input placeholder="如 qwen3-max" />
          </Form.Item>
        </Form>
      </Modal>

      {/* 通用配置段 Modal */}
      <Modal title={editingSection ? `${editingSection.label} 配置` : '运行配置'}
        open={sectionModalOpen} onOk={handleSectionSubmit}
        onCancel={() => setSectionModalOpen(false)}
        confirmLoading={sectionSubmitting} destroyOnHidden width={760}>
        <Input.TextArea
          className="section-config-editor"
          value={sectionJson}
          onChange={(event) => setSectionJson(event.target.value)}
          autoSize={{ minRows: 14, maxRows: 26 }}
          spellCheck={false}
        />
      </Modal>

      {/* 工具配置 Modal */}
      <Modal title={editingTool ? `编辑工具 - ${getToolInfo(editingTool).label}` : '添加工具'}
        open={toolModalOpen} onOk={handleToolSubmit} onCancel={() => setToolModalOpen(false)}
        confirmLoading={toolSubmitting} destroyOnHidden width={450}>
        <Form form={toolForm} layout="vertical">
          {!editingTool && (
            <Form.Item name="tool_name" label="工具名称" rules={[{ required: true, message: '请输入工具名称' }]}>
              <Input placeholder="如 tianyancha, hunter, bocha" />
            </Form.Item>
          )}
          <Form.Item name="api_key" label="API Key" rules={[{ required: true, message: '请输入 API Key' }]}>
            <Input.Password placeholder="输入 API Key" />
          </Form.Item>
        </Form>
      </Modal>

      {/* LangSmith 配置 Modal */}
      <Modal title="LangSmith 配置" open={langsmithModalOpen} onOk={handleLangSmithSubmit}
        onCancel={() => setLangsmithModalOpen(false)} confirmLoading={langsmithSubmitting} destroyOnHidden width={500}>
        <Form form={langsmithForm} layout="vertical">
          <Form.Item name="api_key" label="API Key" extra="留空则不修改">
            <Input.Password placeholder="lsv2_pt_xxx" />
          </Form.Item>
          <Form.Item name="project" label="项目名称">
            <Input placeholder="my-project" />
          </Form.Item>
          <Form.Item name="endpoint" label="API 端点">
            <Input placeholder="https://api.smith.langchain.com" />
          </Form.Item>
        </Form>
      </Modal>

      {/* Langfuse 配置 Modal */}
      <Modal title="Langfuse 配置" open={langfuseModalOpen} onOk={handleLangfuseSubmit}
        onCancel={() => setLangfuseModalOpen(false)} confirmLoading={langfuseSubmitting} destroyOnHidden width={500}>
        <Form form={langfuseForm} layout="vertical">
          <Form.Item name="secret_key" label="Secret Key" extra="留空则不修改">
            <Input.Password placeholder="sk-lf-xxx" />
          </Form.Item>
          <Form.Item name="public_key" label="Public Key" extra="留空则不修改">
            <Input.Password placeholder="pk-lf-xxx" />
          </Form.Item>
          <Form.Item name="base_url" label="Base URL">
            <Input placeholder="https://cloud.langfuse.com" />
          </Form.Item>
        </Form>
      </Modal>

      {/* 钉钉机器人配置 Modal */}
      <Modal title={editingBot ? `编辑钉钉机器人 - ${editingBot}` : '添加钉钉机器人'}
        open={dingtalkModalOpen} onOk={handleDingTalkSubmit} onCancel={() => setDingtalkModalOpen(false)}
        confirmLoading={dingtalkSubmitting} destroyOnHidden width={640}>
        <Form form={dingtalkForm} layout="vertical">
          {!editingBot && (
            <Form.Item name="bot_name" label="机器人名称" rules={[{ required: true, message: '请输入机器人名称' }]}>
              <Input placeholder="如 default, alert, notify" />
            </Form.Item>
          )}
          <Alert
            type="info"
            showIcon
            style={{ marginBottom: 16 }}
            title="Stream Mode 用于 AI 对话，Webhook 用于主动通知，两套配置可独立使用。"
            description={
              <span>
                在钉钉开发者后台创建企业内部应用并添加机器人，消息接收模式选择 Stream Mode，
                然后填写应用的 Client ID 与 Client Secret。无需配置公网回调 URL。
              </span>
            }
          />
          <Form.Item name="stream_enabled" label="启用 Stream Mode" valuePropName="checked">
            <Switch />
          </Form.Item>
          <Form.Item
            noStyle
            shouldUpdate={(prev, current) => prev.stream_enabled !== current.stream_enabled}
          >
            {({ getFieldValue }) => getFieldValue('stream_enabled') ? (
              <>
                <Form.Item
                  name="client_id"
                  label="Client ID（AppKey）"
                  rules={[{ required: true, message: '请输入 Client ID' }]}
                >
                  <Input placeholder="应用凭证中的 Client ID / AppKey" />
                </Form.Item>
                <Form.Item
                  name="client_secret"
                  label="Client Secret（AppSecret）"
                  extra={editingBot ? '留空则保留已加密保存的 Client Secret' : undefined}
                  rules={[{
                    validator: async (_, value) => {
                      const existing = dingtalkBots.find(bot => bot.bot_name === editingBot)
                      if (!value && !(editingBot && existing?.has_client_secret)) {
                        throw new Error('请输入 Client Secret')
                      }
                    },
                  }]}
                >
                  <Input.Password placeholder="应用凭证中的 Client Secret / AppSecret" />
                </Form.Item>
                <Form.Item name="ai_card_streaming" label="AI Card 流式回答" valuePropName="checked">
                  <Switch />
                </Form.Item>
                <Form.Item name="public_base_url" label="公网访问地址（可选）"
                  extra="用于在钉钉 AI Card 中生成“在 AI 中枢打开”按钮；用户登录后才能下载 Artifact，例如 https://your-domain.com">
                  <Input placeholder="https://your-domain.com" />
                </Form.Item>
                <Form.Item name="reconnect_seconds" label="断线重连间隔">
                  <InputNumber min={2} max={60} suffix="秒" style={{ width: '100%' }} />
                </Form.Item>
              </>
            ) : null}
          </Form.Item>
          <Divider titlePlacement="start" plain>主动通知 Webhook（可选）</Divider>
          <Form.Item name="access_token" label="Access Token"
            extra={editingBot ? '留空则不修改' : '自定义机器人 Webhook URL 中的 access_token 参数'}>
            <Input.Password placeholder="输入 Access Token" />
          </Form.Item>
          <Form.Item name="secret" label="签名密钥 (Secret)"
            extra="安全设置中的加签密钥，以 SEC 开头">
            <Input.Password placeholder="SECxxxxxxxx（可选）" />
          </Form.Item>
          <Form.Item name="keyword" label="关键词"
            extra="安全设置中的自定义关键词，消息中必须包含此关键词">
            <Input placeholder="如：安全资讯、告警（可选）" />
          </Form.Item>
          <Form.Item name="outgoing_app_secret" label="旧回调 App Secret（可选）"
            extra="仅兼容 /api/v1/dingtalk/callback 回调模式；使用 Stream Mode 时无需填写">
            <Input.Password placeholder="旧 Outgoing 回调验签密钥" />
          </Form.Item>
        </Form>
      </Modal>
    </div>
  )
}
