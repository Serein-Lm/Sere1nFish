import {
  ApiOutlined,
  BranchesOutlined,
  CheckCircleOutlined,
  CloudServerOutlined,
  CodeOutlined,
  DatabaseOutlined,
  FileSearchOutlined,
  FileTextOutlined,
  GlobalOutlined,
  MessageOutlined,
  RobotOutlined,
  RocketOutlined,
  SearchOutlined,
  SyncOutlined,
  ToolOutlined,
} from '@ant-design/icons'
import type { ThoughtChainItemType } from '@ant-design/x'
import { ThoughtChain } from '@ant-design/x'
import XMarkdown from '@ant-design/x-markdown'
import React from 'react'
import { API_CONFIG, API_ENDPOINTS, REQUEST_HEADERS } from '../config/api'
import { redirectToLogin } from '../utils/authNavigation'
import { apiFetch, clearToken, getToken } from './http'

// ============================================
// SSE 协议 v2 类型定义
// ============================================

/**
 * SSE 事件类型
 */
export type EventType = 'start' | 'update' | 'content' | 'final' | 'end' | 'error' | 'ping'

/**
 * 节点类型
 */
export type NodeType = 'graph' | 'phase' | 'agent' | 'tool' | 'subgraph' | 'node'

/**
 * 节点状态
 */
export type NodeStatus = 'pending' | 'running' | 'success' | 'error' | 'abort'

/**
 * SSE 事件数据
 */
export interface EventData {
  type?: NodeType
  name?: string
  displayName?: string
  icon?: string
  description?: string
  status?: NodeStatus
  content?: string
  error?: string
  meta?: Record<string, unknown>
  duration?: number
  section?: string  // 用于 final 事件，标识输出段落
}

/**
 * SSE 事件结构（协议 v2）
 */
export interface SSEEvent {
  event: EventType
  id: string
  path: string
  ts: number
  data: EventData
}

// ============================================
// 执行状态管理
// ============================================

/**
 * 执行节点
 */
export interface ExecutionNode {
  id: string
  path: string
  parentPath: string | null
  childPaths: string[]
  
  type: NodeType
  name: string
  displayName: string
  icon?: string
  description?: string
  status: NodeStatus
  
  content: string
  startTime: number
  endTime?: number
  duration?: number
  meta?: Record<string, unknown>
}

/**
 * 最终输出段落
 */
export interface FinalSection {
  section: string
  title?: string
  content: string
}

/**
 * 执行状态
 */
export interface ExecutionState {
  nodes: Map<string, ExecutionNode>
  rootPath: string
  activeNodes: Set<string>
  finalContent: string  // 最终输出内容（显示在底部）
  finalSections: FinalSection[]  // 分段输出（支持多段结果）
}

/**
 * 创建初始执行状态
 */
export function createExecutionState(): ExecutionState {
  return {
    nodes: new Map(),
    rootPath: '',
    activeNodes: new Set(),
    finalContent: '',
    finalSections: [],
  }
}

/**
 * 获取父路径
 */
function getParentPath(path: string): string | null {
  const lastDot = path.lastIndexOf('.')
  return lastDot > 0 ? path.substring(0, lastDot) : null
}

/**
 * 处理 SSE 事件，更新状态
 */
