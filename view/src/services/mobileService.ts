/**
 * 云手机操控 - 真实后端接入服务
 *
 * 对接 MOBILE_API.md (后端 v3)：设备资源池 / 设备分组与元数据 / 基础控制 /
 * AI 规划执行 / 话术辅助 / 实时事件流。统一鉴权(Bearer)、统一错误(detail+path)、SSE 解析。
 *
 * 路径约定：后端挂载在 `/api/v1/mobile/**`。
 * 开发期 API_CONFIG.BASE_URL = '/api'（vite 代理到 127.0.0.1:8000），
 * 因此这里所有 path 以 `/v1/mobile/...` 拼接，与项目其它 service 一致。
 *
 * v3 同步要点：
 *  - /overview 配置改为 config.llm_configured + config.models.{default,vision}，
 *    并新增 video_defaults / coordinate_scales / socketio 块。
 *  - /pool 设备附带稳定 device_key 与 meta（分组/备注/标签/显示名）。
 *  - 新增设备分组 CRUD 与设备元数据读写（§13b）。
 *  - tap/swipe 支持 coord_space；控制类接口设备无响应统一 504。
 *  - 错误响应统一附带 path（见 MobileError.path）。
 */

import { API_CONFIG } from '../config/api'
import { getToken, clearToken } from './http'

const MOBILE = '/v1/mobile'

// ============================================
// 错误类型
// ============================================

export class MobileError extends Error {
  status: number
  /** v3：后端统一在错误响应附带的请求路径，便于定位/上报 */
  path?: string
  constructor(message: string, status: number, path?: string) {
    super(message)
    this.name = 'MobileError'
    this.status = status
    this.path = path
  }
}

function buildHeaders(json = true): Headers {
  const headers = new Headers()
  if (json) headers.set('Content-Type', 'application/json')
  const token = getToken()
  if (token) headers.set('Authorization', `Bearer ${token}`)
  return headers
}

function handleUnauthorized(): void {
  clearToken()
  if (window.location.pathname !== '/login') {
    window.location.href = '/login'
  }
}

function resolveApiUrl(pathOrUrl: string): string {
  if (/^https?:\/\//i.test(pathOrUrl)) return pathOrUrl
  if (pathOrUrl.startsWith('/api/') && API_CONFIG.BASE_URL.endsWith('/api')) {
    if (/^https?:\/\//i.test(API_CONFIG.BASE_URL)) {
      return `${API_CONFIG.BASE_URL.replace(/\/api\/?$/, '')}${pathOrUrl}`
    }
    return pathOrUrl
  }
  return `${API_CONFIG.BASE_URL}${pathOrUrl.startsWith('/') ? pathOrUrl : `/${pathOrUrl}`}`
}

/**
 * 解析后端统一错误信封。v3 错误体形如 `{ detail, path }`：
 * - detail 为字符串：直接展示；
 * - detail 为数组（422 校验错）：打包成可读字段错误；
 * - 同时回传 path（若有）便于上报/定位。
 */
async function extractError(resp: Response): Promise<{ message: string; path?: string }> {
  const ct = resp.headers.get('content-type')
  if (ct?.includes('application/json')) {
    const body = await resp.json().catch(() => null)
    const path: string | undefined = typeof body?.path === 'string' ? body.path : undefined
    if (body?.detail != null) {
      if (typeof body.detail === 'string') return { message: body.detail, path }
      if (Array.isArray(body.detail)) {
        const msg = body.detail
          .map((d: { loc?: unknown[]; msg?: string }) => {
            const field = Array.isArray(d.loc) ? d.loc.filter((x) => x !== 'body').join('.') : ''
            return field ? `${field}: ${d.msg ?? ''}` : d.msg ?? ''
          })
          .filter(Boolean)
          .join('；')
        return { message: msg || `请求失败 (${resp.status})`, path }
      }
      return { message: JSON.stringify(body.detail), path }
    }
    return { message: `请求失败 (${resp.status})`, path }
  }
  const text = await resp.text().catch(() => '')
  return { message: text || `请求失败 (${resp.status})` }
}

/** JSON 请求（带鉴权 + detail 错误 + 401 跳登录） */
async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const resp = await fetch(`${API_CONFIG.BASE_URL}${path}`, {
    ...init,
    headers: buildHeaders(!!init.body || init.method === 'GET' || !init.method),
  })

  if (resp.status === 401) {
    handleUnauthorized()
    throw new MobileError('登录已过期，请重新登录', 401)
  }

  if (!resp.ok) {
    const { message, path } = await extractError(resp)
    throw new MobileError(message, resp.status, path)
  }

  const ct = resp.headers.get('content-type')
  if (ct?.includes('application/json')) return (await resp.json()) as T
  return (await resp.text()) as unknown as T
}

