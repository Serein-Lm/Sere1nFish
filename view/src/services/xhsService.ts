import { apiFetch, getToken } from './http'

// ============ 类型定义 ============

/**
 * 小红书 Cookie 账号信息
 */
export interface XhsCookieAccount {
  id: string
  account_name: string
  is_active: boolean
  is_enabled: boolean
  is_valid: boolean | null
  last_verified_at?: string
  last_used_at?: string
  cooldown_until?: string
  lease_count: number
  success_count: number
  failure_count: number
  consecutive_failures: number
  quarantined_at?: string | null
  quarantine_reason?: string | null
  last_error?: string | null
  created_at: string
  updated_at: string
}

/**
 * 创建 Cookie 请求
 */
export interface CreateCookieRequest {
  account_name: string
  cookie_string: string
}

/**
 * 验证 Cookie 响应
 */
export interface VerifyCookieResponse {
  account_name: string
  is_valid: boolean
  message: string
}

/**
 * 搜索任务
 */
export interface XhsSearchTask {
  id: string
  project_id: string
  keyword: string
  status: 'pending' | 'running' | 'completed' | 'failed'
  notes_count: number
  suspicious_count: number
  profiles_count: number
  error_message?: string | null
  created_at: string
}

/**
 * 创建搜索任务请求
 */
export interface CreateSearchTaskRequest {
  project_id: string
  keyword: string
  max_notes?: number
  attention_threshold?: number
}

/**
 * 笔记用户信息
 */
export interface XhsNoteUser {
  user_id: string
  nickname: string
  avatar?: string
}

/**
 * 笔记标签数据
 */
export interface XhsNoteTagging {
  keyword_relevance: number
  relevance_reason: string
  is_suspicious: boolean
  attention_score: number
  attack_surface_types: string[]
  reason?: string
  evidence?: string
  company_mentioned: string | null
  key_info_extracted: string[]
}

/**
 * 小红书笔记
 */
export interface XhsNote {
  id: string
  project_id: string
  task_id?: string
  keyword?: string
  note_id: string
  xsec_token?: string
  xsec_source?: string
  title: string
  desc: string
  liked_count?: string
  user: XhsNoteUser
  cover?: string
  tagging: XhsNoteTagging
  created_at?: string
}

/**
 * 笔记详情发现
 */
export interface XhsDetailFinding {
  type: string
  value: string
  evidence: string
  attention_reason: string
}

/**
 * 笔记详情打标 - 公司识别
 */
export interface XhsDetailCompanyIdentified {
  name: string
  confidence: string
  evidence: string  // 注意：这里是字符串，不是数组
  related_to_keyword: boolean
  relationship_type: string | null
}

/**
 * 笔记详情打标
 */
export interface XhsDetailTagging {
  keyword_relevance: number
  keyword_analysis: string
  company_identified: XhsDetailCompanyIdentified | null
  attention_score: number
  findings: XhsDetailFinding[]
  summary: string
}

/**
 * 笔记详情
 */
export interface XhsNoteDetail {
  id: string
  note_id: string
  project_id: string
  content: string
  comments_summary: string
  tagging: XhsDetailTagging
  created_at?: string
}

/**
 * 公司归属识别（旧版，保留兼容）
 */
export interface CompanyIdentified {
  name: string
  confidence: 'high' | 'medium' | 'low'
  evidence: string[]
  related_to_keyword?: boolean
  relationship_type?: string
}

// ============ 新版人物画像类型定义 ============

/**
 * 基础信息
 */
export interface ProfileBasicInfo {
  xhs_id: string | null
  ip_location: string | null
  account_type: string | null
  account_age_estimate: string | null
  verification: string | null
}

/**
 * 账号数据
 */
export interface ProfileStats {
  follows: string
  fans: string
  likes_and_collects: string
  notes_count: string
  activity_level: string
  influence_level: string
}

/**
 * 身份信息
 */