export function handleSSEEvent(state: ExecutionState, event: SSEEvent): void {
  const { event: eventType, path, data, ts } = event

  switch (eventType) {
    case 'start': {
      const parentPath = getParentPath(path)
      const node: ExecutionNode = {
        id: event.id,
        path,
        parentPath,
        childPaths: [],
        type: data.type || 'phase',
        name: data.name || '',
        displayName: data.displayName || data.name || path,
        icon: data.icon,
        description: data.description,
        status: 'running',
        content: '',
        startTime: ts,
        meta: data.meta,
      }

      state.nodes.set(path, node)
      state.activeNodes.add(path)

      // 设置根路径
      if (!parentPath) {
        state.rootPath = path
      }

      // 更新父节点的 childPaths
      if (parentPath && state.nodes.has(parentPath)) {
        const parent = state.nodes.get(parentPath)!
        if (!parent.childPaths.includes(path)) {
          parent.childPaths.push(path)
        }
      }
      break
    }

    case 'content': {
      const node = state.nodes.get(path)
      if (node) {
        node.content += data.content || ''
      }
      break
    }

    case 'final': {
      // 累加最终输出内容
      state.finalContent += data.content || ''
      
      // 如果指定了 section，则分段存储
      if (data.section) {
        const sectionId = data.section
        let section = state.finalSections.find(s => s.section === sectionId)
        
        if (!section) {
          const sectionTitle = data.meta?.sectionTitle
          const createdSection: FinalSection = {
            section: sectionId,
            title: typeof sectionTitle === 'string' ? sectionTitle : undefined,
            content: '',
          }
          state.finalSections.push(createdSection)
          section = createdSection
        }
        
        section.content += data.content || ''
      }
      break
    }

    case 'update': {
      const node = state.nodes.get(path)
      if (node) {
        if (data.description) node.description = data.description
        if (data.status) node.status = data.status
        if (data.meta) node.meta = { ...node.meta, ...data.meta }
      }
      break
    }

    case 'end': {
      const node = state.nodes.get(path)
      if (node) {
        node.status = data.status || 'success'
        node.endTime = ts
        node.duration = data.duration
        if (data.meta) node.meta = { ...node.meta, ...data.meta }
      }
      state.activeNodes.delete(path)
      break
    }

    case 'error': {
      const node = state.nodes.get(path)
      if (node) {
        node.status = 'error'
        node.description = data.error
        node.endTime = ts
        if (data.meta) node.meta = { ...node.meta, ...data.meta }
      }
      state.activeNodes.delete(path)
      break
    }

    case 'ping':
      // 心跳事件，不需要处理
      break
  }
}

// ============================================
// 流式请求服务
// ============================================

/**
 * 流式请求参数
 */
export interface StreamRequest {
  workflow: string
  query: string
  delay?: number  // 模拟延迟（秒，mock 时使用）
  conversation_id?: string  // 传入则后端留存本轮对话
  options?: {
    enableCopywriting?: boolean
    selectedAgents?: string[]
    timeout?: number
    maxConcurrency?: number
    project_id?: string
    references?: Array<Record<string, unknown>>
    display_query?: string
  }
  context?: {
    conversationId?: string
    files?: Array<{
      id: string
      name: string
      type: 'image' | 'document'
      url: string
    }>
  }
}

/**
 * 流式响应回调
 */
export interface StreamCallbacks {
  onEvent?: (event: SSEEvent, state: ExecutionState) => void
  onStateChange?: (state: ExecutionState) => void
  onComplete?: (state: ExecutionState) => void
  onError?: (error: string, state: ExecutionState) => void
}

/**
 * Agent 流式服务（协议 v2）
 */
export class AgentStreamService {
  private baseURL: string

  constructor(baseURL: string = API_CONFIG.BASE_URL) {
    this.baseURL = baseURL
  }

  /**
   * 发送流式请求
   */
  async streamQuery(
    request: StreamRequest,
    callbacks: StreamCallbacks
  ): Promise<ExecutionState> {
    const state = createExecutionState()

    try {
      const headers = new Headers(REQUEST_HEADERS)
      const token = getToken()
      if (token) {
        headers.set('Authorization', `Bearer ${token}`)
      }

      const response = await fetch(`${this.baseURL}${API_ENDPOINTS.GRAPH_STREAM}`, {
        method: 'POST',
        headers,
        body: JSON.stringify(request),
      })

      if (response.status === 401) {
        clearToken()
        redirectToLogin()
        throw new Error('Unauthorized')
      }

      if (!response.ok) {
        throw new Error(`HTTP error! status: ${response.status}`)
      }

      const reader = response.body?.getReader()
      if (!reader) {
        throw new Error('无法获取响应流')
      }

      await this.processStream(reader, state, callbacks)
      callbacks.onComplete?.(state)
    } catch (error) {
      const errorMessage = error instanceof Error ? error.message : '未知错误'
      callbacks.onError?.(errorMessage, state)
      throw error
    }

    return state
  }

