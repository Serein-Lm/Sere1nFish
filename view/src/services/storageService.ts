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

export function storageObjectId(pathOrId: string): string {
  const value = String(pathOrId || '').trim()
  const match = value.match(/\/objects\/([^/?#]+)\/(?:content|access)(?:[?#]|$)/)
  return decodeURIComponent(match?.[1] || value)
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
  const access = await getStorageObjectAccess(pathOrId)
  if (access.mode === 'redirect') return { url: access.url }

  const blob = await fetchBlobWithAuth(access.url, signal)
  const objectUrl = URL.createObjectURL(blob)
  return { url: objectUrl, revoke: () => URL.revokeObjectURL(objectUrl) }
}