// ============================================
// 类型定义（对齐文档）
// ============================================

/** 设备元数据（v3 §13b）：按稳定 device_key 存储，掉线重连不丢 */
export interface DeviceMeta {
  device_id?: string
  device_key?: string
  display_name?: string | null
  note?: string | null
  tags?: string[]
  group_id?: string | null
}

export interface PoolDevice {
  device_id: string
  status: string // "device" | "available" | "offline" ...
  model?: string | null
  connection_type?: string | null // "usb" | "wifi" | "remote"
  online: boolean
  reserved: boolean
  owner: string | null
  since: number | null // unix 秒(float)
  note: string | null // 顶层 note = 占用备注（区别于 meta.note 设备备注）
  /** v3：稳定硬件 key（ro.serialno），USB/WiFi 重连不变 */
  device_key?: string
  /** v3：设备分组 / 备注 / 标签 / 显示名 */
  meta?: DeviceMeta | null
  /** EasyTier 已入网但 ADB 尚未配对/连接的候选手机 */
  network_ip?: string
  pairing_required?: boolean
  pairing_available?: boolean
  easytier_peer?: EasyTierPeerCandidate
}

export interface PoolResponse {
  devices: PoolDevice[]
  total: number
}

/** 设备分组（v3 §13b） */
export interface DeviceGroup {
  group_id: string
  name: string
  color?: string | null
  order?: number
  device_count?: number
  created_at?: string
  updated_at?: string
}

export interface DeviceHealth {
  device_id: string
  online: boolean
  screenshot_ready: boolean
  input_ready: boolean
  current_app_ready: boolean
  /** v3：底层截屏是否失败（设备无响应超时也会置 true） */
  capture_failed?: boolean
  error: string | null
}

export interface AutoConnectResult {
  connected: { source?: string; serial: string | null; address: string; ok: boolean; message: string }[]
  errors: { source?: string; serial?: string | null; address: string; ok?: boolean; message?: string }[]
  count: number
  sources?: { mdns: number; easytier_scan: number }
  scan?: { enabled: boolean; scanned: number; open: number; cidr?: string; port?: number; pairing_candidates?: number }
  pairing_candidates?: EasyTierPeerCandidate[]
}

export interface WakeResult {
  ok: boolean
  wake?: { ok: boolean; stderr: string }
  stay_on?: { ok: boolean; stderr: string }
}

export interface EasyTierAccessProfile {
  enabled: boolean
  public_host: string
  network_name: string
  network_secret: string
  hostname: string
  virtual_cidr: string
  adb_port: number
  backend_peer_hostname: string
  backend_peer_ipv4: string
  phone_ipv4_cidr: string
  auto_scan_enabled: boolean
  listeners: string[]
  peers: string[]
  agent_download_url: string
  android_download_url: string
  docs_url: string
  server_command: string
  phone_command: string
  config_filename: string
  config_toml: string
  warnings: string[]
}

export interface EasyTierPeerCandidate {
  id?: string
  hostname: string
  ipv4: string
  cidr?: string
  adb_port?: number
  cost?: string
  lat_ms?: string
  loss_rate?: string
  rx_bytes?: string
  tx_bytes?: string
  tunnel_proto?: string
  nat_type?: string
  version?: string
  source?: string
}

export interface AdbPairResult {
  ok: boolean
  message?: string
  stdout?: string
  stderr?: string
  connected?: boolean
  address?: string
  pair?: { ok: boolean; stdout?: string; stderr?: string }
  connect?: { ok: boolean; stdout?: string; stderr?: string; address?: string } | null
  pairing_service?: { name: string; service_type: string; address: string; host: string; port: number }
}

export interface AdbPairQrSession {
  service_name: string
  password: string
  qr_payload: string
}

/** 实时事件统一信封 */
export interface MobileEvent<D = Record<string, unknown>> {
  type: 'profile_updated' | 'suggestion' | 'auto_chat' | 'auto_chat_watch' | string
  device_id?: string
  contact_id?: string
  ts: number
  data: D
}

/** run-planned / chat-assist 的 SSE 帧（stage 驱动） */
export interface StageFrame<D = unknown> {
  stage: string
  data: D
}

export interface PlanResponse {
  goal: string
  subtasks: string[]
}

// ============================================
// 系统1：设备资源池
// ============================================

