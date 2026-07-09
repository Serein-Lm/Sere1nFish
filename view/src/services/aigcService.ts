import { apiFetch } from './http'

export interface BailianConfigStatus {
  provider: string
  configured: boolean
  region: string
  has_api_key: boolean
  has_workspace_id: boolean
  qwen_image_edit_model: string
  wanx_image_edit_model: string
  text_to_video_model: string
  image_to_video_model: string
}

export interface BailianTaskResp {
  ok: boolean
  provider: string
  mode?: string
  model?: string
  task_protocol?: string
  payload_protocol?: string
  task_id?: string
  task_status?: string
  images?: string[]
  result_urls?: string[]
  video_url?: string
  response?: Record<string, unknown>
}

export interface QwenImageEditReq {
  images: string[]
  prompt: string
  model?: string
  parameters?: Record<string, unknown>
}

export interface WanxImageEditReq {
  base_image_url: string
  prompt: string
  function?: string
  mask_image_url?: string
  model?: string
  parameters?: Record<string, unknown>
  extra_input?: Record<string, unknown>
}

export interface TextToVideoReq {
  prompt: string
  negative_prompt?: string
  audio_url?: string
  model?: string
  parameters?: Record<string, unknown>
}

export interface ImageToVideoReq {
  img_url?: string
  image_url?: string
  last_frame_url?: string
  first_clip_url?: string
  media?: Array<{ type: string; url: string }>
  prompt?: string
  negative_prompt?: string
  audio_url?: string
  template?: string
  model?: string
  parameters?: Record<string, unknown>
  protocol?: 'workspace' | 'legacy' | 'auto'
}

export const getBailianConfig = () =>
  apiFetch<BailianConfigStatus>('/v1/aigc/config')

export const qwenImageEdit = (body: QwenImageEditReq) =>
  apiFetch<BailianTaskResp>('/v1/aigc/images/qwen-edit', {
    method: 'POST',
    body: JSON.stringify(body),
  })

export const wanxImageEdit = (body: WanxImageEditReq) =>
  apiFetch<BailianTaskResp>('/v1/aigc/images/wanx-edit', {
    method: 'POST',
    body: JSON.stringify(body),
  })

export const textToVideo = (body: TextToVideoReq) =>
  apiFetch<BailianTaskResp>('/v1/aigc/videos/text-to-video', {
    method: 'POST',
    body: JSON.stringify(body),
  })

export const imageToVideo = (body: ImageToVideoReq) =>
  apiFetch<BailianTaskResp>('/v1/aigc/videos/image-to-video', {
    method: 'POST',
    body: JSON.stringify(body),
  })

export const queryBailianTask = (taskId: string, protocol: 'workspace' | 'legacy' | 'auto' = 'auto') =>
  apiFetch<BailianTaskResp>(`/v1/aigc/tasks/${encodeURIComponent(taskId)}?protocol=${protocol}`)