  /**
   * 处理 SSE 流
   */
  private async processStream(
    reader: ReadableStreamDefaultReader<Uint8Array>,
    state: ExecutionState,
    callbacks: StreamCallbacks
  ): Promise<void> {
    const decoder = new TextDecoder()
    let buffer = ''

    try {
      while (true) {
        const { done, value } = await reader.read()
        if (done) break

        buffer += decoder.decode(value, { stream: true })
        const lines = buffer.split('\n')
        buffer = lines.pop() || ''

        for (const line of lines) {
          if (line.startsWith('data: ')) {
            const data = line.slice(6).trim()
            if (!data) continue

            try {
              const event: SSEEvent = JSON.parse(data)
              handleSSEEvent(state, event)
              callbacks.onEvent?.(event, state)
              callbacks.onStateChange?.(state)
            } catch (e) {
              console.error('Parse error:', e, 'Line:', data)
            }
          }
        }
      }
    } finally {
      reader.releaseLock()
    }
  }
}

/**
 * 默认服务实例
 */
export const agentService = new AgentStreamService()

// ============================================
// AI 中枢对话留存（conversations）
// ============================================

export interface Conversation {
  conversation_id: string
  title: string
  owner?: string
  message_count: number
  last_message_at?: string | null
  created_at?: string
  updated_at?: string
}

export interface ConversationMessage {
  message_id: string
  conversation_id: string
  role: 'user' | 'assistant'
  content: string
  workflow?: string
  meta?: Record<string, unknown>
  created_at?: string
}

export interface Artifact {
  artifact_id: string
  kind: string
  title: string
  filename: string
  size: number
  content_type?: string
  download_url: string
  owner?: string
  meta?: {
    conversation_id?: string
    project_id?: string
    channel?: string
    sources?: Array<{ title?: string; url?: string; summary?: string }>
    references?: Array<Record<string, unknown>>
  }
  created_at?: string
  updated_at?: string
}

export type ArtifactFormatKey =
  | 'word'
  | 'markdown'
  | 'spreadsheet'
  | 'pdf'
  | 'data'
  | 'image'
  | 'audio'
  | 'video'
  | 'text'
  | 'file'

export interface ArtifactPresentation {
  key: ArtifactFormatKey
  label: string
  color: string
}

const ARTIFACT_KIND_PRESENTATION: Record<string, ArtifactPresentation> = {
  word: { key: 'word', label: 'Word', color: 'blue' },
  payload_word: { key: 'word', label: '载荷 Word', color: 'blue' },
  persona_word: { key: 'word', label: '人物 Word', color: 'blue' },
  markdown: { key: 'markdown', label: 'Markdown', color: 'purple' },
  text: { key: 'text', label: 'TXT', color: 'default' },
  json: { key: 'data', label: 'JSON', color: 'cyan' },
  csv: { key: 'spreadsheet', label: 'CSV', color: 'green' },
  excel: { key: 'spreadsheet', label: 'Excel', color: 'green' },
  spreadsheet: { key: 'spreadsheet', label: 'Excel', color: 'green' },
  pdf: { key: 'pdf', label: 'PDF', color: 'red' },
  image: { key: 'image', label: '图片', color: 'magenta' },
  audio: { key: 'audio', label: '音频', color: 'gold' },
  video: { key: 'video', label: '视频', color: 'volcano' },
}

const ARTIFACT_SUFFIX_PRESENTATION: Record<string, ArtifactPresentation> = {
  doc: ARTIFACT_KIND_PRESENTATION.word,
  docx: ARTIFACT_KIND_PRESENTATION.word,
  md: ARTIFACT_KIND_PRESENTATION.markdown,
  txt: ARTIFACT_KIND_PRESENTATION.text,
  json: ARTIFACT_KIND_PRESENTATION.json,
  csv: ARTIFACT_KIND_PRESENTATION.csv,
  xls: ARTIFACT_KIND_PRESENTATION.excel,
  xlsx: ARTIFACT_KIND_PRESENTATION.excel,
  pdf: ARTIFACT_KIND_PRESENTATION.pdf,
  png: ARTIFACT_KIND_PRESENTATION.image,
  jpg: ARTIFACT_KIND_PRESENTATION.image,
  jpeg: ARTIFACT_KIND_PRESENTATION.image,
  webp: ARTIFACT_KIND_PRESENTATION.image,
  gif: ARTIFACT_KIND_PRESENTATION.image,
  mp3: ARTIFACT_KIND_PRESENTATION.audio,
  wav: ARTIFACT_KIND_PRESENTATION.audio,
  m4a: ARTIFACT_KIND_PRESENTATION.audio,
  mp4: ARTIFACT_KIND_PRESENTATION.video,
  mov: ARTIFACT_KIND_PRESENTATION.video,
}