/**
 * 设备池全景。v3 支持按分组过滤：
 *  - groupId 省略：全部设备
 *  - groupId='grp_xxx'：仅该分组
 *  - groupId='ungrouped'：仅未分组
 */
export const getPool = (groupId?: string) =>
  request<PoolResponse>(
    `${MOBILE}/pool${groupId ? `?group_id=${encodeURIComponent(groupId)}` : ''}`,
  )

export const autoConnect = () =>
  request<AutoConnectResult>(`${MOBILE}/pool/auto-connect`, { method: 'POST' })

export const acquireDevice = (deviceId: string, note?: string) =>
  request<{ ok: boolean; device_id: string; device_key?: string; owner: string; since: number }>(
    `${MOBILE}/pool/acquire`,
    { method: 'POST', body: JSON.stringify({ device_id: deviceId, note: note ?? '' }) },
  )

export const releaseDevice = (deviceId: string) =>
  request<{ ok: boolean; device_key?: string }>(`${MOBILE}/pool/release`, {
    method: 'POST',
    body: JSON.stringify({ device_id: deviceId }),
  })

export const wakeDevice = (deviceId: string, stayOn = false) =>
  request<WakeResult>(`${MOBILE}/pool/wake`, {
    method: 'POST',
    body: JSON.stringify({ device_id: deviceId, stay_on: stayOn }),
  })

export const wakeUnlockDevice = (deviceId: string, pin?: string, stayOn = true) =>
  request<WakeResult & { unlock?: { step: string; ok: boolean; stderr?: string; error?: string }[] }>(
    `${MOBILE}/pool/wake-unlock`,
    {
      method: 'POST',
      body: JSON.stringify({
        device_id: deviceId,
        stay_on: stayOn,
        ...(pin ? { pin } : {}),
      }),
    },
  )

export const connectWifi = (ip: string, port = 5555) =>
  request<{ ok: boolean }>(`${MOBILE}/pool/connect/wifi`, {
    method: 'POST',
    body: JSON.stringify({ ip, port }),
  })

export const getEasyTierAccess = () =>
  request<EasyTierAccessProfile>(`${MOBILE}/network/easytier/access`)

export const pairAdbWithCode = (ip: string, pairingPort: number, pairingCode: string, connectPort?: number) =>
  request<AdbPairResult>(`${MOBILE}/adb/wireless/pair-code`, {
    method: 'POST',
    body: JSON.stringify({
      ip,
      pairing_port: pairingPort,
      pairing_code: pairingCode,
      ...(connectPort ? { connect_port: connectPort } : {}),
    }),
  })

export const connectAdbWireless = (ip: string, port: number) =>
  request<AdbPairResult>(`${MOBILE}/adb/wireless/connect`, {
    method: 'POST',
    body: JSON.stringify({ ip, port }),
  })

export const startAdbQrPairing = () =>
  request<AdbPairQrSession>(`${MOBILE}/adb/wireless/pair-qr/start`, { method: 'POST' })

export const completeAdbQrPairing = (serviceName: string, password: string, timeoutSeconds = 20) =>
  request<AdbPairResult>(`${MOBILE}/adb/wireless/pair-qr/complete`, {
    method: 'POST',
    body: JSON.stringify({
      service_name: serviceName,
      password,
      timeout_seconds: timeoutSeconds,
      connect_after_pair: true,
    }),
  })

// ============================================
// 系统：设备健康 / 前台应用
// ============================================

export const getHealth = (deviceId: string) =>
  request<DeviceHealth>(`${MOBILE}/devices/${encodeURIComponent(deviceId)}/health`)

export const getCurrentApp = (deviceId: string) =>
  request<{ device_id: string; current_app: string }>(
    `${MOBILE}/devices/${encodeURIComponent(deviceId)}/current_app`,
  )

// ============================================
// 基础控制（坐标 = 设备真实像素）
// ============================================

const ctrl = (deviceId: string) => `${MOBILE}/devices/${encodeURIComponent(deviceId)}`

/**
 * 坐标空间（v3 §4）。默认 pixel（设备真实像素）。
 * 传 normalized_* / auto 时坐标无需前端按画面尺寸换算，由后端缩放。
 */
export type CoordSpace = 'pixel' | 'normalized_1000' | 'normalized_10000' | 'auto'

export const tap = (deviceId: string, x: number, y: number, coordSpace?: CoordSpace) =>
  request<{ ok: boolean }>(`${ctrl(deviceId)}/tap`, {
    method: 'POST',
    body: JSON.stringify({
      x: Math.round(x),
      y: Math.round(y),
      ...(coordSpace ? { coord_space: coordSpace } : {}),
    }),
  })