export interface ProfileIdentity {
  company: string | null
  industry: string | null
  position: string | null
  position_level: string | null
  department: string | null
  work_years: string | null
  employment_status: string | null
  confidence: string | null
}

/**
 * 教育信息
 */
export interface ProfileEducation {
  school: string | null
  school_tier: string | null
  degree: string | null
  major: string | null
  graduation_year: string | null
}

/**
 * 位置信息
 */
export interface ProfileLocation {
  city: string | null
  district: string | null
  work_address_hint: string | null
  matches_ip: boolean
}

/**
 * 联系方式暴露
 */
export interface ProfileContactExposed {
  wechat: string | null
  email: string | null
  phone: string | null
  other_social: string[]
}

/**
 * 简介分析
 */
export interface ProfileBioAnalysis {
  raw: string
  identity_tags: string[]
  education: ProfileEducation
  location: ProfileLocation
  contact_exposed: ProfileContactExposed
  interests: string[]
  life_stage: string | null
  life_events: string[]
}

/**
 * 设备信息
 */
export interface ProfileDeviceInfo {
  computer_os: string | null
  computer_brand: string | null
  phone_brand: string | null
  evidence: string[]
}

/**
 * 头像分析
 */
export interface ProfileAvatarAnalysis {
  type: string
  is_real_person: boolean
  gender_from_avatar: string | null
  age_estimate: string | null
  appearance_features: string[]
  dress_style: string | null
  has_work_badge: boolean
  badge_info: string | null
  has_company_logo: boolean
  company_logo_info: string | null
  background_location: string | null
  background_clues: string[]
  photo_professionalism: string | null
}

/**
 * 性别分析
 */
export interface ProfileGenderAnalysis {
  conclusion: string
  confidence: string
  evidence: {
    from_avatar: string
    from_nickname: string
    from_bio: string
    from_notes: string
    from_writing_style: string
  }
}

/**
 * 大五人格
 */
export interface ProfileBigFive {
  openness: string
  conscientiousness: string
  extraversion: string
  agreeableness: string
  neuroticism: string
}

/**
 * 性格画像
 */
export interface ProfilePersonality {
  keywords: string[]
  mbti_estimate: string | null
  big_five: ProfileBigFive
  communication_style: string | null
  content_style: string | null
  emotional_tendency: string | null
  values_hint: string[]
  vulnerability_points: string[]
  trust_building_approach: string | null
}

/**
 * 笔记内容分布
 */
export interface NoteContentDistribution {
  category: string
  count: string
  percentage: string
  social_value: string
}

/**
 * 发布模式
 */
export interface NotePostingPattern {
  frequency: string
  active_time: string
  recent_activity: string
}

/**
 * 敏感笔记
 */
export interface SensitiveNote {
  title: string
  type: string
  sensitive_level: string
  exposed_info: string[]
  exploitability: string
}

/**
 * 工作内容
 */
export interface NoteWorkContent {
  has_work_content: boolean
  work_topics: string[]
  project_mentions: string[]
  tool_mentions: string[]
  insider_level: string
}

/**
 * 消费提示
 */
export interface NoteConsumptionHints {
  spending_level: string
  brand_preferences: string[]
  lifestyle_indicators: string[]
}

/**
 * 笔记分析
 */
export interface ProfileNotesAnalysis {
  total_visible: string
  content_distribution: NoteContentDistribution[]
  posting_pattern: NotePostingPattern
  sensitive_notes: SensitiveNote[]
  work_content: NoteWorkContent
  consumption_hints: NoteConsumptionHints
}

/**
 * 公司识别
 */
export interface ProfileCompanyIdentification {
  identified_company: string | null
  confidence: string
  evidence: string[]
  company_type: string | null
  company_scale: string | null
  industry: string | null
  business_line: string | null
  office_location: string | null
  related_companies: string[]
  competitor_of: string[]
}

/**
 * 关键词关联度
 */
export interface ProfileKeywordRelevance {
  score: number
  target_company: string | null
  target_keyword: string | null
  relationship: string | null
  evidence: string[]
  analysis: string
}

