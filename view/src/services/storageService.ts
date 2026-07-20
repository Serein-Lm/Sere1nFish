import { apiFetch, fetchBlobWithAuth } from './http'

export interface StorageObjectAccess {
  object_id: string
  mode: 'redirect' | 'local'
  url: string
  content_type: string
  expires_in: number
  expires_at: string
}

interface CachedAccess {
  value: StorageObjectAccess
  expiresAt: number
}

const accessCache = new Map<string, CachedAccess>()
const pendingAccess = new Map<string, Promise<StorageObjectAccess>>()

function decodedRouteId(match: RegExpMatchArray | null): string {
  if (!match?.[1]) return ''
  try {
    return decodeURIComponent(match[1])
  } catch {
    return ''
  }
}

function isMobileScreenshotPath(value: string): boolean {
  return /\/mobile\/screenshots\/[^/?#]+\/image(?:[?#]|$)/.test(value)
}

export function storageObjectId(pathOrId: string): string {
  const value = String(pathOrId || '').trim()
  const storageRouteId = decodedRouteId(
    value.match(/\/storage\/objects\/([^/?#]+)\/(?:content|access)(?:[?#]|$)/),
  )
  if (storageRouteId) return storageRouteId
  const screenshotObjectId = decodedRouteId(
    value.match(/\/mobile\/screenshots\/([^/?#]+)\/image(?:[?#]|$)/),
  )
  if (screenshotObjectId) return screenshotObjectId
  return value && !/[/?#]/.test(value) && !value.includes('://') ? value : ''
}

export async function getStorageObjectAccess(pathOrId: string): Promise<StorageObjectAccess> {
  const objectId = storageObjectId(pathOrId)
  if (!objectId) throw new Error('缺少存储对象 ID')

  const cached = accessCache.get(objectId)
  if (cached && cached.expiresAt > Date.now() + 30_000) return cached.value

  const pending = pendingAccess.get(objectId)
  if (pending) return pending

  const request = apiFetch<StorageObjectAccess>(
    `/v1/storage/objects/${encodeURIComponent(objectId)}/access`,
  ).then((value) => {
    const ttl = Math.max(30, Number(value.expires_in) || 300)
    accessCache.set(objectId, { value, expiresAt: Date.now() + ttl * 1000 })
    return value
  }).finally(() => pendingAccess.delete(objectId))
  pendingAccess.set(objectId, request)
  return request
}

export async function resolveStorageImage(
  pathOrId: string,
  signal?: AbortSignal,
): Promise<{ url: string; revoke?: () => void }> {
  const source = String(pathOrId || '').trim()
  const objectId = storageObjectId(source)
  if (objectId) {
    try {
      const access = await getStorageObjectAccess(objectId)
      if (access.mode === 'redirect') return { url: access.url }

      const blob = await fetchBlobWithAuth(access.url, signal)
      const objectUrl = URL.createObjectURL(blob)
      return { url: objectUrl, revoke: () => URL.revokeObjectURL(objectUrl) }
    } catch (error) {
      if (!isMobileScreenshotPath(source)) throw error
    }
  }

  if (!source || (!source.startsWith('/') && !source.includes('://'))) {
    throw new Error('图片地址不是有效的鉴权资源')
  }
  const blob = await fetchBlobWithAuth(source, signal)
  const objectUrl = URL.createObjectURL(blob)
  return { url: objectUrl, revoke: () => URL.revokeObjectURL(objectUrl) }
}