export const swipe = (
  deviceId: string,
  startX: number,
  startY: number,
  endX: number,
  endY: number,
  durationMs = 300,
  coordSpace?: CoordSpace,
) =>
  request<{ ok: boolean }>(`${ctrl(deviceId)}/swipe`, {
    method: 'POST',
    body: JSON.stringify({
      start_x: Math.round(startX),
      start_y: Math.round(startY),
      end_x: Math.round(endX),
      end_y: Math.round(endY),
      duration_ms: durationMs,
      ...(coordSpace ? { coord_space: coordSpace } : {}),
    }),
  })

export const inputText = (deviceId: string, text: string) =>
  request<{ ok: boolean }>(`${ctrl(deviceId)}/text`, {
    method: 'POST',
    body: JSON.stringify({ text }),
  })

export type MobileKey =
  | 'back'
  | 'home'
  | 'enter'
  | 'search'
  | 'delete'
  | 'tab'
  | 'menu'
  | 'escape'
  | 'space'
  | 'dpad_center'
  | 'dpad_up'
  | 'dpad_down'
  | 'dpad_left'
  | 'dpad_right'
  | 'app_switch'

export const pressKey = (deviceId: string, key: MobileKey) =>
  request<{ ok: boolean }>(`${ctrl(deviceId)}/key`, {
    method: 'POST',
    body: JSON.stringify({ key }),
  })

export const launchApp = (deviceId: string, appName: string) =>
  request<{ ok: boolean; app_name: string }>(`${ctrl(deviceId)}/launch`, {
    method: 'POST',
    body: JSON.stringify({ app_name: appName }),
  })

// ============================================
// 画面：静态截图（鉴权 blob，可轮询成实时预览）
// ============================================

/** 拉取一帧截图（image/png）。<img> 无法带鉴权头，故用 fetch blob。 */
export async function fetchScreenshotBlob(
  deviceId: string,
  signal?: AbortSignal,
): Promise<Blob> {
  const resp = await fetch(`${API_CONFIG.BASE_URL}${ctrl(deviceId)}/screenshot`, {
    headers: buildHeaders(false),
    signal,
    cache: 'no-store',
  })
  if (resp.status === 401) {
    handleUnauthorized()
    throw new MobileError('登录已过期', 401)
  }
  if (!resp.ok) {
    const { message, path } = await extractError(resp)
    throw new MobileError(message, resp.status, path)
  }
  return resp.blob()
}

// ============================================
// SSE 基础设施
// ============================================

/**
 * 打开一个 SSE 流，逐条把 `data:` JSON 回调出去。
 * 调用方用 AbortController 关闭。
 */
async function openSSE(
  path: string,
  init: RequestInit,
  onMessage: (data: unknown) => void,
): Promise<void> {
  const resp = await fetch(`${API_CONFIG.BASE_URL}${path}`, {
    ...init,
    headers: buildHeaders(!!init.body),
  })

  if (resp.status === 401) {
    handleUnauthorized()
    throw new MobileError('登录已过期', 401)
  }
  if (!resp.ok || !resp.body) {
    if (resp.body) {
      const { message, path } = await extractError(resp)
      throw new MobileError(message, resp.status, path)
    }
    throw new MobileError('无法建立事件流', resp.status)
  }

  const reader = resp.body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''

  try {
    while (true) {
      const { value, done } = await reader.read()
      if (done) break
      buffer += decoder.decode(value, { stream: true })
      let idx: number
      while ((idx = buffer.indexOf('\n')) >= 0) {
        const line = buffer.slice(0, idx)
        buffer = buffer.slice(idx + 1)
        const trimmed = line.trim()
        if (trimmed.startsWith('data:')) {
          const payload = trimmed.slice(5).trim()
          if (payload && payload !== '[DONE]') {
            try {
              onMessage(JSON.parse(payload))
            } catch {
              /* 忽略非 JSON 帧（如心跳） */
            }
          }
        }
      }
    }
  } finally {
    reader.releaseLock()
  }
}

// ============================================
// 系统2：AI 规划 + 执行
// ============================================

export const planGoal = (goal: string) =>
  request<PlanResponse>(`${MOBILE}/agent/plan`, {
    method: 'POST',
    body: JSON.stringify({ goal }),
  })

export interface RunPlannedBody {
  device_id: string
  goal: string
  screen_aware?: boolean
  max_replans?: number
  max_steps_per_subtask?: number
  project_id?: string
  contact_id?: string
}