/**
 * 身份确认
 */
export interface IdentityConfirmation {
  confirmed: boolean
  real_name_exposed: boolean
  real_name: string | null
  confidence: string
}

/**
 * 暴露信息
 */
export interface ExposedInformation {
  category: string
  type: string
  value: string
  source: string
  sensitivity: string
  freshness: string
  exploitability: string
}

/**
 * 凭证泄露
 */
export interface CredentialLeaks {
  internal_codes: string[]
  system_access: string[]
  account_hints: string[]
}

/**
 * 攻击向量
 */
export interface AttackVector {
  vector: string
  method: string
  prerequisites: string[]
  difficulty: string
  success_probability: string
  potential_gain: string
}

/**
 * 攻击面分析
 */
export interface ProfileAttackSurface {
  risk_score: number
  risk_level: string
  identity_confirmation: IdentityConfirmation
  exposed_information: ExposedInformation[]
  credential_leaks: CredentialLeaks
  attack_vectors: AttackVector[]
}

/**
 * 社交图谱
 */
export interface ProfileSocialGraph {
  mentioned_colleagues: string[]
  mentioned_companies: string[]
  team_info: string | null
  manager_hints: string | null
  social_circle: string | null
  relationship_status: string | null
  family_info: string | null
}

/**
 * 职业历史
 */
export interface CareerHistory {
  company: string
  position: string
  period: string
  source: string
}

/**
 * 教育历史
 */
export interface EducationHistory {
  school: string
  degree: string
  period: string
}

/**
 * 时间线
 */
export interface ProfileTimeline {
  career_history: CareerHistory[]
  education_history: EducationHistory[]
  key_events: string[]
}

/**
 * 建议行动
 */
export interface RecommendedAction {
  action: string
  description: string
  priority: string
  difficulty: string
  expected_outcome: string
  risk: string
}

/**
 * 小红书人物画像（新版完整结构）
 */
export interface XhsProfile {
  id: string
  project_id: string
  task_id?: string
  finding_id?: string
  user_id: string
  nickname: string
  avatar_url?: string
  
  // 分析结果
  basic_info: ProfileBasicInfo
  stats: ProfileStats
  identity: ProfileIdentity
  bio_analysis: ProfileBioAnalysis
  device_info: ProfileDeviceInfo
  avatar_analysis: ProfileAvatarAnalysis
  gender_analysis: ProfileGenderAnalysis
  personality_profile: ProfilePersonality
  notes_analysis: ProfileNotesAnalysis
  company_identification: ProfileCompanyIdentification
  keyword_relevance: ProfileKeywordRelevance
  attack_surface: ProfileAttackSurface
  social_graph: ProfileSocialGraph
  timeline: ProfileTimeline
  
  // 汇总
  profile_summary: string
  attention_score: number
  recommended_actions: RecommendedAction[]
  tags: string[]
  
  // 关联
  note_ids: string[]
  notes_count: number
  
  // 时间戳
  created_at?: string
  updated_at?: string
}

// 保留旧版类型别名以兼容
export interface XhsProfileTagging {
  keyword_relevance: number
  keyword_relevance_reason: string
  company_identified: CompanyIdentified | null
  profile_summary: string
  risk_assessment: string
  attention_score: number
  potential_attack_vectors: string[]
  recommended_actions: string[]
}

// ============ Cookie 管理 API ============

/**
 * 获取 Cookie 账号列表
 */
export async function listXhsCookies(): Promise<XhsCookieAccount[]> {
  const page = await apiFetch<{ items: XhsCookieAccount[] }>('/v1/xhs/cookies', {
    method: 'GET',
  })
  return page.items || []
}

/**
 * 添加 Cookie 账号
 */
export async function createXhsCookie(body: CreateCookieRequest): Promise<XhsCookieAccount> {
  return apiFetch<XhsCookieAccount>('/v1/xhs/cookies', {
    method: 'POST',
    body: JSON.stringify(body),
  })
}

