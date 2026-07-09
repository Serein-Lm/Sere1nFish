import { apiFetch, getToken } from './http'

// ============ 类型定义 ============

/**
 * 抖音 Cookie 账号信息
 */
export interface DouyinCookieAccount {
  id: string
  account_name: string
  is_active: boolean
  is_valid: boolean | null
  last_verified_at?: string | null
  created_at: string
  updated_at?: string
}

/**
 * 抖音 Cookie 详情（含 cookie_string）
 */
export interface DouyinCookieDetail extends DouyinCookieAccount {
  cookie_string: string
}

/**
 * 创建 Cookie 请求
 */
export interface CreateDouyinCookieRequest {
  account_name: string
  cookie_string: string
}

/**
 * 更新 Cookie 请求
 */
export interface UpdateDouyinCookieRequest {
  cookie_string?: string
  is_active?: boolean
  new_account_name?: string
}

/**
 * 搜索结果
 */
export interface DouyinSearchResult {
  id: string
  project_id: string
  aweme_id: string
  keyword: string
  aweme_type?: string
  title: string
  create_time?: number
  create_time_str?: string
  ip_location?: string
  liked_count?: string
  collected_count?: string
  comment_count?: string
  share_count?: string
  user_id: string
  sec_uid: string
  nickname: string
  avatar?: string
  cover_url?: string
  video_download_url?: string
  aweme_url?: string
  user_profile_url?: string
  source_keyword?: string
  created_at?: string
  updated_at?: string
}

/**
 * 打标结果
 */
export interface DouyinTaggedResult {
  id: string
  project_id: string
  aweme_id: string
  keyword?: string
  title: string
  nickname: string
  sec_uid: string
  user_id: string
  user_profile_url?: string
  aweme_url?: string
  avatar?: string
  cover_url?: string
  ip_location?: string
  liked_count?: string
  collected_count?: string
  comment_count?: string
  share_count?: string
  create_time_str?: string
  tag: 'potential_employee' | 'marketing' | 'uncertain'
  tag_reason: string
  confidence: 'high' | 'medium' | 'low'
  key_evidence?: string[]
  company_mentioned?: string
  position_mentioned?: string
  priority: number
  created_at?: string
  updated_at?: string
}

/**
 * 用户画像
 */
export interface DouyinProfile {
  id: string
  project_id: string
  finding_id?: string
  sec_uid: string
  user_id: string
  nickname: string
  user_profile_url?: string
  sample_title?: string
  tag_reason?: string
  confidence?: 'high' | 'medium' | 'low'
  priority?: number
  vision_analysis?: string
  screenshot_paths?: string[]
  created_at?: string
  updated_at?: string
}

/**
 * 潜在用户
 */
export interface DouyinPotentialUser {
  sec_uid: string
  nickname: string
  user_profile_url?: string
  tag_reason?: string
  confidence?: 'high' | 'medium' | 'low'
  priority?: number
  aweme_count?: number
}

/**
 * 打标统计
 */
export interface DouyinTaggedStats {
  total: number
  potential_employee: number
  marketing: number
  uncertain: number
}

// ============ Cookie 管理 API ============

/**
 * 获取 Cookie 账号列表
 */
export async function listDouyinCookies(params?: { limit?: number; skip?: number }): Promise<DouyinCookieAccount[]> {
  const searchParams = new URLSearchParams()
  if (params?.limit) searchParams.append('limit', String(params.limit))
  if (params?.skip) searchParams.append('skip', String(params.skip))
  const queryString = searchParams.toString()
  
  return apiFetch<DouyinCookieAccount[]>(`/v1/douyin/cookies${queryString ? `?${queryString}` : ''}`, {
    method: 'GET',
  })
}

/**
 * 添加 Cookie 账号
 */
export async function createDouyinCookie(body: CreateDouyinCookieRequest): Promise<DouyinCookieAccount> {
  return apiFetch<DouyinCookieAccount>('/v1/douyin/cookies', {
    method: 'POST',
    body: JSON.stringify(body),
  })
}

