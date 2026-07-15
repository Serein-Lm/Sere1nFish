import { apiFetch } from './http'

// ============ 类型定义 ============

export type ConfigSection = Record<string, unknown>

export interface LLMConfig {
  api_key: string
  base_url: string
  default_model: string
  vision_model: string
  mobile_planner_model?: string
  mobile_executor_model?: string
  mobile_screen_model?: string
  mobile_chat_model?: string
}

export interface ToolConfig {
  tool_name: string
  api_key: string
  has_key: boolean
}

export interface LangSmithConfig {
  enabled: boolean
  api_key: string
  project: string
  endpoint: string
}

export interface LangfuseConfig {
  enabled: boolean
  secret_key: string
  public_key: string
  base_url: string
}

export interface AllConfig {
  storage?: string
  revealed?: boolean
  configs?: Record<string, ConfigSection>
  llm: LLMConfig
  tools: Record<string, { api_key: string }>
  langsmith: LangSmithConfig
  langfuse: LangfuseConfig
  dingtalk?: Record<string, Partial<DingTalkBot>>
  app?: ConfigSection
  runtime?: ConfigSection
  mobile?: ConfigSection
  mongodb?: ConfigSection
  redis?: ConfigSection
  mcpServers?: ConfigSection
  logging?: ConfigSection
  cosyvoice?: ConfigSection
  bailian?: ConfigSection
  easytier?: ConfigSection
  notifications?: ConfigSection
  chrome_docker?: ConfigSection
  xhs_crawler?: ConfigSection
  douyin_crawler?: ConfigSection
}

export interface ConfigRevealStatus {
  configured: boolean
  bootstrap_with?: 'admin_password' | null
}

export interface ConfigSectionResponse {
  category: string
  config: ConfigSection
  storage: string
}

// ============ 获取所有配置 ============

export async function getAllConfig(): Promise<AllConfig> {
  return apiFetch<AllConfig>('/v1/config', { method: 'GET' })
}

export async function getConfigRevealStatus(): Promise<ConfigRevealStatus> {
  return apiFetch<ConfigRevealStatus>('/v1/config/reveal/status', { method: 'GET' })
}

export async function revealConfig(password: string): Promise<AllConfig> {
  return apiFetch<AllConfig>('/v1/config/reveal', {
    method: 'POST',
    body: JSON.stringify({ password }),
  })
}

export async function setConfigRevealPassword(
  currentPassword: string,
  newPassword: string
): Promise<{ ok: boolean; configured: boolean }> {
  return apiFetch<{ ok: boolean; configured: boolean }>('/v1/config/reveal/password', {
    method: 'POST',
    body: JSON.stringify({ current_password: currentPassword, new_password: newPassword }),
  })
}

export async function getConfigSection(category: string): Promise<ConfigSectionResponse> {
  return apiFetch<ConfigSectionResponse>(`/v1/config/sections/${encodeURIComponent(category)}`, {
    method: 'GET',
  })
}

export async function setConfigSection(category: string, config: ConfigSection): Promise<ConfigSectionResponse> {
  return apiFetch<ConfigSectionResponse>(`/v1/config/sections/${encodeURIComponent(category)}`, {
    method: 'POST',
    body: JSON.stringify({ config }),
  })
}

// ============ LLM 配置 ============

export async function getLLMConfig(): Promise<LLMConfig> {
  return apiFetch<LLMConfig>('/v1/config/llm', { method: 'GET' })
}

export async function setLLMConfig(config: Partial<LLMConfig>): Promise<LLMConfig> {
  return apiFetch<LLMConfig>('/v1/config/llm', {
    method: 'POST',
    body: JSON.stringify(config),
  })
}

export async function deleteLLMConfig(): Promise<{ ok: boolean }> {
  return apiFetch<{ ok: boolean }>('/v1/config/llm', { method: 'DELETE' })
}

// ============ 工具配置 ============

export async function listToolConfigs(): Promise<{ tools: ToolConfig[] }> {
  return apiFetch<{ tools: ToolConfig[] }>('/v1/config/tools', { method: 'GET' })
}

export async function getToolConfig(toolName: string): Promise<ToolConfig> {
  return apiFetch<ToolConfig>(`/v1/config/tools/${encodeURIComponent(toolName)}`, {
    method: 'GET',
  })
}

export async function setToolConfig(toolName: string, apiKey: string): Promise<ToolConfig> {
  return apiFetch<ToolConfig>(`/v1/config/tools/${encodeURIComponent(toolName)}`, {
    method: 'POST',
    body: JSON.stringify({ api_key: apiKey }),
  })
}

export async function deleteToolConfig(toolName: string): Promise<{ ok: boolean }> {
  return apiFetch<{ ok: boolean }>(`/v1/config/tools/${encodeURIComponent(toolName)}`, {
    method: 'DELETE',
  })
}