/**
 * 验证 Cookie 有效性
 */
export async function verifyXhsCookie(accountName: string): Promise<VerifyCookieResponse> {
  return apiFetch<VerifyCookieResponse>(
    `/v1/xhs/cookies/${encodeURIComponent(accountName)}/verify`,
    {
      method: 'POST',
    }
  )
}

/**
 * 激活 Cookie 账号
 */
export async function activateXhsCookie(
  accountName: string
): Promise<{ message: string; account: XhsCookieAccount }> {
  return apiFetch<{ message: string; account: XhsCookieAccount }>(
    `/v1/xhs/cookies/${encodeURIComponent(accountName)}/activate`,
    {
      method: 'POST',
    }
  )
}

/**
 * 删除 Cookie 账号
 */
export async function deleteXhsCookie(accountName: string): Promise<{ ok: boolean }> {
  return apiFetch<{ ok: boolean }>(
    `/v1/xhs/cookies/${encodeURIComponent(accountName)}`,
    {
      method: 'DELETE',
    }
  )
}

/**
 * 获取账号详情（含 Cookie 字符串）
 */
export interface XhsCookieDetail extends XhsCookieAccount {
  cookie_string: string
}

export async function getXhsCookieDetail(accountName: string): Promise<XhsCookieDetail> {
  return apiFetch<XhsCookieDetail>(
    `/v1/xhs/cookies/${encodeURIComponent(accountName)}/detail`,
    {
      method: 'GET',
    }
  )
}

/**
 * 更新账号 Cookie
 */
export interface UpdateXhsCookieRequest {
  new_account_name?: string
  cookie_string?: string
  is_active?: boolean
  is_enabled?: boolean
}

export interface XhsRuntimeStatus {
  account_pool: {
    enabled: boolean
    strategy: string
    search_pages_per_account: number
    search_retries_per_page: number
    search_page_size: number
    search_max_pages_per_keyword: number
    request_interval_min_seconds: number
    request_interval_max_seconds: number
    search_fallback_enabled: boolean
    search_health_check_enabled: boolean
    total: number
    usable: number
    invalid: number
    cooling_down: number
    quarantined: number
    max_consecutive_failures: number
  }
  proxy_pool: {
    enabled: boolean
    provider: string
    static_count: number
    fail_open: boolean
  }
}

export interface XhsSignerTestResult {
  ok: boolean
  message?: string
  elapsed_ms: number
  script: {
    path?: string
    exists?: boolean
  }
  sign: {
    ok?: boolean
    x_s_prefix?: string
    x_t?: string | number
  }
  network?: {
    ok: boolean
    account_name?: string
    proxy?: Record<string, unknown>
  } | null
}

export async function updateXhsCookie(
  accountName: string,
  body: UpdateXhsCookieRequest
): Promise<XhsCookieAccount> {
  return apiFetch<XhsCookieAccount>(
    `/v1/xhs/cookies/${encodeURIComponent(accountName)}`,
    {
      method: 'PUT',
      body: JSON.stringify(body),
    }
  )
}

export async function getXhsRuntimeStatus(): Promise<XhsRuntimeStatus> {
  return apiFetch<XhsRuntimeStatus>('/v1/xhs/runtime/status', {
    method: 'GET',
  })
}

export async function testXhsSigner(params?: {
  account_name?: string
  verify_network?: boolean
}): Promise<XhsSignerTestResult> {
  const query = new URLSearchParams()
  if (params?.account_name) query.set('account_name', params.account_name)
  if (params?.verify_network) query.set('verify_network', 'true')
  const qs = query.toString()
  return apiFetch<XhsSignerTestResult>(`/v1/xhs/runtime/sign-test${qs ? `?${qs}` : ''}`, {
    method: 'POST',
  })
}

// ============ 搜索任务 API ============

/**
 * 创建搜索任务
 */