export function getArtifactPresentation(
  artifact: Pick<Artifact, 'kind' | 'filename'>,
): ArtifactPresentation {
  const kind = String(artifact.kind || '').toLowerCase()
  if (ARTIFACT_KIND_PRESENTATION[kind]) return ARTIFACT_KIND_PRESENTATION[kind]
  const suffix = String(artifact.filename || '').split('.').pop()?.toLowerCase() || ''
  return ARTIFACT_SUFFIX_PRESENTATION[suffix]
    || { key: 'file', label: suffix ? suffix.toUpperCase() : '文件', color: 'default' }
}

export function formatArtifactSize(size: number): string {
  if (!Number.isFinite(size) || size <= 0) return ''
  if (size < 1024) return `${size} B`
  if (size < 1024 * 1024) return `${(size / 1024).toFixed(1)} KB`
  return `${(size / (1024 * 1024)).toFixed(1)} MB`
}

export interface HubToolCatalog {
  agents: Array<{
    name: string
    prompt: string
    tools: string[]
    mcp_servers?: string[]
  }>
  tools: Array<{ name: string; description: string; kind: string; agents: string[] }>
  project_datasets: Array<{
    source: string
    label: string
    description: string
    filters: string[]
  }>
  mcp: Array<{ name: string; purpose: string; configured: boolean; agents: string[] }>
  audit: {
    query_interfaces: number
    registered_query_interfaces: number
    missing_query_interfaces: string[]
    project_dataset_interfaces: number
    target_filterable_datasets: number
    complete: boolean
  }
}

export const listConversations = (limit = 50) =>
  apiFetch<{ items: Conversation[]; total: number }>(`/v1/agent/conversations?limit=${limit}`)

export const createConversation = (title = '') =>
  apiFetch<Conversation>(`/v1/agent/conversations`, {
    method: 'POST',
    body: JSON.stringify({ title }),
  })

export const getConversation = (conversationId: string) =>
  apiFetch<{ conversation: Conversation; messages: ConversationMessage[] }>(
    `/v1/agent/conversations/${encodeURIComponent(conversationId)}`,
  )

export const renameConversation = (conversationId: string, title: string) =>
  apiFetch<Conversation>(`/v1/agent/conversations/${encodeURIComponent(conversationId)}`, {
    method: 'PUT',
    body: JSON.stringify({ title }),
  })

export const deleteConversation = (conversationId: string) =>
  apiFetch<{ ok: boolean }>(`/v1/agent/conversations/${encodeURIComponent(conversationId)}`, {
    method: 'DELETE',
  })

export const appendMessage = (
  conversationId: string,
  role: 'user' | 'assistant',
  content: string,
  workflow = '',
  meta: Record<string, unknown> = {},
) =>
  apiFetch<ConversationMessage>(
    `/v1/agent/conversations/${encodeURIComponent(conversationId)}/messages`,
    {
      method: 'POST',
      body: JSON.stringify({ role, content, workflow, meta }),
    },
  )

export const listArtifacts = (filters: {
  conversationId?: string
  projectId?: string
  kind?: string
  scope?: 'mine' | 'all'
  limit?: number
} = {}) => {
  const params = new URLSearchParams()
  if (filters.conversationId) params.set('conversation_id', filters.conversationId)
  if (filters.projectId) params.set('project_id', filters.projectId)
  if (filters.kind) params.set('kind', filters.kind)
  if (filters.scope) params.set('scope', filters.scope)
  params.set('limit', String(filters.limit || 50))
  return apiFetch<{ items: Artifact[]; total: number }>(`/v1/artifacts?${params.toString()}`)
}

export const getArtifact = (artifactId: string) =>
  apiFetch<Artifact>(`/v1/artifacts/${encodeURIComponent(artifactId)}`)

export const getHubToolCatalog = () => apiFetch<HubToolCatalog>('/v1/agent/tools')

// ============================================
// 可跳转引用解析（AI 输出内嵌 [[ref:type:id|label]]）
// ============================================

export type EntityRefType = 'person' | 'finding' | 'company' | 'project'