/**
 * 激活 Cookie 账号
 */
export async function activateDouyinCookie(accountName: string): Promise<DouyinCookieAccount> {
  return apiFetch<DouyinCookieAccount>(
    `/v1/douyin/cookies/${encodeURIComponent(accountName)}/activate`,
    { method: 'POST' }
  )
}

/**
 * 删除 Cookie 账号
 */
export async function deleteDouyinCookie(accountName: string): Promise<{ ok: boolean }> {
  return apiFetch<{ ok: boolean }>(
    `/v1/douyin/cookies/${encodeURIComponent(accountName)}`,
    { method: 'DELETE' }
  )
}

/**
 * 获取 Cookie 账号基本信息
 */
export async function getDouyinCookie(accountName: string): Promise<DouyinCookieAccount> {
  return apiFetch<DouyinCookieAccount>(
    `/v1/douyin/cookies/${encodeURIComponent(accountName)}`,
    { method: 'GET' }
  )
}

/**
 * 获取 Cookie 账号详情（含 cookie_string）
 */
export async function getDouyinCookieDetail(accountName: string): Promise<DouyinCookieDetail> {
  return apiFetch<DouyinCookieDetail>(
    `/v1/douyin/cookies/${encodeURIComponent(accountName)}/detail`,
    { method: 'GET' }
  )
}

/**
 * 更新 Cookie 账号
 */
export async function updateDouyinCookie(
  accountName: string,
  body: UpdateDouyinCookieRequest
): Promise<DouyinCookieAccount> {
  return apiFetch<DouyinCookieAccount>(
    `/v1/douyin/cookies/${encodeURIComponent(accountName)}`,
    {
      method: 'PUT',
      body: JSON.stringify(body),
    }
  )
}

/**
 * 验证 Cookie 有效性
 */
export async function verifyDouyinCookie(accountName: string): Promise<DouyinCookieAccount> {
  return apiFetch<DouyinCookieAccount>(
    `/v1/douyin/cookies/${encodeURIComponent(accountName)}/verify`,
    { method: 'POST' }
  )
}

// ============ 搜索结果 API ============

/**
 * 获取搜索结果列表（POST 分页）
 */
export async function listDouyinSearchResults(
  projectId: string,
  params?: { keyword?: string; page?: number; page_size?: number }
): Promise<{ total: number; items: DouyinSearchResult[]; page: number; page_size: number }> {
  return apiFetch<{ total: number; items: DouyinSearchResult[]; page: number; page_size: number }>(
    `/v1/douyin/${encodeURIComponent(projectId)}/search-results`,
    {
      method: 'POST',
      body: JSON.stringify({
        project_id: projectId,
        page: params?.page ?? 1,
        page_size: params?.page_size ?? 50,
        keyword: params?.keyword ?? '',
      }),
    }
  )
}

/**
 * 获取单条搜索结果
 */
export async function getDouyinSearchResult(projectId: string, awemeId: string): Promise<DouyinSearchResult> {
  return apiFetch<DouyinSearchResult>(
    `/v1/douyin/${encodeURIComponent(projectId)}/search-results/${encodeURIComponent(awemeId)}`,
    { method: 'GET' }
  )
}

/**
 * 统计搜索结果
 */
export async function countDouyinSearchResults(
  projectId: string,
  keyword?: string
): Promise<{ total: number }> {
  const searchParams = new URLSearchParams()
  if (keyword) searchParams.append('keyword', keyword)
  const queryString = searchParams.toString()
  
  return apiFetch<{ total: number }>(
    `/v1/douyin/${encodeURIComponent(projectId)}/search-results/count${queryString ? `?${queryString}` : ''}`,
    { method: 'GET' }
  )
}

// ============ 打标结果 API ============

/**
 * 获取打标结果列表（POST 分页）
 */