export async function createXhsSearchTask(
  body: CreateSearchTaskRequest
): Promise<{ task: XhsSearchTask; message: string }> {
  return apiFetch<{ task: XhsSearchTask; message: string }>('/v1/xhs/search', {
    method: 'POST',
    body: JSON.stringify(body),
  })
}

/**
 * 查询任务状态
 */
export async function getXhsSearchTask(taskId: string): Promise<XhsSearchTask> {
  return apiFetch<XhsSearchTask>(`/v1/xhs/tasks/${encodeURIComponent(taskId)}`, {
    method: 'GET',
  })
}

// ============ 笔记查询 API ============

/**
 * 查询项目下的笔记（POST 分页）
 */
export async function listXhsNotes(
  projectId: string,
  params?: {
    page?: number
    page_size?: number
    task_id?: string
    is_suspicious?: boolean | null
    sort_by?: 'relevance' | 'created_at'
  }
): Promise<{ items: XhsNote[]; total: number; page: number; page_size: number }> {
  return apiFetch<{ items: XhsNote[]; total: number; page: number; page_size: number }>(
    `/v1/projects/${encodeURIComponent(projectId)}/notes`,
    {
      method: 'POST',
      body: JSON.stringify({
        project_id: projectId,
        page: params?.page ?? 1,
        page_size: params?.page_size ?? 50,
        task_id: params?.task_id ?? '',
        is_suspicious: params?.is_suspicious ?? null,
        sort_by: params?.sort_by ?? 'relevance',
      }),
    }
  )
}

/**
 * 获取单个笔记
 */
export async function getXhsNote(noteId: string): Promise<XhsNote> {
  return apiFetch<XhsNote>(`/v1/xhs/notes/${encodeURIComponent(noteId)}`, {
    method: 'GET',
  })
}

/**
 * 获取笔记详情
 */
export async function getXhsNoteDetail(noteId: string): Promise<XhsNoteDetail> {
  return apiFetch<XhsNoteDetail>(`/v1/xhs/notes/${encodeURIComponent(noteId)}/detail`, {
    method: 'GET',
  })
}

// ============ 人物画像 API ============

/**
 * 查询项目下的人物画像（POST 分页）
 */
export async function listXhsProfiles(
  projectId: string,
  params?: {
    page?: number
    page_size?: number
    min_score?: number
    sort?: 'score_desc' | 'score_asc'
  }
): Promise<{ items: XhsProfile[]; total: number; page: number; page_size: number }> {
  return apiFetch<{ items: XhsProfile[]; total: number; page: number; page_size: number }>(
    `/v1/projects/${encodeURIComponent(projectId)}/profiles`,
    {
      method: 'POST',
      body: JSON.stringify({
        project_id: projectId,
        page: params?.page ?? 1,
        page_size: params?.page_size ?? 50,
        min_score: params?.min_score ?? 0,
        sort: params?.sort ?? 'score_desc',
      }),
    }
  )
}

/**
 * 获取单个人物画像
 */
export async function getXhsProfile(profileId: string): Promise<XhsProfile> {
  return apiFetch<XhsProfile>(`/v1/xhs/profiles/${encodeURIComponent(profileId)}`, {
    method: 'GET',
  })
}

/**
 * 删除人物画像
 */
export async function deleteXhsProfile(profileId: string): Promise<{ ok: boolean; message: string }> {
  return apiFetch<{ ok: boolean; message: string }>(`/v1/xhs/profiles/${encodeURIComponent(profileId)}`, {
    method: 'DELETE',
  })
}

// ============ 人物画像生成 SSE API ============

/**
 * 人物画像生成请求
 */
export interface GenerateProfileRequest {
  user_url: string
  project_id: string
  keyword?: string
}

/**
 * SSE 消息类型
 */
export type ProfileSSEType = 'init' | 'status' | 'avatar' | 'content' | 'profile' | 'done' | 'cancelled' | 'error'

/**
 * 阶段类型
 */
export type ProfileStage = 'screenshot' | 'vision' | 'format' | 'save'

/**
 * SSE 消息数据
 */