export interface EntityRef {
  type: EntityRefType
  id: string
  label: string
}

const REF_PATTERN = /\[\[ref:(person|finding|company|project):([^|\]]+)\|([^\]]*)\]\]/g

/** 从 AI 输出文本中提取可跳转引用，按 type+id 去重保序。 */
export function parseEntityRefs(text: string): EntityRef[] {
  if (!text) return []
  const seen = new Set<string>()
  const refs: EntityRef[] = []
  for (const m of text.matchAll(REF_PATTERN)) {
    const type = m[1] as EntityRefType
    const id = (m[2] || '').trim()
    const label = (m[3] || '').trim() || id
    const key = `${type}:${id}`
    if (id && !seen.has(key)) {
      seen.add(key)
      refs.push({ type, id, label })
    }
  }
  return refs
}

/** 移除文本中的原始引用标记，仅保留其可读 label，用于展示。 */
export function stripEntityRefs(text: string): string {
  if (!text) return ''
  return text.replace(REF_PATTERN, (_all, _type, _id, label) => (label || '').trim())
}

const ARTIFACT_REF_PATTERN = /\[\[artifact:(art_[A-Za-z0-9]+)\|([^\]]*)\]\]/g

export function parseArtifactRefs(text: string): Array<{ artifact_id: string; title: string }> {
  if (!text) return []
  const seen = new Set<string>()
  const refs: Array<{ artifact_id: string; title: string }> = []
  for (const match of text.matchAll(ARTIFACT_REF_PATTERN)) {
    const artifactId = (match[1] || '').trim()
    if (artifactId && !seen.has(artifactId)) {
      seen.add(artifactId)
      refs.push({ artifact_id: artifactId, title: (match[2] || '').trim() || artifactId })
    }
  }
  return refs
}

export function stripArtifactRefs(text: string): string {
  if (!text) return ''
  return text.replace(ARTIFACT_REF_PATTERN, '')
}

// ============================================
// 图标配置
// ============================================

/**
 * 节点类型对应的图标组件
 */
const NODE_TYPE_ICONS: Record<NodeType, React.ReactNode> = {
  graph: React.createElement(RocketOutlined, { style: { color: '#1890ff' } }),
  phase: React.createElement(BranchesOutlined, { style: { color: '#722ed1' } }),
  agent: React.createElement(RobotOutlined, { style: { color: '#13c2c2' } }),
  tool: React.createElement(ToolOutlined, { style: { color: '#fa8c16' } }),
  subgraph: React.createElement(ApiOutlined, { style: { color: '#eb2f96' } }),
  node: React.createElement(BranchesOutlined, { style: { color: '#52c41a' } }),
}

/**
 * 特定节点名称对应的图标（可扩展）
 */
const NODE_NAME_ICONS: Record<string, React.ReactNode> = {
  // Agents
  browser: React.createElement(GlobalOutlined, { style: { color: '#1890ff' } }),
  xhs: React.createElement(FileSearchOutlined, { style: { color: '#ff4d4f' } }),
  weixin: React.createElement(MessageOutlined, { style: { color: '#52c41a' } }),
  tianyancha: React.createElement(SearchOutlined, { style: { color: '#faad14' } }),
  bid: React.createElement(FileSearchOutlined, { style: { color: '#722ed1' } }),

  // AI 中枢专家子 Agent
  data: React.createElement(DatabaseOutlined, { style: { color: '#1890ff' } }),
  persona: React.createElement(RobotOutlined, { style: { color: '#eb2f96' } }),
  content: React.createElement(FileTextOutlined, { style: { color: '#13c2c2' } }),
  payload: React.createElement(GlobalOutlined, { style: { color: '#1677ff' } }),

  // Phases
  classify: React.createElement(SearchOutlined, { style: { color: '#1890ff' } }),
  synthesis: React.createElement(CheckCircleOutlined, { style: { color: '#52c41a' } }),
  synthesize: React.createElement(CheckCircleOutlined, { style: { color: '#52c41a' } }),
  
  // Tools
  search: React.createElement(SearchOutlined, { style: { color: '#fa8c16' } }),
  fetch: React.createElement(CloudServerOutlined, { style: { color: '#fa8c16' } }),
  execute: React.createElement(CodeOutlined, { style: { color: '#fa8c16' } }),
}