export async function listDouyinTaggedResults(
  projectId: string,
  params?: { tag?: string; page?: number; page_size?: number }
): Promise<{ total: number; stats: DouyinTaggedStats; items: DouyinTaggedResult[]; page: number; page_size: number }> {
  return apiFetch<{ total: number; stats: DouyinTaggedStats; items: DouyinTaggedResult[]; page: number; page_size: number }>(
    `/v1/douyin/${encodeURIComponent(projectId)}/tagged-results`,
    {
      method: 'POST',
      body: JSON.stringify({
        project_id: projectId,
        page: params?.page ?? 1,
        page_size: params?.page_size ?? 50,
        tag: params?.tag ?? null,
      }),
    }
  )
}

/**
 * 获取潜在用户列表（去重）
 */
export async function listDouyinPotentialUsers(
  projectId: string
): Promise<{ total: number; users: DouyinPotentialUser[] }> {
  return apiFetch<{ total: number; users: DouyinPotentialUser[] }>(
    `/v1/douyin/${encodeURIComponent(projectId)}/potential-users`,
    { method: 'GET' }
  )
}

/**
 * 统计打标结果
 */
export async function getDouyinTaggedStats(projectId: string): Promise<DouyinTaggedStats> {
  return apiFetch<DouyinTaggedStats>(
    `/v1/douyin/${encodeURIComponent(projectId)}/tagged-results/stats`,
    { method: 'GET' }
  )
}

// ============ 用户画像 API ============

/**
 * 获取用户画像列表（POST 分页）
 */
export async function listDouyinProfiles(
  projectId: string,
  params?: { page?: number; page_size?: number }
): Promise<{ total: number; items: DouyinProfile[]; page: number; page_size: number }> {
  return apiFetch<{ total: number; items: DouyinProfile[]; page: number; page_size: number }>(
    `/v1/douyin/${encodeURIComponent(projectId)}/profiles`,
    {
      method: 'POST',
      body: JSON.stringify({
        project_id: projectId,
        page: params?.page ?? 1,
        page_size: params?.page_size ?? 50,
      }),
    }
  )
}

/**
 * 获取单个用户画像
 */
export async function getDouyinProfile(profileId: string): Promise<DouyinProfile> {
  return apiFetch<DouyinProfile>(
    `/v1/douyin/profiles/${encodeURIComponent(profileId)}`,
    { method: 'GET' }
  )
}

/**
 * 删除用户画像
 */
export async function deleteDouyinProfile(profileId: string): Promise<{ ok: boolean }> {
  return apiFetch<{ ok: boolean }>(
    `/v1/douyin/profiles/${encodeURIComponent(profileId)}`,
    { method: 'DELETE' }
  )
}

/**
 * 统计用户画像
 */
export async function countDouyinProfiles(projectId: string): Promise<{ total: number }> {
  return apiFetch<{ total: number }>(
    `/v1/douyin/${encodeURIComponent(projectId)}/profiles/count`,
    { method: 'GET' }
  )
}

// ============ 截图与视觉分析 SSE API ============

/**
 * 截图请求
 */
export interface DouyinScreenshotRequest {
  user_url: string
  max_screenshots?: number
}

/**
 * 视觉分析请求
 */
export interface DouyinVisionAnalysisRequest {
  user_url: string
  project_id: string
}

/**
 * SSE 消息类型
 */
export type DouyinSSEType = 'init' | 'status' | 'progress' | 'content' | 'result' | 'done' | 'cancelled' | 'error'

/**
 * SSE 消息
 */
export interface DouyinSSEMessage {
  type: DouyinSSEType
  task_id?: string
  stage?: 'screenshot' | 'vision'
  message?: string
  content?: string
  data?: unknown
}

/**
 * SSE 回调
 */
export interface DouyinSSECallbacks {
  onInit?: (taskId: string, stage: string) => void
  onStatus?: (message: string, stage?: string) => void
  onProgress?: (message: string) => void
  onContent?: (content: string, accumulated: string) => void
  onResult?: (data: unknown) => void
  onDone?: (message: string) => void
  onCancelled?: (message: string, stage?: string) => void
  onError?: (error: string) => void
}