export async function testToolConfig(toolName: string): Promise<{ ok: boolean; message: string }> {
  return apiFetch<{ ok: boolean; message: string }>(
    `/v1/config/tools/${encodeURIComponent(toolName)}/test`,
    { method: 'POST' }
  )
}

// ============ LangSmith 配置 ============

export async function getLangSmithConfig(): Promise<LangSmithConfig> {
  return apiFetch<LangSmithConfig>('/v1/config/langsmith', { method: 'GET' })
}

export async function setLangSmithConfig(config: Partial<LangSmithConfig>): Promise<LangSmithConfig> {
  return apiFetch<LangSmithConfig>('/v1/config/langsmith', {
    method: 'POST',
    body: JSON.stringify(config),
  })
}

export async function toggleLangSmith(enabled: boolean): Promise<{ enabled: boolean }> {
  return apiFetch<{ enabled: boolean }>(`/v1/config/langsmith/toggle?enabled=${enabled}`, {
    method: 'POST',
  })
}

export async function deleteLangSmithConfig(): Promise<{ ok: boolean }> {
  return apiFetch<{ ok: boolean }>('/v1/config/langsmith', { method: 'DELETE' })
}

// ============ Langfuse 配置 ============

export async function getLangfuseConfig(): Promise<LangfuseConfig> {
  return apiFetch<LangfuseConfig>('/v1/config/langfuse', { method: 'GET' })
}

export async function setLangfuseConfig(config: Partial<LangfuseConfig>): Promise<LangfuseConfig> {
  return apiFetch<LangfuseConfig>('/v1/config/langfuse', {
    method: 'POST',
    body: JSON.stringify(config),
  })
}

export async function toggleLangfuse(enabled: boolean): Promise<{ enabled: boolean }> {
  return apiFetch<{ enabled: boolean }>(`/v1/config/langfuse/toggle?enabled=${enabled}`, {
    method: 'POST',
  })
}

export async function deleteLangfuseConfig(): Promise<{ ok: boolean }> {
  return apiFetch<{ ok: boolean }>('/v1/config/langfuse', { method: 'DELETE' })
}

// ============ 钉钉机器人配置 ============

export interface DingTalkBot {
  bot_name: string
  access_token: string
  secret: string
  keyword: string
  enabled: boolean
  has_token: boolean
  has_outgoing_secret?: boolean
  stream_enabled: boolean
  client_id?: string
  client_secret?: string
  has_client_secret: boolean
  ai_card_streaming: boolean
  public_base_url?: string
  reconnect_seconds: number
  stream_state: string
  stream_connected: boolean
  stream_last_error?: string
  stream_last_connected_at?: string
}

export interface DingTalkBotConfig {
  access_token?: string
  secret?: string
  keyword?: string
  enabled?: boolean
  outgoing_app_secret?: string
  stream_enabled?: boolean
  client_id?: string
  client_secret?: string
  ai_card_streaming?: boolean
  public_base_url?: string
  reconnect_seconds?: number
}

export interface DingTalkStreamStatus {
  bot_name: string
  state: string
  connected: boolean
  last_error?: string
  last_connected_at?: string
  last_message_at?: string
}

export async function listDingTalkBots(): Promise<{ bots: DingTalkBot[] }> {
  return apiFetch<{ bots: DingTalkBot[] }>('/v1/config/dingtalk', { method: 'GET' })
}

export async function getDingTalkBot(botName: string): Promise<DingTalkBot> {
  return apiFetch<DingTalkBot>(`/v1/config/dingtalk/${encodeURIComponent(botName)}`, {
    method: 'GET',
  })
}

export async function setDingTalkBot(botName: string, config: DingTalkBotConfig): Promise<DingTalkBot> {
  return apiFetch<DingTalkBot>(`/v1/config/dingtalk/${encodeURIComponent(botName)}`, {
    method: 'POST',
    body: JSON.stringify(config),
  })
}

export async function toggleDingTalkBot(botName: string, enabled: boolean): Promise<{ bot_name: string; enabled: boolean }> {
  return apiFetch<{ bot_name: string; enabled: boolean }>(
    `/v1/config/dingtalk/${encodeURIComponent(botName)}/toggle?enabled=${enabled}`,
    { method: 'POST' }
  )
}

export async function testDingTalkBot(botName: string): Promise<{ ok: boolean; message: string }> {
  return apiFetch<{ ok: boolean; message: string }>(
    `/v1/config/dingtalk/${encodeURIComponent(botName)}/test`,
    { method: 'POST' }
  )
}

export async function getDingTalkStreamStatus(botName: string): Promise<DingTalkStreamStatus> {
  return apiFetch<DingTalkStreamStatus>(
    `/v1/config/dingtalk/${encodeURIComponent(botName)}/status`,
    { method: 'GET' },
  )
}

export async function deleteDingTalkBot(botName: string): Promise<{ ok: boolean }> {
  return apiFetch<{ ok: boolean }>(`/v1/config/dingtalk/${encodeURIComponent(botName)}`, {
    method: 'DELETE',
  })
}