export function runPlanned(
  body: RunPlannedBody,
  onFrame: (frame: StageFrame) => void,
  signal: AbortSignal,
): Promise<void> {
  return openSSE(
    `${MOBILE}/agent/run-planned`,
    { method: 'POST', body: JSON.stringify(body), signal },
    (data) => onFrame(data as StageFrame),
  )
}

export const cancelAgent = (taskId: string) =>
  request<{ ok: boolean }>(`${MOBILE}/agent/cancel`, {
    method: 'POST',
    body: JSON.stringify({ task_id: taskId }),
  })

// ============================================
// 系统4：话术辅助
// ============================================

export interface SuggestBody {
  device_id: string
  my_background?: string
  contact_id?: string
  contact_profile?: string
  project_id?: string
  task_id?: string
}

export function suggestChat(
  body: SuggestBody,
  onFrame: (frame: StageFrame) => void,
  signal: AbortSignal,
): Promise<void> {
  return openSSE(
    `${MOBILE}/chat-assist/suggest`,
    { method: 'POST', body: JSON.stringify(body), signal },
    (data) => onFrame(data as StageFrame),
  )
}

export interface SendChatBody {
  device_id: string
  text: string
  send_button?: { x: number; y: number }
  project_id?: string
  task_id?: string
  contact_id?: string
}

export const sendChat = (body: SendChatBody) =>
  request<{ ok: boolean }>(`${MOBILE}/chat-assist/send`, {
    method: 'POST',
    body: JSON.stringify(body),
  })

// ============================================
// 系统3/4/5：实时事件流
// ============================================

export interface EventQuery {
  device_id?: string
  contact_id?: string
  project_id?: string
  types?: string[]
}

function eventParams(q: EventQuery): string {
  const p = new URLSearchParams()
  if (q.device_id) p.set('device_id', q.device_id)
  if (q.contact_id) p.set('contact_id', q.contact_id)
  if (q.project_id) p.set('project_id', q.project_id)
  if (q.types?.length) p.set('types', q.types.join(','))
  const s = p.toString()
  return s ? `?${s}` : ''
}

export const getRecentEvents = (q: EventQuery & { limit?: number }) => {
  const p = new URLSearchParams()
  if (q.device_id) p.set('device_id', q.device_id)
  if (q.contact_id) p.set('contact_id', q.contact_id)
  if (q.project_id) p.set('project_id', q.project_id)
  if (q.types?.length) p.set('types', q.types.join(','))
  if (q.limit) p.set('limit', String(q.limit))
  const s = p.toString()
  return request<{ events: MobileEvent[] }>(`${MOBILE}/events/recent${s ? `?${s}` : ''}`)
}

export function subscribeEvents(
  q: EventQuery,
  onEvent: (ev: MobileEvent) => void,
  signal: AbortSignal,
): Promise<void> {
  return openSSE(`${MOBILE}/events${eventParams(q)}`, { method: 'GET', signal }, (data) =>
    onEvent(data as MobileEvent),
  )
}

// ============================================
// 设备列表 / 概览 / 视频流复位
// ============================================

export interface SimpleDevice {
  device_id: string
  status: string
  model?: string | null
  connection_type?: string | null
}

export const getDevices = () => request<{ devices: SimpleDevice[] }>(`${MOBILE}/devices`)

/** 视频画质/流畅度默认值（v3 /overview.video_defaults，来源前端写入的 MongoDB 运行时配置） */
export interface VideoDefaults {
  maxSize: number
  bitRate: number
  maxFps: number
  downsizeOnError: boolean
}

/** Socket.IO 能力广播（v3 /overview.socketio），前端据此连流/控制免硬编码 */
export interface SocketioInfo {
  path?: string
  connect_device?: string
  video_events?: string[]
  control_event?: string
  video_payload_keys?: string[]
}

export interface Overview {
  devices?: { total?: number; online?: number; items?: SimpleDevice[]; [k: string]: unknown }
  /**
   * v3：LLM 就绪状态与脱敏模型名。
   * 注意字段从 v2 的 llm_ready/model 改为 llm_configured/models.{default,vision}。
   */
  config?: {
    llm_configured?: boolean
    models?: { default?: string; vision?: string; [k: string]: unknown }
    sampling?: {
      max_tokens?: number
      temperature?: number
      top_p?: number
      frequency_penalty?: number
      [k: string]: unknown
    }
    [k: string]: unknown
  }
  video_defaults?: VideoDefaults
  coordinate_scales?: { agent?: number; api?: number }
  socketio?: SocketioInfo
  capabilities?: Record<string, unknown>
  running_tasks?: string[]
  [k: string]: unknown
}

