import { apiFetch } from './http'

export interface RemoteMediaOutputSession {
  session_id: string
  viewer_path: string
  viewer_token: string
  created_at: number
  expires_at: number
  viewer_count: number
  video_frames: number
  audio_chunks: number
  last_published_at?: number | null
}

export function createRemoteMediaOutput(
  ttlSeconds = 8 * 3600,
): Promise<RemoteMediaOutputSession> {
  return apiFetch<RemoteMediaOutputSession>('/v1/media-output/sessions', {
    method: 'POST',
    body: JSON.stringify({ ttl_seconds: ttlSeconds }),
  })
}

export function getRemoteMediaOutput(
  sessionId: string,
): Promise<Omit<RemoteMediaOutputSession, 'viewer_token'>> {
  return apiFetch(`/v1/media-output/sessions/${encodeURIComponent(sessionId)}`)
}

export function deleteRemoteMediaOutput(
  sessionId: string,
  keepalive = false,
): Promise<{ ok: boolean; session_id: string }> {
  return apiFetch(`/v1/media-output/sessions/${encodeURIComponent(sessionId)}`, {
    method: 'DELETE',
    keepalive,
  })
}

export function remoteMediaOutputViewerUrl(
  session: Pick<RemoteMediaOutputSession, 'viewer_path' | 'viewer_token'>,
): string {
  const url = new URL(session.viewer_path, window.location.origin)
  url.hash = session.viewer_token
  return url.toString()
}