/**
 * 流式截图用户主页
 */
export async function screenshotDouyinUserStream(
  request: DouyinScreenshotRequest,
  callbacks: DouyinSSECallbacks
): Promise<void> {
  const baseUrl = import.meta.env.VITE_API_BASE_URL || '/api'
  const token = getToken()
  
  const response = await fetch(`${baseUrl}/v1/douyin/screenshot/stream`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      ...(token ? { 'Authorization': `Bearer ${token}` } : {}),
    },
    body: JSON.stringify(request),
  })

  if (!response.ok) {
    throw new Error(`HTTP error! status: ${response.status}`)
  }

  await processSSEStream(response, callbacks)
}

/**
 * 流式视觉分析
 */
export async function visionAnalysisDouyinStream(
  request: DouyinVisionAnalysisRequest,
  callbacks: DouyinSSECallbacks
): Promise<void> {
  const baseUrl = import.meta.env.VITE_API_BASE_URL || '/api'
  const token = getToken()
  
  const response = await fetch(`${baseUrl}/v1/douyin/vision-analysis/stream`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      ...(token ? { 'Authorization': `Bearer ${token}` } : {}),
    },
    body: JSON.stringify(request),
  })

  if (!response.ok) {
    throw new Error(`HTTP error! status: ${response.status}`)
  }

  await processSSEStream(response, callbacks)
}

/**
 * 取消 SSE 任务
 */
export async function cancelDouyinSSETask(taskId: string): Promise<{ success: boolean; message: string }> {
  return apiFetch<{ success: boolean; message: string }>(
    `/v1/douyin/sse/cancel/${encodeURIComponent(taskId)}`,
    { method: 'POST' }
  )
}

/**
 * 处理 SSE 流
 */
async function processSSEStream(response: Response, callbacks: DouyinSSECallbacks): Promise<void> {
  const reader = response.body?.getReader()
  if (!reader) {
    throw new Error('无法获取响应流')
  }

  const decoder = new TextDecoder()
  let buffer = ''
  let accumulatedContent = ''

  try {
    while (true) {
      const { done, value } = await reader.read()
      if (done) break

      buffer += decoder.decode(value, { stream: true })
      
      const parts = buffer.split('data: ')
      buffer = parts.pop() || ''
      
      for (const part of parts) {
        if (!part.trim()) continue
        
        let jsonStr = part.trim()
        const lastBrace = jsonStr.lastIndexOf('}')
        if (lastBrace !== -1 && lastBrace < jsonStr.length - 1) {
          buffer = jsonStr.slice(lastBrace + 1) + buffer
          jsonStr = jsonStr.slice(0, lastBrace + 1)
        }
        
        if (!jsonStr) continue
        
        try {
          const msg: DouyinSSEMessage = JSON.parse(jsonStr)

          switch (msg.type) {
            case 'init':
              callbacks.onInit?.(msg.task_id || '', msg.stage || '')
              break
            case 'status':
              callbacks.onStatus?.(msg.message || '', msg.stage)
              break
            case 'progress':
              callbacks.onProgress?.(msg.message || '')
              break
            case 'content':
              accumulatedContent += msg.content || ''
              callbacks.onContent?.(msg.content || '', accumulatedContent)
              break
            case 'result':
              callbacks.onResult?.(msg.data)
              break
            case 'done':
              callbacks.onDone?.(msg.message || '')
              break
            case 'cancelled':
              callbacks.onCancelled?.(msg.message || '任务已取消', msg.stage)
              break
            case 'error':
              callbacks.onError?.(msg.message || '未知错误')
              break
          }
        } catch (e) {
          console.error('Parse SSE error:', e, 'Data:', jsonStr)
        }
      }
    }
  } finally {
    reader.releaseLock()
  }
}