export const getOverview = () => request<Overview>(`${MOBILE}/overview`)

export const videoReset = (deviceId?: string) =>
  request<{ ok: boolean }>(
    `${MOBILE}/video/reset${deviceId ? `?device_id=${encodeURIComponent(deviceId)}` : ''}`,
    { method: 'POST' },
  )

// ============================================
// 系统1：组网（USB→WiFi / 断开 / 远程发现接入 / 充电常亮）
// ============================================

export const usbToWifi = (deviceId: string, port = 5555) =>
  request<{ ok: boolean }>(`${MOBILE}/pool/connect/usb-to-wifi`, {
    method: 'POST',
    body: JSON.stringify({ device_id: deviceId, port }),
  })

export const disconnectDevice = (deviceId: string) =>
  request<{ ok: boolean }>(`${MOBILE}/pool/disconnect`, {
    method: 'POST',
    body: JSON.stringify({ device_id: deviceId }),
  })

export const remoteDiscover = (baseUrl: string) =>
  request<{ devices: SimpleDevice[] }>(`${MOBILE}/pool/remote/discover`, {
    method: 'POST',
    body: JSON.stringify({ base_url: baseUrl }),
  })

export const remoteAdd = (baseUrl: string, deviceId: string) =>
  request<{ ok: boolean }>(`${MOBILE}/pool/remote/add`, {
    method: 'POST',
    body: JSON.stringify({ base_url: baseUrl, device_id: deviceId }),
  })

export const remoteRemove = (serial: string) =>
  request<{ ok: boolean }>(`${MOBILE}/pool/remote/remove`, {
    method: 'POST',
    body: JSON.stringify({ serial }),
  })

export const stayAwake = (deviceId: string, on: boolean) =>
  request<{ ok: boolean; stderr: string }>(`${MOBILE}/pool/stay-awake`, {
    method: 'POST',
    body: JSON.stringify({ device_id: deviceId, on }),
  })

// ============================================
// 系统1b：设备分组（v3 §13b）
// ============================================

export const listGroups = () =>
  request<{ groups: DeviceGroup[]; total: number }>(`${MOBILE}/groups`)

export const createGroup = (name: string, color?: string) =>
  request<DeviceGroup>(`${MOBILE}/groups`, {
    method: 'POST',
    body: JSON.stringify({ name, ...(color ? { color } : {}) }),
  })

export interface UpdateGroupBody {
  name?: string
  color?: string
  order?: number
}

export const updateGroup = (groupId: string, body: UpdateGroupBody) =>
  request<DeviceGroup>(`${MOBILE}/groups/${encodeURIComponent(groupId)}`, {
    method: 'PATCH',
    body: JSON.stringify(body),
  })

export const deleteGroup = (groupId: string) =>
  request<{ ok?: boolean; status?: string }>(`${MOBILE}/groups/${encodeURIComponent(groupId)}`, {
    method: 'DELETE',
  })

// ============================================
// 系统1b：设备元数据 - 备注 / 标签 / 显示名 / 分组归属（v3 §13b）
// ============================================

export const getDeviceMeta = (deviceId: string) =>
  request<DeviceMeta>(`${MOBILE}/devices/${encodeURIComponent(deviceId)}/meta`)

/**
 * 部分更新设备元数据（仅写入传入的字段）。
 * 「移出分组」传 { group_id: null }；不传该字段则保持不变。
 */
export const updateDeviceMeta = (deviceId: string, meta: Partial<DeviceMeta>) =>
  request<DeviceMeta>(`${MOBILE}/devices/${encodeURIComponent(deviceId)}/meta`, {
    method: 'PUT',
    body: JSON.stringify(meta),
  })

// ============================================
// 系统2：单步 AI 任务（agent/task，type 驱动 SSE）
// ============================================

export interface AgentTaskBody {
  device_id: string
  task: string
  max_steps?: number
  project_id?: string
  contact_id?: string
}

export interface TypedFrame<D = unknown> {
  type: string
  data: D
}

export function agentTask(
  body: AgentTaskBody,
  onFrame: (frame: TypedFrame) => void,
  signal: AbortSignal,
): Promise<void> {
  return openSSE(
    `${MOBILE}/agent/task`,
    { method: 'POST', body: JSON.stringify(body), signal },
    (data) => onFrame(data as TypedFrame),
  )
}

// ============================================
// 系统3：联系人画像
// ============================================

