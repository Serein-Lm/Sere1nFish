import { useState, useEffect, useRef } from 'react'
import { useNavigate, useSearchParams } from 'react-router-dom'
import { Bubble, Sender, Welcome, ThoughtChain, Prompts } from '@ant-design/x'
import type { BubbleListProps, SenderProps, PromptsProps } from '@ant-design/x'
import type { GetRef } from 'antd'
import XMarkdown from '@ant-design/x-markdown'
import { Flex, Space, Button, Divider, Dropdown, message, Popconfirm, Spin, Empty, Tooltip, Tag, Drawer, Badge, Collapse, Alert, Segmented, Input } from 'antd'
import type { MenuProps } from 'antd'
import { 
  RobotOutlined, 
  UserOutlined, 
  ShareAltOutlined, 
  ThunderboltOutlined,
  PaperClipOutlined,
  SearchOutlined,
  CodeOutlined,
  MailOutlined,
  GlobalOutlined,
  PhoneOutlined,
  AntDesignOutlined,
  ApiOutlined,
  ProfileOutlined,
  FileImageOutlined,
  FileTextOutlined,
  BulbOutlined,
  PlusOutlined,
  MessageOutlined,
  DeleteOutlined,
  FileWordOutlined,
  DatabaseOutlined,
  ProjectOutlined,
  MenuFoldOutlined,
  MenuUnfoldOutlined,
  InboxOutlined,
  LinkOutlined,
  HistoryOutlined,
} from '@ant-design/icons'
import { 
  agentService, 
  createExecutionState, 
  buildThoughtChainItems,
  parseEntityRefs,
  stripEntityRefs,
  parseArtifactRefs,
  stripArtifactRefs,
  listArtifacts,
  getArtifact,
  getHubToolCatalog,
  type EntityRef,
  type Artifact,
  type HubToolCatalog,
  type ExecutionState,
  type StreamRequest,
} from '../../services/agentService'
import {
  listConversations,
  createConversation,
  getConversation,
  deleteConversation,
  type Conversation,
} from '../../services/agentService'
import { downloadWithAuth } from '../../services/http'
import { getFindingDetail } from '../../services/taskService'
import DataReferencePicker, { type DataReference } from './DataReferencePicker'
import './PhishingPlatform.css'

const Switch = Sender.Switch
const isNarrowViewport = () => typeof window !== 'undefined'
  && window.matchMedia('(max-width: 768px)').matches
const AIHubInput = (props: React.ComponentProps<typeof Input.TextArea>) => (
  <Input.TextArea {...props} id="ai-hub-query" name="ai_hub_query" aria-label="AI 中枢问题" />
)

interface Message {
  key: string
  role: 'user' | 'assistant'
  content: string
  executionState?: ExecutionState
  status?: 'loading' | 'updating' | 'success'
  expandedKeys?: string[]  // 每条消息独立的展开状态
  artifacts?: Artifact[]
}

// Agent 配置信息
const AgentInfo: {
  [key: string]: {
    icon: React.ReactNode
    label: string
    skill: SenderProps['skill']
    slotConfig: SenderProps['slotConfig']
  }
} = {
  phishing_email: {
    icon: <MailOutlined />,
    label: '钓鱼邮件',
    skill: {
      value: 'phishingEmail',
      title: '钓鱼邮件生成',
      closable: true,
    },
    slotConfig: [
      { type: 'text', value: '请帮我生成一封针对' },
      {
        type: 'select',
        key: 'target_type',
        props: {
          options: ['技术人员', '财务人员', '管理层', 'HR部门'],
          placeholder: '请选择目标人群',
        },
      },
      { type: 'text', value: '的钓鱼邮件，主题是' },
      {
        type: 'input',
        key: 'email_topic',
        props: {
          placeholder: '请输入邮件主题',
          defaultValue: '系统升级通知',
        },
      },
      { type: 'text', value: '。' },
    ],
  },
  website_clone: {
    icon: <GlobalOutlined />,
    label: '网站克隆',
    skill: {
      value: 'websiteClone',
      title: '网站克隆助手',
      closable: true,
    },
    slotConfig: [
      { type: 'text', value: '请帮我克隆' },
      {
        type: 'select',
        key: 'site_type',
        props: {
          options: ['企业登录页', '邮箱登录页', 'VPN登录页', '云服务登录页'],
          placeholder: '请选择网站类型',
        },
      },
      { type: 'text', value: '，目标域名是' },
      {
        type: 'input',
        key: 'target_domain',
        props: {
          placeholder: '请输入目标域名',
          defaultValue: 'example.com',
        },
      },
      { type: 'text', value: '。' },
    ],
  },
  social_engineering: {
    icon: <PhoneOutlined />,
    label: '社工话术',
    skill: {
      value: 'socialEngineering',
      title: '社工话术助手',
      closable: true,
    },
    slotConfig: [
      { type: 'text', value: '请帮我设计一套针对' },
      {
        type: 'select',
        key: 'scenario',
        props: {
          options: ['电话钓鱼', '短信钓鱼', '即时通讯', '面对面社工'],
          placeholder: '请选择场景',
        },
      },
      { type: 'text', value: '的社工话术，目标是获取' },
      {
        type: 'select',
        key: 'target_info',
        props: {
          options: ['账号密码', '验证码', '内部信息', '物理访问权限'],
          placeholder: '请选择目标信息',
        },
      },
      { type: 'text', value: '。' },
    ],
  },
  deep_search: {
    icon: <SearchOutlined />,
    label: '深度搜索',
    skill: {
      value: 'deepSearch',
      title: '深度搜索',
      closable: true,
    },
    slotConfig: [
      { type: 'text', value: '请帮我搜索关于' },
      {
        type: 'input',
        key: 'search_keyword',
        props: {
          placeholder: '请输入搜索关键词',
        },
      },
      { type: 'text', value: '的' },
      {
        type: 'select',
        key: 'search_type',
        props: {
          options: ['漏洞信息', '泄露数据', '社交账号', '企业信息'],
          placeholder: '请选择搜索类型',
        },
      },
      { type: 'text', value: '。' },
    ],
  },
  ai_code: {
    icon: <CodeOutlined />,
    label: '代码生成',
    skill: {
      value: 'aiCode',
      title: '代码助手',
      closable: true,
    },
    slotConfig: [
      { type: 'text', value: '请使用' },
      {
        type: 'select',
        key: 'code_lang',
        props: {
          options: ['Python', 'JavaScript', 'PowerShell', 'Bash'],
          placeholder: '请选择编程语言',
        },
      },
      { type: 'text', value: '编写一个' },
      {
        type: 'input',
        key: 'code_desc',
        props: {
          placeholder: '请描述功能',
          defaultValue: '信息收集脚本',
        },
      },
      { type: 'text', value: '。' },
    ],
  },
}

