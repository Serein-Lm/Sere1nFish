import { useCallback, useState, useEffect, useMemo, useRef } from 'react'
import { useNavigate, useSearchParams } from 'react-router-dom'
import { ThoughtChain } from '@ant-design/x'
import { Conversation as AIConversation, ConversationContent, ConversationScrollButton } from '@/components/ai-elements/conversation'
import { Message, MessageContent, MessageResponse } from '@/components/ai-elements/message'
import {
  PromptInput,
  PromptInputBody,
  PromptInputButton,
  PromptInputFooter,
  PromptInputHeader,
  PromptInputSubmit,
  PromptInputTextarea,
  PromptInputTools,
} from '@/components/ai-elements/prompt-input'
import type { StickToBottomContext } from 'use-stick-to-bottom'
import { Flex, Space, Button, Dropdown, message, Spin, Empty, Tooltip, Tag, Drawer, Collapse, Alert, Segmented, Switch } from 'antd'
import type { MenuProps } from 'antd'
import {
  UserOutlined,
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
  DatabaseOutlined,
  ProjectOutlined,
  LinkOutlined,
  CopyOutlined,
  ReloadOutlined,
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
  getArtifactPresentation,
  formatArtifactSize,
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
  renameConversation,
  deleteConversation,
  type Conversation,
} from '../../services/agentService'
import { downloadWithAuth } from '../../services/http'
import { getFindingDetail } from '../../services/taskService'
import DataReferencePicker, { type DataReference } from './DataReferencePicker'
import SkillSelector from '../../components/SkillSelector'
import AIHubArtifactIcon from './components/AIHubArtifactIcon'
import AIHubConversationRail from './components/AIHubConversationRail'
import AIHubEmptyState from './components/AIHubEmptyState'
import AIHubWorkspaceHeader, {
  type AIHubLayoutMode,
} from './components/AIHubWorkspaceHeader'
import AIHubWorkspaceInspector from './components/AIHubWorkspaceInspector'
import './PhishingPlatform.css'

const isNarrowViewport = () => typeof window !== 'undefined'
  && window.matchMedia('(max-width: 768px)').matches

interface AgentSkill {
  value: string
  title: string
}

interface Message {
  key: string
  role: 'user' | 'assistant'
  content: string
  executionState?: ExecutionState
  status?: 'loading' | 'updating' | 'success'
  expandedKeys?: string[]  // 每条消息独立的展开状态
  artifacts?: Artifact[]
}