export interface Persona {
  background?: string
  personality?: string
  communication_style?: string
  tone?: string
  reply_pattern?: string
  common_phrases?: string[]
  risk_signals?: string[]
  summary?: string
  interests: string[]
  tags: string[]
  confidence?: number
}

export interface ContactProfileProjectLink {
  project_id: string
  finding_id: string
  first_seen_at?: string
  updated_at?: string
}

export interface ContactProfile {
  contact_id: string
  name?: string
  platform?: string
  device_id?: string
  project_id?: string
  project_ids?: string[]
  latest_finding_id?: string
  project_links?: ContactProfileProjectLink[]
  persona: Persona
  observations: { ts: string; content: string; source: string; project_id?: string; finding_id?: string; task_id?: string }[]
  created_at: string
  updated_at: string
}

export async function listProfiles(deviceId?: string, limit = 100, projectId?: string): Promise<ContactProfile[]> {
  const p = new URLSearchParams()
  if (deviceId) p.set('device_id', deviceId)
  if (projectId) p.set('project_id', projectId)
  p.set('limit', String(limit))
  const res = await request<{ profiles?: ContactProfile[] } | ContactProfile[]>(`${MOBILE}/profiles?${p}`)
  if (Array.isArray(res)) return res
  return res.profiles ?? []
}

export const getProfile = (contactId: string) =>
  request<ContactProfile>(`${MOBILE}/profiles/${encodeURIComponent(contactId)}`)

export interface AnalyzeProfileBody {
  device_id: string
  contact_id: string
  name?: string
  platform?: string
  project_id?: string
  task_id?: string
}

export const analyzeProfile = (body: AnalyzeProfileBody) =>
  request<ContactProfile>(`${MOBILE}/profiles/analyze`, {
    method: 'POST',
    body: JSON.stringify(body),
  })

export interface UpsertProfileBody {
  name?: string
  platform?: string
  persona?: Partial<Persona>
  project_id?: string
}

export const upsertProfile = (contactId: string, body: UpsertProfileBody) =>
  request<ContactProfile>(`${MOBILE}/profiles/${encodeURIComponent(contactId)}`, {
    method: 'PUT',
    body: JSON.stringify(body),
  })

export const deleteProfile = (contactId: string) =>
  request<{ status?: string; ok?: boolean }>(`${MOBILE}/profiles/${encodeURIComponent(contactId)}`, {
    method: 'DELETE',
  })

// ============================================
// 系统4：最新建议快照
// ============================================

export interface SuggestionDoc {
  key: string
  device_id?: string
  contact_id?: string
  suggestions: string
  screen_analysis?: string
  created_at?: string
  updated_at?: string
}

export const getSuggestion = (key: string) =>
  request<SuggestionDoc>(`${MOBILE}/suggestions/${encodeURIComponent(key)}`)

// ============================================
// 系统5：自动聊天 + 新好友 watcher
// ============================================

export interface StartAutoChatBody {
  device_id: string
  contact_id?: string
  contact_name?: string
  goal?: string
  my_background?: string
  platform?: string
  interval?: number
  auto_send?: boolean
  ensure_chat?: boolean
  send_button?: { x: number; y: number }
  project_id?: string
}

export const startAutoChat = (body: StartAutoChatBody) =>
  request<{ ok: boolean; task_id: string }>(`${MOBILE}/auto-chat/start`, {
    method: 'POST',
    body: JSON.stringify(body),
  })

export const stopAutoChat = (taskId: string) =>
  request<{ ok: boolean }>(`${MOBILE}/auto-chat/stop`, {
    method: 'POST',
    body: JSON.stringify({ task_id: taskId }),
  })

export interface AutoChatSession {
  task_id: string
  device_id: string
  project_id?: string
  contact_id?: string
  contact_name?: string
  owner?: string
  running: boolean
  auto_send: boolean
  ensure_chat?: boolean
  rounds: number
  replies_sent: number
  observed?: number
  skipped?: number
  last_reply?: string
  last_suggestion?: string
  last_state?: Record<string, unknown>
  last_error?: string | null
  started_at?: number
}

export const getAutoChatStatus = (taskId?: string, projectId?: string) => {
  const p = new URLSearchParams()
  if (taskId) p.set('task_id', taskId)
  if (projectId) p.set('project_id', projectId)
  const s = p.toString()
  return request<{ sessions: AutoChatSession[] }>(
    `${MOBILE}/auto-chat/status${s ? `?${s}` : ''}`,
  )
}

// ============================================
// 项目维度：手机画像 / 截图 / 操作日志 / 自动聊天快照
// ============================================