// 文件引用配置
const FileInfo: {
  [key: string]: {
    icon: React.ReactNode
    label: string
  }
} = {
  file_image: {
    icon: <FileImageOutlined />,
    label: '图片文件',
  },
  file_doc: {
    icon: <FileTextOutlined />,
    label: '文档文件',
  },
}

const IconStyle = { fontSize: 16 }

// 从消息文本中提取产物（Word 等）下载链接
const ARTIFACT_LINK_RE = /\/api\/v1\/artifacts\/(art_[A-Za-z0-9]+)\/download/g
function extractArtifactLinks(text: string): Array<{ id: string; url: string }> {
  if (!text) return []
  const seen = new Set<string>()
  const links: Array<{ id: string; url: string }> = []
  let match: RegExpExecArray | null
  ARTIFACT_LINK_RE.lastIndex = 0
  while ((match = ARTIFACT_LINK_RE.exec(text)) !== null) {
    const id = match[1]
    if (!seen.has(id)) {
      seen.add(id)
      links.push({ id, url: match[0] })
    }
  }
  return links
}

const SwitchTextStyle = {
  display: 'inline-flex',
  width: 28,
  justifyContent: 'center',
  alignItems: 'center',
}

// 欢迎页 Prompts 配置
const welcomePrompts: PromptsProps['items'] = [
  {
    key: 'query',
    label: (
      <Space>
        <DatabaseOutlined style={{ color: '#1890FF' }} />
        <span>数据查询</span>
      </Space>
    ),
    description: '实时查库：项目 / 任务 / 发现',
    children: [
      {
        key: 'query-1',
        description: '当前平台有哪些项目？',
      },
      {
        key: 'query-2',
        description: '看看某个项目最近的任务日志有没有报错',
      },
      {
        key: 'query-3',
        description: '列出关注度最高的目标 finding',
      },
    ],
  },
  {
    key: 'analysis',
    label: (
      <Space>
        <SearchOutlined style={{ color: '#13C2C2' }} />
        <span>情报分析</span>
      </Space>
    ),
    description: '态势分析：看板 / 资产 / 对比',
    children: [
      {
        key: 'analysis-1',
        description: '给我某个项目的综合态势看板',
      },
      {
        key: 'analysis-2',
        description: '对比多个项目的发现数量和进展',
      },
      {
        key: 'analysis-3',
        description: '列出某项目的网络资产测绘结果',
      },
    ],
  },
  {
    key: 'persona',
    label: (
      <Space>
        <BulbOutlined style={{ color: '#FAAD14' }} />
        <span>人设与话术</span>
      </Space>
    ),
    description: '人设库 / 联系人 / 社工话术',
    children: [
      {
        key: 'persona-1',
        description: '在人设库里搜索某个目标人物的背景',
      },
      {
        key: 'persona-2',
        description: '手机上采集了哪些联系人画像？',
      },
      {
        key: 'persona-3',
        description: '基于某人物背景生成一套社工话术',
      },
    ],
  },
  {
    key: 'artifact',
    label: (
      <Space>
        <FileWordOutlined style={{ color: '#722ED1' }} />
        <span>产物导出</span>
      </Space>
    ),
    description: '一键导出 Word 报告 / 背景资料',
    children: [
      {
        key: 'artifact-1',
        description: '把某个人物的背景整理成 Word 文档给我下载',
      },
      {
        key: 'artifact-2',
        description: '生成一份项目态势总结报告的 Word',
      },
      {
        key: 'artifact-3',
        description: '导出某 finding 的话术包为 Word 文档',
      },
    ],
  },
]