// Agent 配置信息（AI Elements Composer 模式：选中后以模板文案填入输入框）
const AgentInfo: {
  [key: string]: {
    icon: React.ReactNode
    label: string
    skill: AgentSkill
    template: string
  }
} = {
  phishing_email: {
    icon: <MailOutlined />,
    label: '钓鱼邮件',
    skill: {
      value: 'phishingEmail',
      title: '钓鱼邮件生成',
    },
    template: '请帮我生成一封针对「目标人群」的钓鱼邮件，主题是「邮件主题」。',
  },
  website_clone: {
    icon: <GlobalOutlined />,
    label: '网站克隆',
    skill: {
      value: 'websiteClone',
      title: '网站克隆助手',
    },
    template: '请帮我克隆「网站类型」，目标域名是「目标域名」。',
  },
  social_engineering: {
    icon: <PhoneOutlined />,
    label: '社工话术',
    skill: {
      value: 'socialEngineering',
      title: '社工话术助手',
    },
    template: '请帮我设计一套针对「场景」的社工话术，目标是获取「目标信息」。',
  },
  deep_search: {
    icon: <SearchOutlined />,
    label: '深度搜索',
    skill: {
      value: 'deepSearch',
      title: '深度搜索',
    },
    template: '请帮我搜索关于「关键词」的「搜索类型」。',
  },
  ai_code: {
    icon: <CodeOutlined />,
    label: '代码生成',
    skill: {
      value: 'aiCode',
      title: '代码助手',
    },
    template: '请使用「编程语言」编写一个「功能描述」。',
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

export default function PhishingPlatform() {
  const navigate = useNavigate()
  const [searchParams, setSearchParams] = useSearchParams()
  const [messages, setMessages] = useState<Message[]>([])
  const [isRequesting, setIsRequesting] = useState(false)
  const [autoExpandExecution, setAutoExpandExecution] = useState(false)
  const [activeAgentKey, setActiveAgentKey] = useState<string | null>(null)
  const [slotConfig, setSlotConfig] = useState<typeof AgentInfo[string] | null>(null)
  const [conversations, setConversations] = useState<Conversation[]>([])
  const [activeConversationId, setActiveConversationId] = useState<string | null>(null)
  const [convLoading, setConvLoading] = useState(false)
  const [pickerOpen, setPickerOpen] = useState(false)
  const [dataRefs, setDataRefs] = useState<DataReference[]>([])
  const [artifactRefs, setArtifactRefs] = useState<Artifact[]>([])
  const [artifacts, setArtifacts] = useState<Artifact[]>([])
  const [focusedArtifact, setFocusedArtifact] = useState<Artifact | null>(null)
  const [artifactsLoading, setArtifactsLoading] = useState(false)
  const [artifactsOpen, setArtifactsOpen] = useState(false)
  const [artifactScope, setArtifactScope] = useState<'conversation' | 'all'>('conversation')
  const [capabilitiesOpen, setCapabilitiesOpen] = useState(false)
  const [toolCatalog, setToolCatalog] = useState<HubToolCatalog | null>(null)
  const [toolCatalogLoading, setToolCatalogLoading] = useState(false)
  const [inputValue, setInputValue] = useState('')
  const [selectedSkillIds, setSelectedSkillIds] = useState<string[]>([])
  const [sidebarCollapsed, setSidebarCollapsed] = useState(() => isNarrowViewport())
  const [layoutMode, setLayoutMode] = useState<AIHubLayoutMode>('chat')
  const textareaRef = useRef<HTMLTextAreaElement>(null)
  // 停止生成：中断进行中的 SSE 请求
  const abortRef = useRef<AbortController | null>(null)
  // StickToBottom 上下文：发送后贴底、加载历史置顶
  const stickCtxRef = useRef<StickToBottomContext | null>(null)
  // 加载历史会话后需要置顶展示最旧消息
  const scrollToTopRef = useRef(false)
  // 重新生成：保存最近一轮请求参数
  const lastTurnRef = useRef<{
    query: string
    skill?: AgentSkill
    references: Array<Record<string, unknown>>
    displayQuery?: string
    selectedSkills: string[]
    conversationId: string
  } | null>(null)

  // 加载历史会话后从顶部开始阅读（StickToBottom 默认贴底，这里手动回滚）
  useEffect(() => {
    if (!scrollToTopRef.current) return
    scrollToTopRef.current = false
    const frame = requestAnimationFrame(() => {
      const el = stickCtxRef.current?.scrollRef.current
      el?.scrollTo({ top: 0, behavior: 'auto' })
    })
    return () => cancelAnimationFrame(frame)
  }, [messages])

  // 从其它页面跳转并预引用（如人设库"带需求跳转中台"）
  useEffect(() => {
    const personId = searchParams.get('ref_person')
    const personIntelId = searchParams.get('ref_person_intel')
    const projectId = searchParams.get('ref_project')
    const findingId = searchParams.get('ref_finding')
    const label = searchParams.get('label') || ''
    const desc = searchParams.get('desc') || undefined
    if (!personId && !personIntelId && !projectId && !findingId) return
    const ref: DataReference | null = personId
      ? { type: 'person', id: personId, label: label || personId, desc }
      : personIntelId
      ? { type: 'person_intel', id: personIntelId, label: label || personIntelId, desc }
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
      setTimeout(() => textareaRef.current?.focus(), 0)
    }
    // 清理 URL 参数，避免刷新重复引用
    const next = new URLSearchParams(searchParams)
    next.delete('ref_person')
    next.delete('ref_person_intel')
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
        setFocusedArtifact(artifact)
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
  const loadConversations = useCallback(async () => {
    setConvLoading(true)
    try {
      const res = await listConversations()
      setConversations(res.items)
    } catch (error) {
      console.error('加载会话列表失败:', error)
    } finally {
      setConvLoading(false)
    }
  }, [])

  const loadArtifactList = useCallback(async (
    conversationId?: string | null,
    scope: 'conversation' | 'all' = 'conversation',
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
  }, [])

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
  }, [loadArtifactList, loadConversations])

  useEffect(() => {
    const media = window.matchMedia('(max-width: 768px)')
    const handleViewportChange = (event: MediaQueryListEvent) => {
      if (event.matches) {
        setSidebarCollapsed(true)
        setLayoutMode('chat')
      }
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
      const latestSkillSelection = [...ordered].reverse().find(
        (item) => item.role === 'user' && Array.isArray(item.meta?.selected_skill_ids),
      )?.meta?.selected_skill_ids
      setSelectedSkillIds(
        Array.isArray(latestSkillSelection)
          ? latestSkillSelection.map(String).filter(Boolean)
          : [],
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
    setFocusedArtifact(null)
    message.success(`已引用产物：${artifact.title}`)
    setTimeout(() => textareaRef.current?.focus(), 0)
  }

  // 可跳转引用点击：跳到对应实体的读取页，供中台快速读取信息
  const handleRefJump = async (ref: EntityRef) => {
    const q = encodeURIComponent(ref.id)
    if (ref.type === 'person') {
      navigate(`/persona-library?person_id=${q}`)
    } else if (ref.type === 'person_intel') {
      navigate(`/person-intelligence?intel_id=${q}`)
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

  const selectAgent = (agentKey: string) => {
    const config = AgentInfo[agentKey]
    if (!config) return
    setActiveAgentKey(agentKey)
    setSlotConfig(config)
    // Composer 模式：把模板文案直接填入输入框，用户替换「占位」后发送
    setInputValue(config.template)
    setTimeout(() => textareaRef.current?.focus(), 0)
  }

  // Agent 选择点击
  const agentItemClick: MenuProps['onClick'] = (item) => selectAgent(item.key)

  // 文件引用点击：以标记文案追加进输入框（随提问一起发送）
  const fileItemClick: MenuProps['onClick'] = (item) => {
    const { label } = FileInfo[item.key]
    setInputValue(prev => (prev.trim() ? `${prev.trim()} ` : '') + `[${label}]`)
    setTimeout(() => textareaRef.current?.focus(), 0)
  }


  // SSE 流式响应 - 使用协议 v2（支持中途停止）
  const streamResponse = async (
    userPrompt: string,
    messageKey: string,
    skill?: AgentSkill,
    conversationId?: string,
    references: Array<Record<string, unknown>> = [],
    displayQuery?: string,
    selectedSkills: string[] = [],
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
      options: {
        references,
        display_query: displayQuery,
        selected_skill_ids: selectedSkills,
      },
    }

    // 完成或停止后统一刷新会话产物与列表
    const finalizeTurn = async () => {
      if (!conversationId) return
      try {
        const artifactResult = await listArtifacts({ conversationId, limit: 100 })
        setArtifacts(artifactResult.items)
        updateMessage({ artifacts: artifactResult.items })
        await loadConversations()
      } catch (error) {
        console.error('刷新会话产物失败:', error)
      }
    }

    const controller = new AbortController()
    abortRef.current = controller

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
          await finalizeTurn()
        },

        // 用户点击停止：保留已生成的部分内容
        onAbort: (state) => {
          const clonedState: ExecutionState = {
            nodes: new Map(state.nodes),
            rootPath: state.rootPath,
            activeNodes: new Set(state.activeNodes),
            finalContent: state.finalContent,
            finalSections: [...state.finalSections],
          }
          updateMessage({
            executionState: clonedState,
            content: state.finalContent
              ? `${state.finalContent}\n\n> 已停止生成，以上为部分结果。`
              : '已停止生成。',
            status: 'success',
          })
          setIsRequesting(false)
          void finalizeTurn()
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
      }, controller.signal)
    } catch (error) {
      console.error('Stream error:', error)
      updateMessage({
        content: `❌ 连接失败：${error instanceof Error ? error.message : '未知错误'}\n\n请检查网络或登录状态后重试。`,
        status: 'success',
      })
      setIsRequesting(false)
    } finally {
      if (abortRef.current === controller) {
        abortRef.current = null
      }
    }
  }

  const handleSend = async (value: string, skill?: AgentSkill) => {
    if (!value.trim() || isRequesting) return

    const refsSnapshot = dataRefs
    const artifactRefsSnapshot = artifactRefs
    const skillIdsSnapshot = [...selectedSkillIds]
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

    // 发送后清空已引用数据，避免带入下一轮
    setDataRefs([])
    setArtifactRefs([])

    // 发送新消息后贴底跟随新回复
    requestAnimationFrame(() => {
      void stickCtxRef.current?.scrollToBottom()
    })

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

    // 保存本轮参数，供「重新生成」使用
    lastTurnRef.current = {
      query: value,
      skill,
      references: persistedReferences,
      displayQuery: userMessage.content,
      selectedSkills: skillIdsSnapshot,
      conversationId,
    }

    // 调用真实的 SSE 流式 API
    await streamResponse(
      value,
      aiMessageKey,
      skill,
      conversationId,
      persistedReferences,
      userMessage.content,
      skillIdsSnapshot,
    )
  }

  // 停止生成：中断进行中的 SSE 流
  const handleCancel = () => {
    if (abortRef.current) {
      abortRef.current.abort()
      message.info('已停止生成')
      return
    }
    setIsRequesting(false)
    message.warning('已取消请求')
  }

  // 重新生成：替换最后一条 AI 回复，复用本轮请求参数
  const handleRegenerate = () => {
    const snapshot = lastTurnRef.current
    if (!snapshot?.conversationId || isRequesting) return
    const aiMessageKey = `ai-${Date.now()}`
    setMessages(prev => {
      let lastAssistantIdx = -1
      for (let i = prev.length - 1; i >= 0; i -= 1) {
        if (prev[i].role === 'assistant') {
          lastAssistantIdx = i
          break
        }
      }
      if (lastAssistantIdx < 0) return prev
      const next = prev.slice(0, lastAssistantIdx)
      next.push({
        key: aiMessageKey,
        role: 'assistant',
        content: '',
        executionState: createExecutionState(),
        status: 'loading',
      })
      return next
    })
    setIsRequesting(true)
    requestAnimationFrame(() => {
      void stickCtxRef.current?.scrollToBottom()
    })
    void streamResponse(
      snapshot.query,
      aiMessageKey,
      snapshot.skill,
      snapshot.conversationId,
      snapshot.references,
      snapshot.displayQuery,
      snapshot.selectedSkills,
    )
  }

  // 复制 AI 回复（去除引用标记后的纯 Markdown）
  const handleCopyMessage = async (msg: Message) => {
    const text = stripArtifactRefs(stripEntityRefs(msg.content || '')).trim()
    if (!text) return
    try {
      await navigator.clipboard.writeText(text)
      message.success('已复制到剪贴板')
    } catch {
      try {
        const textarea = document.createElement('textarea')
        textarea.value = text
        textarea.style.position = 'fixed'
        textarea.style.opacity = '0'
        document.body.appendChild(textarea)
        textarea.select()
        document.execCommand('copy')
        document.body.removeChild(textarea)
        message.success('已复制到剪贴板')
      } catch {
        message.error('复制失败，请手动选择复制')
      }
    }
  }

  // 重命名会话
  const handleRenameConversation = async (conversationId: string, title: string) => {
    const trimmed = title.trim()
    if (!trimmed) return
    try {
      await renameConversation(conversationId, trimmed)
      setConversations(prev => prev.map(c =>
        c.conversation_id === conversationId ? { ...c, title: trimmed } : c
      ))
      message.success('会话已重命名')
    } catch (error) {
      console.error('重命名会话失败:', error)
      message.error('重命名会话失败')
    }
  }

  const drawerArtifacts = focusedArtifact
    ? [focusedArtifact, ...artifacts.filter(
      artifact => artifact.artifact_id !== focusedArtifact.artifact_id,
    )]
    : artifacts
  const activeConversationTitle = useMemo(
    () => conversations.find(
      (conversation) => conversation.conversation_id === activeConversationId,
    )?.title || '新会话',
    [activeConversationId, conversations],
  )
  const latestExecutionState = useMemo(
    () => [...messages].reverse().find(
      (item) => item.role === 'assistant' && item.executionState,
    )?.executionState,
    [messages],
  )
  const referenceCount = dataRefs.length + artifactRefs.length

  const openArtifactDrawer = () => {
    setArtifactsOpen(true)
    const nextScope = activeConversationId ? artifactScope : 'all'
    setArtifactScope(nextScope)
    void loadArtifactList(activeConversationId, nextScope)
  }

  // AI Elements chatbot 模式：助手无头像纯文本流、用户右对齐小胶囊
  // 渲染消息列表项
  const lastAssistantKey = useMemo(
    () => [...messages].reverse().find(item => item.role === 'assistant')?.key,
    [messages],
  )

  const renderUserContent = (msg: Message) => {
    const text = msg.content || ''
    const refIdx = text.indexOf('\n\n> 已引用：')
    const main = refIdx >= 0 ? text.slice(0, refIdx) : text
    const refLabels = refIdx >= 0
      ? text.slice(refIdx + '\n\n> 已引用：'.length).split('、').filter(Boolean)
      : []
    return (
      <div className="ai-hub-user-content">
        <div className="ai-hub-user-text">{main}</div>
        {refLabels.length > 0 && (
          <div className="ai-hub-user-refs">
            <span className="ai-hub-user-refs-label">已引用</span>
            {refLabels.map(label => (
              <span key={label} className="ai-hub-user-ref-chip">{label}</span>
            ))}
          </div>
        )}
      </div>
    )
  }

  const renderMessages = () => {
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
              kind: 'file',
              title: ref.title,
              filename: ref.artifact_id,
              size: 0,
              content_type: 'application/octet-stream',
              download_url: `/api/v1/artifacts/${ref.artifact_id}/download`,
            } as Artifact)),
          ...fallbackLinks
            .filter(link => !structuredArtifacts.some(item => item.artifact_id === link.id)
              && !markerRefs.some(ref => ref.artifact_id === link.id))
            .map(link => ({
              artifact_id: link.id,
              kind: 'file',
              title: '文档产物',
              filename: link.id,
              size: 0,
              content_type: 'application/octet-stream',
              download_url: link.url,
            })),
        ]

        // 提取可跳转引用（person/finding/company），供中台快速跳转
        const entityRefs = parseEntityRefs(artifactText)

        // 当前消息的展开状态，默认全部展开（执行中）或全部折叠（完成后）
        const currentExpandedKeys = msg.expandedKeys
          ?? (autoExpandExecution && msg.status !== 'success' ? allKeys : [])

        // 更新展开状态的处理函数
        const handleExpand = (keys: string[]) => {
          setMessages(prev => prev.map(m =>
            m.key === msg.key ? { ...m, expandedKeys: keys } : m
          ))
        }

        return (
          <Message key={msg.key} from="assistant">
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

              {/* 最终回复内容 - 支持分段显示（MessageResponse：Streamdown 流式 Markdown） */}
              <MessageContent>
                {msg.executionState?.finalSections && msg.executionState.finalSections.length > 0 ? (
                  <Flex vertical gap={16}>
                    {msg.executionState.finalSections.map((section) => (
                      <div key={section.section} className="final-section-card">
                        {section.title && (
                          <div className="final-section-title">
                            {section.title}
                          </div>
                        )}
                        <MessageResponse>{stripArtifactRefs(stripEntityRefs(section.content))}</MessageResponse>
                      </div>
                    ))}
                  </Flex>
                ) : msg.content ? (
                  <MessageResponse>{stripArtifactRefs(stripEntityRefs(msg.content))}</MessageResponse>
                ) : (
                  <div className="ai-hub-typing" aria-label="正在生成回复">
                    <span className="ai-hub-typing-dot" />
                    <span className="ai-hub-typing-dot" />
                    <span className="ai-hub-typing-dot" />
                  </div>
                )}
              </MessageContent>

              {/* 消息操作：复制 / 重新生成（最后一条 AI 回复） */}
              {msg.status === 'success' && (msg.content || msg.executionState?.finalSections?.length) && (
                <Flex gap={4} className="ai-hub-msg-toolbar" align="center">
                  <Tooltip title="复制全文">
                    <Button
                      type="text"
                      size="small"
                      icon={<CopyOutlined />}
                      onClick={() => void handleCopyMessage(msg)}
                    />
                  </Tooltip>
                  {msg.key === lastAssistantKey && (
                    <Tooltip title="重新生成">
                      <Button
                        type="text"
                        size="small"
                        icon={<ReloadOutlined />}
                        disabled={isRequesting}
                        onClick={handleRegenerate}
                      />
                    </Tooltip>
                  )}
                </Flex>
              )}

              {/* 产物下载入口（Word 等） */}
              {messageArtifacts.length > 0 && (
                <div className="message-artifact-list">
                  {messageArtifacts.map(artifact => {
                    const presentation = getArtifactPresentation(artifact)
                    return (
                      <div key={artifact.artifact_id} className="message-artifact-item">
                        <AIHubArtifactIcon artifact={artifact} />
                        <span className="message-artifact-title">{artifact.title}</span>
                        <Tag color={presentation.color}>{presentation.label}</Tag>
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
                          icon={<AIHubArtifactIcon artifact={artifact} />}
                          onClick={() => handleDownloadArtifact(artifact.download_url, artifact.filename)}
                        >
                          下载
                        </Button>
                      </div>
                    )
                  })}
                </div>
              )}

              {/* 可跳转引用（人物 / 发现 / 公司），点击跳到读取页 */}
              {entityRefs.length > 0 && (
                <Flex gap={8} wrap="wrap" align="center" style={{ marginTop: 12 }}>
                  <span style={{ color: '#999', fontSize: 12 }}>相关跳转：</span>
                  {entityRefs.map(ref => (
                    <Tag
                      key={`${ref.type}:${ref.id}`}
                      color={ref.type === 'person' ? 'blue' : ref.type === 'person_intel' ? 'cyan' : ref.type === 'company' ? 'green' : ref.type === 'project' ? 'purple' : 'gold'}
                      style={{ cursor: 'pointer', marginInlineEnd: 0 }}
                      icon={ref.type === 'person' ? <UserOutlined /> : ref.type === 'person_intel' ? <GlobalOutlined /> : ref.type === 'company' ? <GlobalOutlined /> : ref.type === 'project' ? <ProjectOutlined /> : <ProfileOutlined />}
                      onClick={() => handleRefJump(ref)}
                    >
                      {ref.label}
                    </Tag>
                  ))}
                </Flex>
              )}
            </div>
          </Message>
        )
      }
      return (
        <Message key={msg.key} from="user">
          <MessageContent>{renderUserContent(msg)}</MessageContent>
        </Message>
      )
    })
  }

  return (
    <div className="phishing-platform fade-in">
      <div className="phishing-layout">
        <AIHubConversationRail
          conversations={conversations}
          activeConversationId={activeConversationId}
          loading={convLoading}
          collapsed={sidebarCollapsed}
          disabled={isRequesting}
          onCollapsedChange={setSidebarCollapsed}
          onNew={handleNewConversation}
          onSelect={(conversationId) => void selectConversation(conversationId)}
          onDelete={(conversationId) => void handleDeleteConversation(conversationId)}
          onRename={(conversationId, title) => void handleRenameConversation(conversationId, title)}
        />
        <div className="chat-container">
          <AIHubWorkspaceHeader
            title={activeConversationTitle}
            requesting={isRequesting}
            messageCount={messages.length}
            artifactCount={artifacts.length}
            referenceCount={referenceCount}
            skillCount={selectedSkillIds.length}
            layoutMode={layoutMode}
            onLayoutModeChange={setLayoutMode}
            onShowHistory={() => setSidebarCollapsed(false)}
            onShowCapabilities={() => void openCapabilities()}
            onShowArtifacts={openArtifactDrawer}
          />
          <div className={`ai-hub-workspace-grid mode-${layoutMode}`}>
            <section className="ai-hub-conversation-pane">
        {messages.length === 0 ? (
          <div className="chat-list">
            <AIHubEmptyState
              onPrompt={(prompt) => void handleSend(prompt)}
              onAgent={selectAgent}
            />
          </div>
        ) : (
          <AIConversation contextRef={stickCtxRef} className="chat-conversation">
            <ConversationContent className="chat-conversation-content">
              {renderMessages()}
            </ConversationContent>
            <ConversationScrollButton className="chat-scroll-button" />
          </AIConversation>
        )}

        <div className="sender-wrapper">
          <PromptInput
            className="chat-prompt-input"
            onSubmit={({ text }) => {
              void handleSend(text, slotConfig?.skill)
            }}
          >
            {(dataRefs.length > 0 || artifactRefs.length > 0 || slotConfig?.skill) && (
              <PromptInputHeader className="chat-prompt-header">
                {slotConfig?.skill && (
                  <Tag
                    color="blue"
                    closable
                    onClose={() => {
                      setSlotConfig(null)
                      setActiveAgentKey(null)
                    }}
                  >
                    {slotConfig.skill.title}
                  </Tag>
                )}
                {dataRefs.map(ref => (
                  <Tag
                    key={`${ref.type}:${ref.id}`}
                    color={ref.type === 'person' ? 'blue' : ref.type === 'person_intel' ? 'cyan' : ref.type === 'finding' ? 'gold' : 'purple'}
                    icon={ref.type === 'person' ? <UserOutlined /> : ref.type === 'person_intel' ? <GlobalOutlined /> : <ProfileOutlined />}
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
                    icon={<AIHubArtifactIcon artifact={artifact} />}
                    closable
                    onClose={() => setArtifactRefs(prev => prev.filter(
                      item => item.artifact_id !== artifact.artifact_id,
                    ))}
                    style={{ marginInlineEnd: 0 }}
                  >
                    {artifact.title}
                  </Tag>
                ))}
              </PromptInputHeader>
            )}
            <PromptInputBody>
              <PromptInputTextarea
                ref={textareaRef}
                value={inputValue}
                onChange={e => setInputValue(e.target.value)}
                placeholder="输入需求，Enter 发送"
              />
            </PromptInputBody>
            <PromptInputFooter>
              <PromptInputTools className="chat-prompt-tools">
                <Switch
                  size="small"
                  value={autoExpandExecution}
                  checkedChildren="展开过程：开"
                  unCheckedChildren="展开过程：关"
                  onChange={(checked: boolean) => setAutoExpandExecution(checked)}
                />
                <Dropdown
                  menu={{
                    selectedKeys: activeAgentKey ? [activeAgentKey] : [],
                    onClick: agentItemClick,
                    items: agentItems,
                  }}
                >
                  <PromptInputButton size="sm">
                    <AntDesignOutlined />
                    <span>功能应用</span>
                  </PromptInputButton>
                </Dropdown>
                {fileItems?.length ? (
                  <Dropdown menu={{ onClick: fileItemClick, items: fileItems }}>
                    <PromptInputButton size="sm">
                      <ProfileOutlined />
                      <span>文件引用</span>
                    </PromptInputButton>
                  </Dropdown>
                ) : null}
                <PromptInputButton size="sm" onClick={() => setPickerOpen(true)}>
                  <DatabaseOutlined />
                  <span>{dataRefs.length > 0 ? `引用数据(${dataRefs.length})` : '引用数据'}</span>
                </PromptInputButton>
                <SkillSelector
                  value={selectedSkillIds}
                  onChange={setSelectedSkillIds}
                  disabled={isRequesting}
                  className="ai-hub-skill-picker"
                  placeholder="Skills"
                />
              </PromptInputTools>
              <Flex align="center" gap={4}>
                <PromptInputButton
                  size="icon-sm"
                  tooltip="能力目录"
                  onClick={openCapabilities}
                >
                  <ApiOutlined />
                </PromptInputButton>
                <PromptInputSubmit
                  status={isRequesting ? 'streaming' : 'ready'}
                  onStop={handleCancel}
                  disabled={!inputValue.trim() && !isRequesting}
                />
              </Flex>
            </PromptInputFooter>
          </PromptInput>
        </div>
            </section>
            {layoutMode === 'split' && (
              <AIHubWorkspaceInspector
                executionState={latestExecutionState}
                artifacts={artifacts}
                artifactsLoading={artifactsLoading}
                onReloadArtifacts={() => void loadArtifactList(activeConversationId)}
                onReferenceArtifact={handleReferenceArtifact}
                onDownloadArtifact={(artifact) => void handleDownloadArtifact(
                  artifact.download_url,
                  artifact.filename,
                )}
              />
            )}
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
              title={toolCatalog.audit.complete
                ? `${toolCatalog.audit.registered_query_interfaces} 个查询工具、${toolCatalog.audit.project_dataset_interfaces} 个项目数据源已全部录入`
                : `缺少 ${toolCatalog.audit.missing_query_interfaces.length} 个查询接口`}
              description={toolCatalog.audit.missing_query_interfaces.join('、') || `${toolCatalog.audit.target_filterable_datasets} 个数据源支持按 Target 精确查询；所有数据源支持 offset 分页。`}
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
              items={[
                ...toolCatalog.agents.map(agent => ({
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
                })),
                {
                  key: 'project-datasets',
                  label: (
                    <Flex justify="space-between" align="center">
                      <span>项目数据查询接口</span>
                      <Tag>{toolCatalog.project_datasets.length} 个数据源</Tag>
                    </Flex>
                  ),
                  children: (
                    <Flex vertical gap={12}>
                      {toolCatalog.project_datasets.map(dataset => (
                        <div key={dataset.source}>
                          <Flex gap={6} wrap="wrap" align="center">
                            <strong>{dataset.label}</strong>
                            <Tag color="blue">{dataset.source}</Tag>
                            {dataset.filters.map(filter => (
                              <Tag key={filter}>{filter}</Tag>
                            ))}
                          </Flex>
                          <div style={{ color: 'var(--ant-color-text-secondary)', marginTop: 4 }}>
                            {dataset.description}
                          </div>
                        </div>
                      ))}
                    </Flex>
                  ),
                },
              ]}
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
        onClose={() => {
          setArtifactsOpen(false)
          setFocusedArtifact(null)
        }}
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
            setFocusedArtifact(null)
            setArtifactScope(nextScope)
            loadArtifactList(activeConversationId, nextScope)
          }}
        />
        {artifactsLoading ? (
          <div className="artifact-drawer-loading"><Spin /></div>
        ) : drawerArtifacts.length === 0 ? (
          <Empty description="暂无 AI 产物" image={Empty.PRESENTED_IMAGE_SIMPLE} />
        ) : (
          <div className="artifact-drawer-list">
            {drawerArtifacts.map(artifact => {
              const presentation = getArtifactPresentation(artifact)
              const size = formatArtifactSize(artifact.size)
              return (
                <div className="artifact-drawer-item" key={artifact.artifact_id}>
                  <div className="artifact-file-icon"><AIHubArtifactIcon artifact={artifact} /></div>
                  <div className="artifact-drawer-body">
                    <div className="artifact-drawer-title">{artifact.title}</div>
                    <Space className="artifact-drawer-description" size={6} wrap>
                      <Tag color={presentation.color}>{presentation.label}</Tag>
                      <span>{artifact.filename}</span>
                      {size ? <span>{size}</span> : null}
                      {artifact.meta?.sources?.length
                        ? <span>{artifact.meta.sources.length} 个公网来源</span>
                        : null}
                      {artifact.meta?.channel === 'dingtalk_stream' ? <Tag color="cyan">钉钉</Tag> : null}
                      {artifact.artifact_id === focusedArtifact?.artifact_id
                        ? <Tag color="blue">当前产物</Tag>
                        : null}
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
                    <Tooltip title={`下载 ${presentation.label}`} key="download">
                      <Button
                        type="text"
                        icon={<AIHubArtifactIcon artifact={artifact} />}
                        onClick={() => handleDownloadArtifact(artifact.download_url, artifact.filename)}
                      />
                    </Tooltip>
                  </Space>
                </div>
              )
            })}
          </div>
        )}
      </Drawer>
    </div>
  )
}
