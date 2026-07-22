import { API_CONFIG } from '../config/api'
import { apiFetch, clearToken, getToken } from './http'
import { redirectToLogin } from '../utils/authNavigation'

export interface DeepfakeStatus {
  ok: boolean
  provider: string
  model: string
  pixel_boost: string
  warmup_ms: number
  runtime_average_fps: number
  active_sessions: number
  session_count: number
  max_sessions: number
  model_use: string
  gpu: {
    name: string
    memory_total_mb?: number
    memory_used_mb?: number
    utilization_percent?: number
  }
}
export interface DeepfakeSession {
  session_id: string
  stream_path: string
  expires_in: number
  model: string
  max_width: number
}

export interface DeepfakeSessionStatus {
  session_id: string
  connected: boolean
  frame_count: number
  average_inference_ms: number
  measured_fps: number
  max_width: number
}

export interface DeepfakeImageResult {
  blob: Blob
  inferenceMs: number
}

async function multipartRequest(path: string, body: FormData): Promise<Response> {
  const token = getToken()
  const response = await fetch(`${API_CONFIG.BASE_URL}${path}`, {
    method: 'POST',
    headers: token ? { Authorization: `Bearer ${token}` } : undefined,
    body,
  })
  if (response.status === 401) {
    clearToken()
    redirectToLogin()
    throw new Error('Unauthorized')
  }
  if (!response.ok) {
    const text = await response.text().catch(() => '')
    try {
      const payload = JSON.parse(text) as { detail?: string }
      throw new Error(payload.detail || text || `HTTP ${response.status}`)
    } catch (error) {
      if (error instanceof SyntaxError) throw new Error(text || `HTTP ${response.status}`)
      throw error
    }
  }
  return response
}

export function getDeepfakeStatus(): Promise<DeepfakeStatus> {
  return apiFetch<DeepfakeStatus>('/v1/deepfake/status')
}

export async function swapDeepfakeImage(
  source: File,
  target: File,
  maxWidth = 1280,
): Promise<DeepfakeImageResult> {
  const body = new FormData()
  body.append('source', source)
  body.append('target', target)
  body.append('authorized_use', 'true')
  body.append('max_width', String(maxWidth))
  const response = await multipartRequest('/v1/deepfake/swap/image', body)
  return {
    blob: await response.blob(),
    inferenceMs: Number(response.headers.get('x-inference-ms') || 0),
  }
}

export async function createDeepfakeSession(
  source: File,
  maxWidth: number,
): Promise<DeepfakeSession> {
  const body = new FormData()
  body.append('source', source)
  body.append('authorized_use', 'true')
  body.append('max_width', String(maxWidth))
  const response = await multipartRequest('/v1/deepfake/sessions', body)
  return (await response.json()) as DeepfakeSession
}

export function getDeepfakeSession(sessionId: string): Promise<DeepfakeSessionStatus> {
  return apiFetch<DeepfakeSessionStatus>(`/v1/deepfake/sessions/${encodeURIComponent(sessionId)}`)
}

export function deleteDeepfakeSession(sessionId: string): Promise<{ deleted: boolean }> {
  return apiFetch<{ deleted: boolean }>(`/v1/deepfake/sessions/${encodeURIComponent(sessionId)}`, {
    method: 'DELETE',
  })
}

export function openDeepfakeSocket(streamPath: string): WebSocket {
  const token = getToken()
  if (!token) throw new Error('登录状态已失效')
  const url = new URL(streamPath, window.location.origin)
  url.protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:'
  return new WebSocket(url, ['sere1nfish', `sere1nfish.auth.${token}`])
}