export default function PhishingPlatform() {
  const navigate = useNavigate()
  const [searchParams, setSearchParams] = useSearchParams()
  const [messages, setMessages] = useState<Message[]>([])
  const [isRequesting, setIsRequesting] = useState(false)
  const [deepThink, setDeepThink] = useState(true)
  const [activeAgentKey, setActiveAgentKey] = useState<string | null>(null)
  const [slotConfig, setSlotConfig] = useState<typeof AgentInfo[string] | null>(null)
  const [conversations, setConversations] = useState<Conversation[]>([])
  const [activeConversationId, setActiveConversationId] = useState<string | null>(null)
  const [convLoading, setConvLoading] = useState(false)
  const [pickerOpen, setPickerOpen] = useState(false)
  const [dataRefs, setDataRefs] = useState<DataReference[]>([])
  const [artifactRefs, setArtifactRefs] = useState<Artifact[]>([])
  const [artifacts, setArtifacts] = useState<Artifact[]>([])
  const [artifactsLoading, setArtifactsLoading] = useState(false)
  const [artifactsOpen, setArtifactsOpen] = useState(false)
  const [artifactScope, setArtifactScope] = useState<'conversation' | 'all'>('conversation')
  const [capabilitiesOpen, setCapabilitiesOpen] = useState(false)
  const [toolCatalog, setToolCatalog] = useState<HubToolCatalog | null>(null)
  const [toolCatalogLoading, setToolCatalogLoading] = useState(false)
  const [inputValue, setInputValue] = useState('')
  const [sidebarCollapsed, setSidebarCollapsed] = useState(() => isNarrowViewport())
  const messagesEndRef = useRef<HTMLDivElement>(null)
  const chatListRef = useRef<HTMLDivElement>(null)
  const scrollToTopRef = useRef(false)
  const senderRef = useRef<GetRef<typeof Sender>>(null)

  // 自动滚动：流式对话滚到底部；加载历史会话时置顶，便于看到最初的提问
  useEffect(() => {
    if (scrollToTopRef.current) {
      scrollToTopRef.current = false
      chatListRef.current?.scrollTo({ top: 0, behavior: 'auto' })
      return
    }
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [messages])

  // 从其它页面跳转并预引用（如人设库"带需求跳转中台"）
  useEffect(() => {
    const personId = searchParams.get('ref_person')
    const projectId = searchParams.get('ref_project')
    const findingId = searchParams.get('ref_finding')
    const label = searchParams.get('label') || ''
    const desc = searchParams.get('desc') || undefined
    if (!personId && !projectId && !findingId) return
    const ref: DataReference | null = personId
      ? { type: 'person', id: personId, label: label || personId, desc }
      : projectId
      ? { type: 'project', id: projectId, label: label || projectId, desc }
      : findingId
      ? { type: 'finding', id: findingId, label: label || findingId, desc }
      : null
    if (ref) {
      setDataRefs(prev =>
        prev.some(r => r.type === ref.type && r.id === ref.id) ? prev : [...prev, ref],
      )
      message.success(`已引用「${ref.label}」，请在下方补充你的需求`)
      // 在输入框预置可见的起草文案，让用户明确引用已生效并继续输入
      setInputValue(prev =>
        prev.trim() ? prev : `请基于已引用的「${ref.label}」，`,
      )
      // 聚焦输入框，提示用户直接说出诉求
      setTimeout(() => senderRef.current?.focus?.(), 0)
    }
    // 清理 URL 参数，避免刷新重复引用
    const next = new URLSearchParams(searchParams)
    next.delete('ref_person')
    next.delete('ref_project')
    next.delete('ref_finding')
    next.delete('label')
    next.delete('desc')
    setSearchParams(next, { replace: true })
  }, [searchParams, setSearchParams])

  // 钉钉 AI Card 等外部入口：登录后把指定产物加入引用并打开产物抽屉。
  useEffect(() => {
    const artifactId = searchParams.get('ref_artifact')
    if (!artifactId) return
    let cancelled = false
    getArtifact(artifactId)
      .then(artifact => {
        if (cancelled) return
        setArtifactRefs(prev => prev.some(item => item.artifact_id === artifact.artifact_id)
          ? prev
          : [...prev, artifact])
        setArtifactScope('all')
        setArtifactsOpen(true)
        loadArtifactList(undefined, 'all')
      })
      .catch(error => {
        if (!cancelled) message.error(`打开产物失败：${error instanceof Error ? error.message : '无权访问'}`)
      })
      .finally(() => {
        if (cancelled) return
        const next = new URLSearchParams(searchParams)
        next.delete('ref_artifact')
        setSearchParams(next, { replace: true })
      })
    return () => { cancelled = true }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [searchParams, setSearchParams])

  // 加载会话列表
  const loadConversations = async () => {
    setConvLoading(true)
    try {
      const res = await listConversations()
      setConversations(res.items)
    } catch (error) {
      console.error('加载会话列表失败:', error)
    } finally {
      setConvLoading(false)
    }
  }

  const loadArtifactList = async (
    conversationId?: string | null,
    scope: 'conversation' | 'all' = artifactScope,
  ) => {
    setArtifactsLoading(true)
    try {
      const res = await listArtifacts({
        conversationId: scope === 'conversation' ? conversationId || undefined : undefined,
        scope: scope === 'all' ? 'all' : 'mine',
        limit: 100,
      })
      setArtifacts(res.items)
    } catch (error) {
      console.error('加载 AI 产物失败:', error)
    } finally {
      setArtifactsLoading(false)
    }
  }

  const openCapabilities = async () => {
    setCapabilitiesOpen(true)
    if (toolCatalog) return
    setToolCatalogLoading(true)
    try {
      setToolCatalog(await getHubToolCatalog())
    } catch (error) {
      console.error('加载 AI 工具目录失败:', error)
      message.error('加载 AI 工具目录失败')
    } finally {
      setToolCatalogLoading(false)
    }
  }

  useEffect(() => {
    loadConversations()
    loadArtifactList()
  }, [])

  useEffect(() => {
    const media = window.matchMedia('(max-width: 768px)')
    const handleViewportChange = (event: MediaQueryListEvent) => {
      if (event.matches) setSidebarCollapsed(true)
    }
    media.addEventListener('change', handleViewportChange)
    return () => media.removeEventListener('change', handleViewportChange)
  }, [])

  // 支持通过 ?conv=<id> 直达并打开指定会话（可分享会话链接）
  useEffect(() => {
    const cid = searchParams.get('conv')
    if (cid && cid !== activeConversationId) {
      selectConversation(cid)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [searchParams])

  // 确保存在一个会话（首次发送时创建）
  const ensureConversation = async (): Promise<string> => {
    if (activeConversationId) return activeConversationId
    try {
      const conv = await createConversation('')
      setActiveConversationId(conv.conversation_id)
      setConversations(prev => [conv, ...prev])
      return conv.conversation_id
    } catch (error) {
      console.error('创建会话失败:', error)
      return ''
    }
  }

  // 新建会话（清空当前，延迟到首次发送再落库）
  const handleNewConversation = () => {
    if (isRequesting) return
    if (isNarrowViewport()) setSidebarCollapsed(true)
    setActiveConversationId(null)
    setMessages([])
    setArtifacts([])
    setArtifactRefs([])
  }

  // 切换会话，加载历史消息
  const selectConversation = async (cid: string) => {
    if (isNarrowViewport()) setSidebarCollapsed(true)
    if (cid === activeConversationId || isRequesting) return
    setActiveConversationId(cid)
    try {
      const res = await getConversation(cid)
      scrollToTopRef.current = true
      const ordered = [...res.messages].sort((a, b) =>
        (a.created_at || '').localeCompare(b.created_at || ''),
      )
      setMessages(
        ordered.map(m => ({
          key: m.message_id,
          role: m.role,
          content: m.content,
          status: 'success' as const,
          artifacts: Array.isArray(m.meta?.artifacts) ? m.meta.artifacts as Artifact[] : [],
        })),
      )
      await loadArtifactList(cid)
    } catch (error) {
      console.error('加载会话失败:', error)
      message.error('加载会话失败')
    }
  }

  // 删除会话
  const handleDeleteConversation = async (cid: string) => {
    try {
      await deleteConversation(cid)
      setConversations(prev => prev.filter(c => c.conversation_id !== cid))
      if (cid === activeConversationId) {
        setActiveConversationId(null)
        setMessages([])
        setArtifacts([])
        setArtifactRefs([])
      }
    } catch (error) {
      console.error('删除会话失败:', error)
      message.error('删除会话失败')
    }
  }

  // 下载产物（Word 等），走鉴权下载
  const handleDownloadArtifact = async (url: string, filename = 'document.docx') => {
    try {
      await downloadWithAuth(url, filename)
    } catch (error) {
      console.error('下载产物失败:', error)
      message.error(`下载失败：${error instanceof Error ? error.message : '未知错误'}`)
    }
  }

  const handleReferenceArtifact = (artifact: Artifact) => {
    setArtifactRefs(prev => prev.some(item => item.artifact_id === artifact.artifact_id)
      ? prev
      : [...prev, artifact])
    setArtifactsOpen(false)
    message.success(`已引用产物：${artifact.title}`)
    setTimeout(() => senderRef.current?.focus?.(), 0)
  }

  // 可跳转引用点击：跳到对应实体的读取页，供中台快速读取信息
  const handleRefJump = async (ref: EntityRef) => {
    const q = encodeURIComponent(ref.id)
    if (ref.type === 'person') {
      navigate(`/persona-library?person_id=${q}`)
    } else if (ref.type === 'company') {
      navigate(`/persona-library?company=${q}`)
    } else if (ref.type === 'finding') {
      // finding 属于具体项目：跳到对应项目内的人物画像，而非全局人设库
      try {
        const detail = await getFindingDetail(ref.id)
        if (detail?.project_id) {
          navigate(`/projects/${encodeURIComponent(detail.project_id)}?finding_id=${q}`)
          return
        }
        message.warning('该发现缺少所属项目，无法定位人物画像')
      } catch {
        message.error('定位人物画像失败，请稍后重试')
      }
    } else if (ref.type === 'project') {
      navigate(`/projects/${q}`)
    }
  }

  // 引用平台数据（人物/项目）给 AI 中枢
  const handlePickReference = (ref: DataReference) => {
    setDataRefs(prev =>
      prev.some(r => r.type === ref.type && r.id === ref.id) ? prev : [...prev, ref],
    )
    message.success(`已引用：${ref.label}`)
  }

  const handleRemoveReference = (type: DataReference['type'], id: string) => {
    setDataRefs(prev => prev.filter(r => !(r.type === type && r.id === id)))
  }

  // 把已引用的数据实体转成给 agent 的指令前缀：只声明"读什么"，不固定编排
  const buildReferencePreamble = (refs: DataReference[]): string => {
    if (!refs.length) return ''
    const persons = refs.filter(r => r.type === 'person')
    const projects = refs.filter(r => r.type === 'project')
    const findings = refs.filter(r => r.type === 'finding')
    const lines: string[] = ['【引用数据】用户在中台引用了以下平台数据，请用你的工具自主读取后再完成任务：']
    persons.forEach(r => {
      lines.push(`- 人物画像：${r.label}（person_id=${r.id}）${r.desc ? `，${r.desc}` : ''}`)
    })
    projects.forEach(r => {
      lines.push(`- 项目数据：${r.label}（project_id=${r.id}）${r.desc ? `，${r.desc}` : ''}`)
    })
    findings.forEach(r => {
      lines.push(`- 发现节点：${r.label}（finding_id=${r.id}）${r.desc ? `，${r.desc}` : ''}`)
    })
    if (persons.length) {
      lines.push('可用 get_entity_context / get_persona 拉取人物完整背景，并结合人设库检索更合适的信息综合判断。')
    }
    if (projects.length) {
      lines.push('可用 get_project_dashboard / query_findings 分析该项目数据、定位高价值目标。')
    }
    if (findings.length) {
      lines.push('可用 query_findings / get_entity_context 读取该发现节点的攻击面与关联情报。')
    }
    lines.push('请基于以上引用数据满足下面的需求；如需产出可下载文件，调用相应 Word 工具并返回下载链接。')
    return lines.join('\n')
  }

  const buildArtifactPreamble = (refs: Artifact[]): string => {
    if (!refs.length) return ''
    return [
      '【引用产物】用户引用了以下历史 AI 产物，请先调用 get_artifact_content 读取正文与来源：',
      ...refs.map(item => `- ${item.title}（artifact_id=${item.artifact_id}，kind=${item.kind}）`),
      '基于这些产物完成新需求；需要更新交付物时生成新的 Artifact，不要覆盖历史文件。',
    ].join('\n')
  }

  // 快捷提示 - 已移除

  // Agent 菜单项
  const agentItems: MenuProps['items'] = Object.keys(AgentInfo).map((agent) => {
    const { icon, label } = AgentInfo[agent]
    return { key: agent, icon, label }
  })

  // 文件菜单项
  const fileItems: MenuProps['items'] = Object.keys(FileInfo).map((file) => {
    const { icon, label } = FileInfo[file]
    return { key: file, icon, label }
  })

  // Agent 选择点击
  const agentItemClick: MenuProps['onClick'] = (item) => {
    setActiveAgentKey(item.key)
    try {
      setSlotConfig(JSON.parse(JSON.stringify(AgentInfo[item.key])))
    } catch (error) {
      console.error(error)
    }
  }

  // 文件引用点击
  const fileItemClick: MenuProps['onClick'] = (item) => {
    const { icon, label } = FileInfo[item.key]
    senderRef.current?.insert?.([
      {
        type: 'tag',
        key: `${item.key}_${Date.now()}`,
        props: {
          label: (
            <Flex gap="small">
              {icon}
              {label}
            </Flex>
          ),
          value: item.key,
        },
      },
    ] as any)
  }


  // SSE 流式响应 - 使用协议 v2
  const streamResponse = async (
    userPrompt: string,
    messageKey: string,
    skill?: SenderProps['skill'],
    conversationId?: string,
    references: Array<Record<string, unknown>> = [],
    displayQuery?: string,
  ) => {
    const updateMessage = (updates: Partial<Message>) => {
      setMessages(prev => prev.map(msg =>
        msg.key === messageKey ? { ...msg, ...updates } : msg
      ))
    }

    // 前端技能标识 → 后端真实 workflow（assistant 为 Skill 驱动、携带全部工具的 AI 中枢）
    const SKILL_WORKFLOW: Record<string, string> = {
      phishingEmail: 'assistant',
      websiteClone: 'assistant',
      socialEngineering: 'assistant',
      aiCode: 'assistant',
      deepSearch: 'router',
    }
    const workflow = SKILL_WORKFLOW[(skill?.value as string) || ''] || 'assistant'

    const request: StreamRequest = {
      workflow,
      query: userPrompt,
      conversation_id: conversationId,
      options: { references, display_query: displayQuery },
    }

    try {
      await agentService.streamQuery(request, {
        onStateChange: (state) => {
          // 深拷贝 state 以触发 React 更新
          const clonedState: ExecutionState = {
            nodes: new Map(state.nodes),
            rootPath: state.rootPath,
            activeNodes: new Set(state.activeNodes),
            finalContent: state.finalContent,
            finalSections: [...state.finalSections],
          }
          
          // 更新状态和实时的最终内容
          updateMessage({ 
            executionState: clonedState,
            content: state.finalContent || '',
            status: 'updating' 
          })
        },

        onComplete: async (state) => {
          const clonedState: ExecutionState = {
            nodes: new Map(state.nodes),
            rootPath: state.rootPath,
            activeNodes: new Set(state.activeNodes),
            finalContent: state.finalContent,
            finalSections: [...state.finalSections],
          }
          
          updateMessage({
            executionState: clonedState,
            content: state.finalContent || '执行完成',
            status: 'success',
          })
          setIsRequesting(false)
          if (conversationId) {
            try {
              const artifactResult = await listArtifacts({ conversationId, limit: 100 })
              setArtifacts(artifactResult.items)
              updateMessage({ artifacts: artifactResult.items })
              await loadConversations()
            } catch (error) {
              console.error('刷新会话产物失败:', error)
            }
          }
        },

        onError: (error, state) => {
          console.error('SSE Error:', error)
          
          const clonedState: ExecutionState = {
            nodes: new Map(state.nodes),
            rootPath: state.rootPath,
            activeNodes: new Set(state.activeNodes),
            finalContent: state.finalContent,
            finalSections: [...state.finalSections],
          }
          
          updateMessage({
            executionState: clonedState,
            content: `❌ 错误: ${error}`,
            status: 'success',
          })
          setIsRequesting(false)
        },
      })
    } catch (error) {
      console.error('Stream error:', error)
      updateMessage({
        content: `❌ 连接失败：${error instanceof Error ? error.message : '未知错误'}\n\n请检查网络或登录状态后重试。`,
        status: 'success',
      })
      setIsRequesting(false)
    }
  }

  const handleSend = async (value: string, _?: any, skill?: SenderProps['skill']) => {
    if (!value.trim() || isRequesting) return

    const refsSnapshot = dataRefs
    const artifactRefsSnapshot = artifactRefs
    const visibleRefLabels = [
      ...refsSnapshot.map(r => r.label),
      ...artifactRefsSnapshot.map(r => r.title),
    ]
    const refLine = visibleRefLabels.length
      ? `\n\n> 已引用：${visibleRefLabels.join('、')}`
      : ''
    const userMessage: Message = {
      key: `user-${Date.now()}`,
      role: 'user',
      content: (skill?.value ? `[${skill.title}] ${value}` : value) + refLine,
      status: 'success',
    }

    const aiMessageKey = `ai-${Date.now()}`
    const aiMessage: Message = {
      key: aiMessageKey,
      role: 'assistant',
      content: '',
      executionState: createExecutionState(),
      status: 'loading',
    }

    setMessages(prev => [...prev, userMessage, aiMessage])
    setIsRequesting(true)
    
    // 清空输入
    setInputValue('')

    // 组装给 agent 的查询：引用数据前缀（只声明读什么，不固定编排）+ 用户需求
    const preamble = buildReferencePreamble(refsSnapshot)
    const artifactPreamble = buildArtifactPreamble(artifactRefsSnapshot)
    const contextPreamble = [preamble, artifactPreamble].filter(Boolean).join('\n\n')
    const agentQuery = contextPreamble ? `${contextPreamble}\n\n【需求】${value}` : value
    // 发送后清空已引用数据，避免带入下一轮
    setDataRefs([])
    setArtifactRefs([])

    // 后端流式入口原子留存用户消息、AI 回复和本轮 Artifact 关联
    const conversationId = await ensureConversation()
    const persistedReferences: Array<Record<string, unknown>> = [
      ...refsSnapshot.map(ref => ({ type: ref.type, id: ref.id, label: ref.label })),
      ...artifactRefsSnapshot.map(item => ({
        type: 'artifact',
        id: item.artifact_id,
        label: item.title,
      })),
    ]

    // 调用真实的 SSE 流式 API
    await streamResponse(
      agentQuery,
      aiMessageKey,
      skill,
      conversationId,
      persistedReferences,
      userMessage.content,
    )
  }

  const handleCancel = () => {
    setIsRequesting(false)
    message.warning('已取消请求')
  }


  // Bubble.List 角色配置
  const roles: BubbleListProps['role'] = {
    assistant: {
      placement: 'start',
      avatar: (
        <div className="avatar-icon assistant">
          <RobotOutlined />
        </div>
      ),
    },
    user: {
      placement: 'end',
      avatar: (
        <div className="avatar-icon user">
          <UserOutlined />
        </div>
      ),
    },
  }

  // 渲染消息列表项
  const renderMessageItems = () => {
    return messages.map(msg => {
      if (msg.role === 'assistant') {
        const items = msg.executionState ? buildThoughtChainItems(msg.executionState) : []
        
        // 获取所有可折叠项的 key
        const allKeys = items.map(item => item.key as string)

        // 提取产物（Word 等）下载链接
        const artifactText = [
          msg.content || '',
          ...(msg.executionState?.finalSections?.map(s => s.content) || []),
        ].join('\n')
        const fallbackLinks = extractArtifactLinks(artifactText)
        const markerRefs = parseArtifactRefs(artifactText)
        const structuredArtifacts = msg.artifacts || []
        const messageArtifacts: Artifact[] = [
          ...structuredArtifacts,
          ...markerRefs
            .filter(ref => !structuredArtifacts.some(item => item.artifact_id === ref.artifact_id))
            .map(ref => artifacts.find(item => item.artifact_id === ref.artifact_id) || ({
              artifact_id: ref.artifact_id,
              kind: 'word',
              title: ref.title,
              filename: `${ref.artifact_id}.docx`,
              size: 0,
              download_url: `/api/v1/artifacts/${ref.artifact_id}/download`,
            } as Artifact)),
          ...fallbackLinks
            .filter(link => !structuredArtifacts.some(item => item.artifact_id === link.id)
              && !markerRefs.some(ref => ref.artifact_id === link.id))
            .map(link => ({
              artifact_id: link.id,
              kind: 'word',
              title: 'Word 文档',
              filename: `${link.id}.docx`,
              size: 0,
              download_url: link.url,
            })),
        ]

        // 提取可跳转引用（person/finding/company），供中台快速跳转
        const entityRefs = parseEntityRefs(artifactText)
        
        // 当前消息的展开状态，默认全部展开（执行中）或全部折叠（完成后）
        const currentExpandedKeys = msg.expandedKeys ?? (msg.status === 'success' ? [] : allKeys)
        
        // 更新展开状态的处理函数
        const handleExpand = (keys: string[]) => {
          setMessages(prev => prev.map(m => 
            m.key === msg.key ? { ...m, expandedKeys: keys } : m
          ))
        }
        
        return {
          key: msg.key,
          role: msg.role,
          content: (
            <div className="assistant-message-wrapper">
              {/* ThoughtChain 思维链展示 */}
              {items.length > 0 && (
                <ThoughtChain
                  items={items}
                  line="dashed"
                  expandedKeys={currentExpandedKeys}
                  onExpand={handleExpand}
                  style={{ marginBottom: 16 }}
                />
              )}
              
              {/* 最终回复内容 - 支持分段显示 */}
              {msg.executionState?.finalSections && msg.executionState.finalSections.length > 0 ? (
                <Flex vertical gap={16}>
                  {msg.executionState.finalSections.map((section) => (
                    <div key={section.section} className="final-section-card">
                      {section.title && (
                        <div className="final-section-title">
                          {section.title}
                        </div>
                      )}
                      <XMarkdown content={stripArtifactRefs(stripEntityRefs(section.content))} />
                    </div>
                  ))}
                </Flex>
              ) : msg.content ? (
                <XMarkdown content={stripArtifactRefs(stripEntityRefs(msg.content))} />
              ) : (
                <div style={{ color: '#999' }}>等待回复...</div>
              )}

              {/* 产物下载入口（Word 等） */}
              {messageArtifacts.length > 0 && (
                <div className="message-artifact-list">
                  {messageArtifacts.map(artifact => (
                    <div key={artifact.artifact_id} className="message-artifact-item">
                      <FileWordOutlined />
                      <span className="message-artifact-title">{artifact.title}</span>
                      <Tooltip title="在新问题中引用">
                        <Button
                          type="text"
                          size="small"
                          icon={<LinkOutlined />}
                          onClick={() => handleReferenceArtifact(artifact)}
                        />
                      </Tooltip>
                      <Button
                        size="small"
                        icon={<FileWordOutlined />}
                        onClick={() => handleDownloadArtifact(artifact.download_url, artifact.filename)}
                      >
                        下载
                      </Button>
                    </div>
                  ))}
                </div>
              )}

              {/* 可跳转引用（人物 / 发现 / 公司），点击跳到读取页 */}
              {entityRefs.length > 0 && (
                <Flex gap={8} wrap="wrap" align="center" style={{ marginTop: 12 }}>
                  <span style={{ color: '#999', fontSize: 12 }}>相关跳转：</span>
                  {entityRefs.map(ref => (
                    <Tag
                      key={`${ref.type}:${ref.id}`}
                      color={ref.type === 'person' ? 'blue' : ref.type === 'company' ? 'green' : ref.type === 'project' ? 'purple' : 'gold'}
                      style={{ cursor: 'pointer', marginInlineEnd: 0 }}
                      icon={ref.type === 'person' ? <UserOutlined /> : ref.type === 'company' ? <GlobalOutlined /> : ref.type === 'project' ? <ProjectOutlined /> : <ProfileOutlined />}
                      onClick={() => handleRefJump(ref)}
                    >
                      {ref.label}
                    </Tag>
                  ))}
                </Flex>
              )}
            </div>
          ),
          loading: msg.status === 'loading',
        }
      }
      return {
        key: msg.key,
        role: msg.role,
        content: msg.content,
      }
    })
  }

  return (
    <div className="phishing-platform fade-in">
      <div className="phishing-layout">
        {!sidebarCollapsed && (
          <button
            type="button"
            className="conversation-sidebar-backdrop"
            aria-label="关闭对话历史"
            onClick={() => setSidebarCollapsed(true)}
          />
        )}
        <aside className={`conversation-sidebar${sidebarCollapsed ? ' collapsed' : ''}`}>
          {sidebarCollapsed ? (
            <div
              className="conversation-sidebar-rail"
              onClick={() => setSidebarCollapsed(false)}
              role="button"
              title="展开对话历史"
            >
              <Tooltip title="新建会话" placement="right">
                <Button
                  type="text"
                  size="small"
                  icon={<PlusOutlined />}
                  onClick={(e) => {
                    e.stopPropagation()
                    handleNewConversation()
                  }}
                  disabled={isRequesting}
                />
              </Tooltip>
              <div className="conversation-sidebar-rail-tab">
                <MenuUnfoldOutlined />
                <span className="conversation-sidebar-rail-text">对话历史</span>
              </div>
            </div>
          ) : (
            <>
              <div className="conversation-sidebar-header">
                <span className="conversation-sidebar-title">对话历史</span>
                <Space size={4}>
                  <Tooltip title="新建会话">
                    <Button
                      type="text"
                      size="small"
                      icon={<PlusOutlined />}
                      onClick={handleNewConversation}
                      disabled={isRequesting}
                    />
                  </Tooltip>
                  <Tooltip title="收起">
                    <Button
                      type="text"
                      size="small"
                      icon={<MenuFoldOutlined />}
                      onClick={() => setSidebarCollapsed(true)}
                    />
                  </Tooltip>
                </Space>
              </div>
              <div className="conversation-list">
                {convLoading ? (
                  <div className="conversation-loading"><Spin size="small" /></div>
                ) : conversations.length === 0 ? (
                  <Empty
                    image={Empty.PRESENTED_IMAGE_SIMPLE}
                    description="暂无会话"
                    style={{ marginTop: 40 }}
                  />
                ) : (
                  <div>
                    {conversations.map(conv => (
                      <div
                        key={conv.conversation_id}
                        className={`conversation-item${conv.conversation_id === activeConversationId ? ' active' : ''}`}
                        onClick={() => selectConversation(conv.conversation_id)}
                      >
                        <MessageOutlined className="conversation-item-icon" />
                        <span className="conversation-item-title">{conv.title || '新会话'}</span>
                        <Popconfirm
                          title="删除该会话？"
                          okText="删除"
                          cancelText="取消"
                          onConfirm={(e) => {
                            e?.stopPropagation()
                            handleDeleteConversation(conv.conversation_id)
                          }}
                          onCancel={(e) => e?.stopPropagation()}
                        >
                          <Button
                            type="text"
                            size="small"
                            className="conversation-item-delete"
                            icon={<DeleteOutlined />}
                            onClick={(e) => e.stopPropagation()}
                          />
                        </Popconfirm>
                      </div>
                    ))}
                  </div>
                )}
              </div>
            </>
          )}
        </aside>
        <div className="chat-container">
        <div className="chat-toolbar">
          <Tooltip title="对话历史">
            <Button
              className="mobile-history-button"
              size="small"
              icon={<HistoryOutlined />}
              onClick={() => setSidebarCollapsed(false)}
            />
          </Tooltip>
          <Space size={8}>
            <Tooltip title="查看 Agent、Prompt、工具和查询接口审计">
              <Button size="small" icon={<ApiOutlined />} onClick={openCapabilities}>
                能力目录
              </Button>
            </Tooltip>
            <Tooltip title={activeConversationId ? '查看当前会话产物' : '查看全部 AI 产物'}>
              <Badge count={artifacts.length} size="small" overflowCount={99}>
                <Button
                  size="small"
                  icon={<InboxOutlined />}
                  onClick={() => {
                    setArtifactsOpen(true)
                    const nextScope = activeConversationId ? artifactScope : 'all'
                    setArtifactScope(nextScope)
                    loadArtifactList(activeConversationId, nextScope)
                  }}
                >
                  AI 产物
                </Button>
              </Badge>
            </Tooltip>
          </Space>
        </div>
        <div className="chat-list" ref={chatListRef}>
          {messages.length === 0 ? (
            <Flex vertical className="welcome-container slide-up" gap={24} align="center" justify="center">
              <Welcome
                variant="borderless"
                icon="https://mdn.alipayobjects.com/huamei_iwk9zp/afts/img/A*s5sNRo5LjfQAAAAAAAAAAAAADgCCAQ/fmt.webp"
                title="AI 中枢"
                description="综合个人助手：实时查库、路由分发、生成建议与话术，并可一键导出 Word。输入需求即可，AI 会自动调用数据、人设、话术等专家并展示完整思维过程。"
                className="scale-in"
                extra={
                  <Space>
                    <Button icon={<ShareAltOutlined />} type="text" className="hover-float">分享</Button>
                  </Space>
                }
              />
              <Prompts
                title="✨ 快速开始"
                items={welcomePrompts}
                wrap
                fadeInLeft
                className="slide-up stagger-1"
                styles={{
                  list: { 
                    justifyContent: 'center', 
                    maxWidth: 800,
                    gap: 16,
                  },
                  item: {
                    flex: 'none',
                    width: 'calc(50% - 8px)',
                  },
                }}
                onItemClick={(info) => {
                  const description = info.data.description as string
                  if (description) {
                    handleSend(description)
                  }
                }}
              />
              {/* 快捷功能按钮 */}
              <Flex gap={12} wrap="wrap" justify="center" className="quick-actions slide-up stagger-2">
                <Button 
                  icon={<MailOutlined />} 
                  className="hover-float"
                  onClick={() => {
                    setActiveAgentKey('phishing_email')
                    setSlotConfig(JSON.parse(JSON.stringify(AgentInfo['phishing_email'])))
                  }}
                >
                  钓鱼邮件
                </Button>
                <Button 
                  icon={<GlobalOutlined />}
                  className="hover-float"
                  onClick={() => {
                    setActiveAgentKey('website_clone')
                    setSlotConfig(JSON.parse(JSON.stringify(AgentInfo['website_clone'])))
                  }}
                >
                  网站克隆
                </Button>
                <Button 
                  icon={<PhoneOutlined />}
                  className="hover-float"
                  onClick={() => {
                    setActiveAgentKey('social_engineering')
                    setSlotConfig(JSON.parse(JSON.stringify(AgentInfo['social_engineering'])))
                  }}
                >
                  社工话术
                </Button>
                <Button 
                  icon={<SearchOutlined />}
                  className="hover-float"
                  onClick={() => {
                    setActiveAgentKey('deep_search')
                    setSlotConfig(JSON.parse(JSON.stringify(AgentInfo['deep_search'])))
                  }}
                >
                  深度搜索
                </Button>
                <Button 
                  icon={<CodeOutlined />}
                  className="hover-float"
                  onClick={() => {
                    setActiveAgentKey('ai_code')
                    setSlotConfig(JSON.parse(JSON.stringify(AgentInfo['ai_code'])))
                  }}
                >
                  代码生成
                </Button>
              </Flex>
            </Flex>
          ) : (
            <>
              <Bubble.List
                items={renderMessageItems()}
                role={roles}
                autoScroll={false}
                style={{ flex: 1 }}
              />
              <div ref={messagesEndRef} />
            </>
          )}
        </div>

        <div className="sender-wrapper">
          {(dataRefs.length > 0 || artifactRefs.length > 0) && (
            <Flex gap={8} wrap="wrap" align="center" style={{ marginBottom: 8 }}>
              <span style={{ color: '#999', fontSize: 12 }}>已引用：</span>
              {dataRefs.map(ref => (
                <Tag
                  key={`${ref.type}:${ref.id}`}
                  color={ref.type === 'person' ? 'blue' : ref.type === 'finding' ? 'gold' : 'purple'}
                  icon={ref.type === 'person' ? <UserOutlined /> : <ProfileOutlined />}
                  closable
                  onClose={() => handleRemoveReference(ref.type, ref.id)}
                  style={{ marginInlineEnd: 0 }}
                >
                  {ref.label}
                </Tag>
              ))}
              {artifactRefs.map(artifact => (
                <Tag
                  key={`artifact:${artifact.artifact_id}`}
                  color="cyan"
                  icon={<FileWordOutlined />}
                  closable
                  onClose={() => setArtifactRefs(prev => prev.filter(
                    item => item.artifact_id !== artifact.artifact_id,
                  ))}
                  style={{ marginInlineEnd: 0 }}
                >
                  {artifact.title}
                </Tag>
              ))}
            </Flex>
          )}
          <Sender
            ref={senderRef}
            components={{ input: AIHubInput }}
            value={inputValue}
            onChange={setInputValue}
            loading={isRequesting}
            skill={slotConfig?.skill ? {
              ...slotConfig.skill,
              closable: {
                onClose: () => {
                  setSlotConfig(null)
                  setActiveAgentKey(null)
                  setInputValue('')
                }
              }
            } : undefined}
            slotConfig={slotConfig?.slotConfig}
            placeholder="输入你的需求，AI 将展示完整的思维过程..."
            autoSize={{ minRows: 3, maxRows: 6 }}
            className="chat-sender"
            suffix={false}
            footer={(actionNode) => (
              <Flex justify="space-between" align="center" className="sender-footer">
                <Flex gap="small" align="center">
                  <Button style={IconStyle} type="text" icon={<PaperClipOutlined />} />
                  <Switch
                    value={deepThink}
                    checkedChildren={
                      <>
                        深度思考：<span style={SwitchTextStyle}>开启</span>
                      </>
                    }
                    unCheckedChildren={
                      <>
                        深度思考：<span style={SwitchTextStyle}>关闭</span>
                      </>
                    }
                    onChange={(checked: boolean) => setDeepThink(checked)}
                    icon={<ThunderboltOutlined />}
                  />
                  <Dropdown
                    menu={{
                      selectedKeys: activeAgentKey ? [activeAgentKey] : [],
                      onClick: agentItemClick,
                      items: agentItems,
                    }}
                  >
                    <Switch value={false} icon={<AntDesignOutlined />}>
                      功能应用
                    </Switch>
                  </Dropdown>
                  {fileItems?.length ? (
                    <Dropdown menu={{ onClick: fileItemClick, items: fileItems }}>
                      <Switch value={false} icon={<ProfileOutlined />}>
                        文件引用
                      </Switch>
                    </Dropdown>
                  ) : null}
                  <Switch
                    value={dataRefs.length > 0}
                    icon={<DatabaseOutlined />}
                    onChange={() => setPickerOpen(true)}
                  >
                    {dataRefs.length > 0 ? `引用数据(${dataRefs.length})` : '引用数据'}
                  </Switch>
                </Flex>
                <Flex align="center">
                  <Tooltip title="能力目录">
                    <Button type="text" style={IconStyle} icon={<ApiOutlined />} onClick={openCapabilities} />
                  </Tooltip>
                  <Divider orientation="vertical" />
                  {actionNode}
                </Flex>
              </Flex>
            )}
            onSubmit={handleSend}
            onCancel={handleCancel}
          />
        </div>
        </div>
      </div>
      <DataReferencePicker
        open={pickerOpen}
        onClose={() => setPickerOpen(false)}
        onPick={handlePickReference}
        selectedIds={dataRefs.map(r => r.id)}
      />
      <Drawer
        rootClassName="ai-hub-drawer"
        title="AI 中枢能力目录"
        open={capabilitiesOpen}
        onClose={() => setCapabilitiesOpen(false)}
        size={520}
      >
        {toolCatalogLoading ? (
          <div className="artifact-drawer-loading"><Spin /></div>
        ) : toolCatalog ? (
          <Flex vertical gap={16}>
            <Alert
              type={toolCatalog.audit.complete ? 'success' : 'warning'}
              showIcon
              message={toolCatalog.audit.complete
                ? `${toolCatalog.audit.registered_query_interfaces} 个查询接口已全部录入`
                : `缺少 ${toolCatalog.audit.missing_query_interfaces.length} 个查询接口`}
              description={toolCatalog.audit.missing_query_interfaces.join('、') || '数据、人设、Artifact 查询接口均已分配给对应 Agent。'}
            />
            {toolCatalog.mcp.map(server => (
              <div className="capability-mcp-row" key={server.name}>
                <GlobalOutlined />
                <span>{server.name}</span>
                <Tag color={server.configured ? 'success' : 'error'}>
                  {server.configured ? '已配置' : '未配置'}
                </Tag>
                <span>{server.purpose}</span>
              </div>
            ))}
            <Collapse
              items={toolCatalog.agents.map(agent => ({
                key: agent.name,
                label: (
                  <Flex justify="space-between" align="center">
                    <span>{agent.name}</span>
                    <Tag>{agent.tools.length} 个工具</Tag>
                  </Flex>
                ),
                children: (
                  <Flex vertical gap={10}>
                    <div><Tag color="blue">Prompt</Tag>{agent.prompt}</div>
                    {agent.mcp_servers?.map(server => (
                      <Tag key={server} color="green">MCP: {server}</Tag>
                    ))}
                    <Flex gap={6} wrap="wrap">
                      {agent.tools.map(tool => <Tag key={tool}>{tool}</Tag>)}
                    </Flex>
                  </Flex>
                ),
              }))}
            />
          </Flex>
        ) : (
          <Empty description="能力目录加载失败" image={Empty.PRESENTED_IMAGE_SIMPLE} />
        )}
      </Drawer>
      <Drawer
        rootClassName="ai-hub-drawer"
        title="AI 产物"
        open={artifactsOpen}
        onClose={() => setArtifactsOpen(false)}
        size={420}
      >
        <Segmented
          block
          className="artifact-scope-switch"
          value={artifactScope}
          options={[
            { label: '当前会话', value: 'conversation', disabled: !activeConversationId },
            { label: '全部渠道', value: 'all' },
          ]}
          onChange={value => {
            const nextScope = value as 'conversation' | 'all'
            setArtifactScope(nextScope)
            loadArtifactList(activeConversationId, nextScope)
          }}
        />
        {artifactsLoading ? (
          <div className="artifact-drawer-loading"><Spin /></div>
        ) : artifacts.length === 0 ? (
          <Empty description="暂无 AI 产物" image={Empty.PRESENTED_IMAGE_SIMPLE} />
        ) : (
          <div className="artifact-drawer-list">
            {artifacts.map(artifact => (
              <div className="artifact-drawer-item" key={artifact.artifact_id}>
                <div className="artifact-file-icon"><FileWordOutlined /></div>
                <div className="artifact-drawer-body">
                  <div className="artifact-drawer-title">{artifact.title}</div>
                  <Space className="artifact-drawer-description" size={6} wrap>
                    <Tag color={artifact.kind === 'payload_word' ? 'blue' : 'default'}>
                      {artifact.kind === 'payload_word' ? '载荷 Word' : 'Word'}
                    </Tag>
                    <span>{artifact.filename}</span>
                    {artifact.meta?.sources?.length
                      ? <span>{artifact.meta.sources.length} 个公网来源</span>
                      : null}
                    {artifact.meta?.channel === 'dingtalk_stream' ? <Tag color="cyan">钉钉</Tag> : null}
                  </Space>
                </div>
                <Space size={4}>
                  <Tooltip title="在新问题中引用" key="reference">
                    <Button
                      type="text"
                      icon={<LinkOutlined />}
                      onClick={() => handleReferenceArtifact(artifact)}
                    />
                  </Tooltip>
                  <Tooltip title="下载 Word" key="download">
                    <Button
                      type="text"
                      icon={<FileWordOutlined />}
                      onClick={() => handleDownloadArtifact(artifact.download_url, artifact.filename)}
                    />
                  </Tooltip>
                </Space>
              </div>
            ))}
          </div>
        )}
      </Drawer>
    </div>
  )
}
