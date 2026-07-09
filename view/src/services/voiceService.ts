import { apiFetch, getToken } from './http'
import { API_CONFIG } from '../config/api'

// ==================== 类型定义 ====================

export interface VoiceCreateReq {
  url: string
  prefix?: string
  language_hints?: string[]
  max_prompt_audio_length?: number
  enable_preprocess?: boolean
}

export interface VoiceCreateResp {
  voice_id: string
  model: string
  request_id: string | null
}

export interface VoiceUpdateReq {
  url: string
  language_hints?: string[]
  max_prompt_audio_length?: number
  enable_preprocess?: boolean
}

export interface VoiceClone {
  voice_id: string
  model: string
  prefix: string
  url: string
  language_hints: string[]
  status: string
  request_id: string | null
  created_at: number
  updated_at: number
}

export interface VoiceDetail {
  local: VoiceClone | null
  remote: Record<string, unknown> | null
}

export interface SynthesisRecord {
  record_id: string
  voice_id: string
  text: string
  text_length: number
  model: string
  status: 'processing' | 'completed' | 'failed'
  audio_bytes: number
  first_pkg_delay_ms: number
  request_id: string | null
  error: string | null
  created_at: number
  completed_at: number | null
}

export interface PageResp<T = Record<string, unknown>> {
  items: T[]
  total: number
  page: number
  page_size: number
}

export interface SynthesizeResult {
  blob: Blob
  requestId: string | null
  recordId: string | null
  delayMs: number
}

export interface UploadResult {
  filename: string
  original_name: string
  size: number
  url: string
  relative_url?: string
}

// ==================== API 调用 ====================

export async function uploadAudio(file: File): Promise<UploadResult> {
  const token = getToken()
  const formData = new FormData()
  formData.append('file', file)

  const res = await fetch(`${API_CONFIG.BASE_URL}/v1/voice/upload`, {
    method: 'POST',
    headers: {
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
    },
    body: formData,
  })

  if (!res.ok) {
    const errText = await res.text().catch(() => '')
    throw new Error(errText || `上传失败: HTTP ${res.status}`)
  }

  return (await res.json()) as UploadResult
}

export async function createVoice(req: VoiceCreateReq): Promise<VoiceCreateResp> {
  return apiFetch<VoiceCreateResp>('/v1/voice/voices', {
    method: 'POST',
    body: JSON.stringify(req),
  })
}

export async function listVoices(params?: {
  prefix?: string
  status?: string
  page?: number
  page_size?: number
}): Promise<PageResp<VoiceClone>> {
  const query = new URLSearchParams()
  if (params?.prefix) query.set('prefix', params.prefix)
  if (params?.status) query.set('status', params.status)
  if (params?.page !== undefined) query.set('page', String(params.page))
  if (params?.page_size !== undefined) query.set('page_size', String(params.page_size))
  const qs = query.toString()
  return apiFetch<PageResp<VoiceClone>>(`/v1/voice/voices${qs ? '?' + qs : ''}`)
}

export async function getVoiceDetail(voiceId: string): Promise<VoiceDetail> {
  return apiFetch<VoiceDetail>(`/v1/voice/voices/${encodeURIComponent(voiceId)}`)
}

export async function updateVoice(
  voiceId: string,
  req: VoiceUpdateReq,
): Promise<{ ok: boolean; voice_id: string; request_id: string }> {
  return apiFetch(`/v1/voice/voices/${encodeURIComponent(voiceId)}`, {
    method: 'PUT',
    body: JSON.stringify(req),
  })
}

export async function deleteVoice(
  voiceId: string,
): Promise<{ ok: boolean; voice_id: string }> {
  return apiFetch(`/v1/voice/voices/${encodeURIComponent(voiceId)}`, {
    method: 'DELETE',
  })
}

export async function synthesizeSpeech(
  text: string,
  voiceId: string,
  model?: string,
): Promise<SynthesizeResult> {
  const token = getToken()
  const res = await fetch(`${API_CONFIG.BASE_URL}/v1/voice/synthesize`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
    },
    body: JSON.stringify({ text, voice_id: voiceId, model: model || undefined }),
  })

  if (!res.ok) {
    const errText = await res.text().catch(() => '')
    throw new Error(errText || `合成失败: HTTP ${res.status}`)
  }

  const blob = await res.blob()
  return {
    blob,
    requestId: res.headers.get('X-Request-Id'),
    recordId: res.headers.get('X-Record-Id'),
    delayMs: Number(res.headers.get('X-First-Package-Delay-Ms') || 0),
  }
}

export async function listRecords(params?: {
  voice_id?: string
  status?: string
  page?: number
  page_size?: number
}): Promise<PageResp<SynthesisRecord>> {
  const query = new URLSearchParams()
  if (params?.voice_id) query.set('voice_id', params.voice_id)
  if (params?.status) query.set('status', params.status)
  if (params?.page !== undefined) query.set('page', String(params.page))
  if (params?.page_size !== undefined) query.set('page_size', String(params.page_size))
  const qs = query.toString()
  return apiFetch<PageResp<SynthesisRecord>>(`/v1/voice/records${qs ? '?' + qs : ''}`)
}

export async function getRecord(recordId: string): Promise<SynthesisRecord> {
  return apiFetch<SynthesisRecord>(`/v1/voice/records/${encodeURIComponent(recordId)}`)
}