export interface ProfileSSEMessage {
  type: ProfileSSEType
  task_id?: string
  user_id?: string
  stage?: ProfileStage
  message?: string
  avatar_url?: string
  content?: string
  data?: XhsProfileTaggingFull
}

/**
 * 完整的人物画像数据（从 SSE 返回，与 XhsProfile 相同）
 */
export type XhsProfileTaggingFull = XhsProfile

/**
 * 人物画像生成回调
 */
export interface ProfileGenerateCallbacks {
  onInit?: (taskId: string, userId: string, stage: ProfileStage) => void
  onStatus?: (message: string, stage?: ProfileStage) => void
  onAvatar?: (avatarUrl: string) => void
  onContent?: (content: string, accumulated: string) => void
  onProfile?: (profile: XhsProfile) => void
  onDone?: (userId: string) => void
  onCancelled?: (message: string, stage?: ProfileStage) => void
  onError?: (error: string) => void
}

/**
 * 流式生成人物画像
 */
export async function generateProfileStream(
  request: GenerateProfileRequest,
  callbacks: ProfileGenerateCallbacks
): Promise<void> {
  const baseUrl = import.meta.env.VITE_API_BASE_URL || '/api'
  const token = getToken()
  
  const response = await fetch(`${baseUrl}/v1/xhs/profile/generate/stream`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      ...(token ? { 'Authorization': `Bearer ${token}` } : {}),
    },
    body: JSON.stringify(request),
  })

  if (!response.ok) {
    if (response.status === 401) {
      throw new Error('未授权，请重新登录')
    }
    throw new Error(`HTTP error! status: ${response.status}`)
  }

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
      
      // 按 "data: " 分割处理消息
      const parts = buffer.split('data: ')
      
      // 最后一部分可能不完整，保留到下次处理
      buffer = parts.pop() || ''
      
      for (const part of parts) {
        if (!part.trim()) continue
        
        // 找到 JSON 的结束位置（处理可能粘连的情况）
        let jsonStr = part.trim()
        
        // 如果以 } 结尾后还有内容，说明有粘连
        const lastBrace = jsonStr.lastIndexOf('}')
        if (lastBrace !== -1 && lastBrace < jsonStr.length - 1) {
          // 把多余的部分放回 buffer
          buffer = jsonStr.slice(lastBrace + 1) + buffer
          jsonStr = jsonStr.slice(0, lastBrace + 1)
        }
        
        if (!jsonStr) continue
        
        try {
          const msg: ProfileSSEMessage = JSON.parse(jsonStr)

          switch (msg.type) {
            case 'init':
              callbacks.onInit?.(msg.task_id || '', msg.user_id || '', msg.stage || 'screenshot')
              break
            case 'status':
              callbacks.onStatus?.(msg.message || '', msg.stage)
              break
            case 'avatar':
              callbacks.onAvatar?.(msg.avatar_url || '')
              break
            case 'content':
              accumulatedContent += msg.content || ''
              callbacks.onContent?.(msg.content || '', accumulatedContent)
              break
            case 'profile':
              if (msg.data) {
                callbacks.onProfile?.(msg.data)
              }
              break
            case 'done':
              callbacks.onDone?.(msg.user_id || '')
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
    
    // 处理 buffer 中剩余的数据
    if (buffer.trim()) {
      try {
        const msg: ProfileSSEMessage = JSON.parse(buffer.trim())
        switch (msg.type) {
          case 'done':
            callbacks.onDone?.(msg.user_id || '')
            break
          case 'error':
            callbacks.onError?.(msg.message || '未知错误')
            break
        }
      } catch {
        // 忽略不完整的数据
      }
    }
  } finally {
    reader.releaseLock()
  }
}

/**
 * 取消 SSE 任务
 */
export async function cancelProfileTask(taskId: string): Promise<{ message: string }> {
  return apiFetch<{ message: string }>(`/v1/xhs/sse/cancel/${encodeURIComponent(taskId)}`, {
    method: 'POST',
  })
}