export interface MobileScreenshot {
  screenshot_id: string
  project_id?: string
  task_id?: string | null
  device_id?: string | null
  contact_id?: string | null
  source?: string
  url: string
  width?: number | null
  height?: number | null
  operation_id?: string | null
  note?: string
  meta?: Record<string, unknown>
  created_at: string
}

export interface MobileOperationLog {
  operation_id: string
  operation_type: string
  device_id?: string | null
  project_id?: string | null
  task_id?: string | null
  contact_id?: string | null
  action?: string
  status?: string
  message?: string
  data?: Record<string, unknown>
  screenshot_id?: string | null
  created_at: string
}

export interface MobileProfileObservation {
  observation_id: string
  project_id?: string | null
  finding_id?: string | null
  task_id?: string | null
  device_id?: string | null
  contact_id: string
  platform?: string | null
  contact_name?: string | null
  source?: string
  screen_analysis?: string
  persona_patch?: Partial<Persona> & Record<string, unknown>
  persona_snapshot?: Partial<Persona> & Record<string, unknown>
  evidence?: Record<string, unknown>
  metrics?: Record<string, unknown>
  created_at: string
}

export const listProjectMobileProfiles = async (projectId: string, limit = 100) => {
  const res = await request<{ profiles: ContactProfile[]; total: number }>(
    `${MOBILE}/projects/${encodeURIComponent(projectId)}/profiles?limit=${limit}`,
  )
  return res
}

export const listProjectMobileProfileObservations = async (projectId: string, limit = 100) => {
  const res = await request<{ observations: MobileProfileObservation[]; total: number }>(
    `${MOBILE}/projects/${encodeURIComponent(projectId)}/profile-observations?limit=${limit}`,
  )
  return res
}

export const listProjectMobileScreenshots = async (projectId: string, limit = 60) => {
  const res = await request<{ screenshots: MobileScreenshot[]; total: number }>(
    `${MOBILE}/projects/${encodeURIComponent(projectId)}/screenshots?limit=${limit}`,
  )
  return res
}

export const listProjectMobileOperations = async (projectId: string, limit = 100) => {
  const res = await request<{ operations: MobileOperationLog[]; total: number }>(
    `${MOBILE}/projects/${encodeURIComponent(projectId)}/operations?limit=${limit}`,
  )
  return res
}

export const listProjectAutoChatSessions = async (projectId: string, limit = 100) => {
  const res = await request<{ sessions: AutoChatSession[]; total: number }>(
    `${MOBILE}/projects/${encodeURIComponent(projectId)}/auto-chat/sessions?limit=${limit}`,
  )
  return res
}

export async function fetchMobileScreenshotBlob(pathOrUrl: string, signal?: AbortSignal): Promise<Blob> {
  const resp = await fetch(resolveApiUrl(pathOrUrl), {
    headers: buildHeaders(false),
    signal,
    cache: 'no-store',
  })
  if (resp.status === 401) {
    handleUnauthorized()
    throw new MobileError('登录已过期', 401)
  }
  if (!resp.ok) {
    const { message, path } = await extractError(resp)
    throw new MobileError(message, resp.status, path)
  }
  return resp.blob()
}

export interface StartWatchBody {
  device_id: string
  platform?: string
  my_background?: string
  auto_accept?: boolean
  auto_send?: boolean
  interval?: number
  send_button?: { x: number; y: number }
  project_id?: string
}

export const startWatch = (body: StartWatchBody) =>
  request<{ ok: boolean; watch_id: string }>(`${MOBILE}/auto-chat/watch/start`, {
    method: 'POST',
    body: JSON.stringify(body),
  })

export const stopWatch = (watchId: string) =>
  request<{ ok: boolean; watch_id: string }>(`${MOBILE}/auto-chat/watch/stop`, {
    method: 'POST',
    body: JSON.stringify({ watch_id: watchId }),
  })

// ============================================
// 工具函数
// ============================================

/** 时间戳归一：number=unix 秒，string=ISO */
export function toDate(x?: string | number | null): Date | null {
  if (x == null) return null
  return typeof x === 'number' ? new Date(x * 1000) : new Date(x)
}

export function relativeTime(x?: string | number | null): string {
  const d = toDate(x)
  if (!d) return '-'
  const diff = Date.now() - d.getTime()
  const s = Math.floor(diff / 1000)
  if (s < 60) return `${Math.max(s, 0)} 秒前`
  const m = Math.floor(s / 60)
  if (m < 60) return `${m} 分钟前`
  const h = Math.floor(m / 60)
  if (h < 24) return `${h} 小时前`
  return d.toLocaleString('zh-CN', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' })
}