/**
 * 获取节点图标
 */
function getNodeIcon(node: ExecutionNode): React.ReactNode {
  // 优先使用后端指定的 icon
  if (node.icon && NODE_NAME_ICONS[node.icon]) {
    return NODE_NAME_ICONS[node.icon]
  }
  // 其次按节点名称匹配
  if (node.name && NODE_NAME_ICONS[node.name]) {
    return NODE_NAME_ICONS[node.name]
  }
  // 最后按类型匹配
  return NODE_TYPE_ICONS[node.type] || React.createElement(SyncOutlined, { spin: true })
}

/**
 * 状态映射到 ThoughtChain 状态
 */
function mapStatus(status: NodeStatus): 'loading' | 'success' | 'error' | 'abort' {
  switch (status) {
    case 'pending':
    case 'running':
      return 'loading'
    case 'success':
      return 'success'
    case 'error':
      return 'error'
    case 'abort':
      return 'abort'
    default:
      return 'loading'
  }
}

/**
 * 构建 ThoughtChain items（支持嵌套）
 */
export function buildThoughtChainItems(state: ExecutionState): ThoughtChainItemType[] {
  if (!state.rootPath || !state.nodes.has(state.rootPath)) {
    return []
  }

  // 递归构建节点及其子节点
  function buildNode(path: string): ThoughtChainItemType | null {
    const node = state.nodes.get(path)
    if (!node) return null

    const icon = getNodeIcon(node)

    // 递归构建子节点
    const childItems: ThoughtChainItemType[] = []
    for (const childPath of node.childPaths) {
      const childItem = buildNode(childPath)
      if (childItem) {
        childItems.push(childItem)
      }
    }

    // 构建 content
    let content: React.ReactNode = undefined
    
    // 如果有子节点，使用嵌套的 ThoughtChain 组件
    if (childItems.length > 0) {
      content = React.createElement(ThoughtChain, {
        items: childItems,
      })
    }
    // 如果有文本内容但没有子节点，使用 XMarkdown 渲染
    else if (node.content) {
      content = React.createElement(XMarkdown, {
        content: node.content,
      })
    }

    const item: ThoughtChainItemType = {
      key: path,
      title: node.displayName,
      icon,
      status: mapStatus(node.status),
      description: node.description || undefined,
      content,
      // 所有项都支持折叠
      collapsible: true,
    }

    return item
  }

  // 从根节点开始构建
  const root = state.nodes.get(state.rootPath)
  if (!root) return []

  // 返回根节点的子节点（graph 下的 phase/agent）
  return root.childPaths
    .map(buildNode)
    .filter((item): item is ThoughtChainItemType => item !== null)
}

/**
 * 构建扁平化的 ThoughtChain items（不嵌套，适合简单展示）
 */
export function buildFlatThoughtChainItems(state: ExecutionState): ThoughtChainItemType[] {
  const items: ThoughtChainItemType[] = []
  
  if (!state.rootPath || !state.nodes.has(state.rootPath)) {
    return items
  }

  // 递归遍历所有节点
  function traverse(path: string, depth: number = 0): void {
    const node = state.nodes.get(path)
    if (!node) return

    const icon = getNodeIcon(node)
    const indent = depth > 0 ? '  '.repeat(depth) : ''

    items.push({
      key: path,
      title: `${indent}${node.displayName}`,
      icon,
      status: mapStatus(node.status),
      description: node.description || undefined,
      content: node.content || undefined,
    })

    for (const childPath of node.childPaths) {
      traverse(childPath, depth + 1)
    }
  }

  traverse(state.rootPath)
  return items
}

/**
 * 获取当前活跃节点的内容（用于实时显示）
 */
export function getActiveContent(state: ExecutionState): string {
  let content = ''
  for (const path of state.activeNodes) {
    const node = state.nodes.get(path)
    if (node?.content) {
      content += node.content
    }
  }
  return content
}

/**
 * 获取最终汇总内容
 */
export function getSynthesisContent(state: ExecutionState): string {
  // 查找 synthesis 或最后一个 phase 节点的内容
  for (const [path, node] of state.nodes) {
    if (path.includes('synthesis') || (node.type === 'phase' && node.content)) {
      if (node.content) return node.content
    }
  }
  return ''
}
