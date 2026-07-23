import { API_CONFIG } from '../config/api'
import { redirectToLogin } from '../utils/authNavigation'

const pendingGetRequests = new Map<string, Promise<unknown>>()
const GET_DEDUP_GRACE_MS = 500

export function getToken(): string | null {
  return localStorage.getItem('token')
}

export function clearToken(): void {
  localStorage.removeItem('token')
  localStorage.removeItem('userInfo')
}

export async function apiFetchResponse(
  path: string,
  init: RequestInit = {},
): Promise<Response> {
  const headers = new Headers(init.headers)
  const token = getToken()
  if (token) headers.set('Authorization', `Bearer ${token}`)

  const response = await fetch(`${API_CONFIG.BASE_URL}${path}`, {
    ...init,
    headers,
  })
  if (response.status === 401) {
    clearToken()
    redirectToLogin()
    throw new Error('Unauthorized')
  }
  if (!response.ok) {
    const contentType = response.headers.get('content-type') || ''
    if (contentType.includes('application/json')) {
      const payload = await response.json().catch(() => null) as {
        detail?: string | Array<{ msg?: string }>
      } | null
      const detail = payload?.detail
      if (typeof detail === 'string') throw new Error(detail)
      if (Array.isArray(detail)) {
        const message = detail.map(item => item.msg).filter(Boolean).join('；')
        throw new Error(message || `HTTP ${response.status}`)
      }
    }
    const text = await response.text().catch(() => '')
    throw new Error(text || `HTTP error! status: ${response.status}`)
  }
  return response
}

export function apiFetch<T>(
  path: string,
  init: RequestInit = {}
): Promise<T> {
  const method = String(init.method || 'GET').toUpperCase()
  const headers = new Headers(init.headers)
  headers.set('Content-Type', 'application/json')

  const token = getToken()
  if (token) {
    headers.set('Authorization', `Bearer ${token}`)
  }

  const execute = async (): Promise<T> => {
    const response = await fetch(`${API_CONFIG.BASE_URL}${path}`, {
      ...init,
      headers,
    })

    if (response.status === 401) {
      clearToken()
      redirectToLogin()
      throw new Error('Unauthorized')
    }

    if (!response.ok) {
      const text = await response.text().catch(() => '')
      throw new Error(text || `HTTP error! status: ${response.status}`)
    }

    const contentType = response.headers.get('content-type')
    if (contentType?.includes('application/json')) {
      return (await response.json()) as T
    }

    return (await response.text()) as T
  }

  if (method !== 'GET' || init.signal || init.body) return execute()

  const cacheKey = `${token || 'anonymous'}:${path}`
  const pending = pendingGetRequests.get(cacheKey) as Promise<T> | undefined
  if (pending) return pending
  const request = execute()
  pendingGetRequests.set(cacheKey, request)
  void request.then(
    () => {
      globalThis.setTimeout(() => {
        if (pendingGetRequests.get(cacheKey) === request) {
          pendingGetRequests.delete(cacheKey)
        }
      }, GET_DEDUP_GRACE_MS)
    },
    () => pendingGetRequests.delete(cacheKey),
  )
  return request
}

function resolveAuthenticatedResourceUrl(pathOrUrl: string): string {
  const url = new URL(pathOrUrl, window.location.origin)
  if (url.origin !== window.location.origin) {
    throw new Error('资源地址必须使用本站鉴权接口')
  }
  const path = `${url.pathname}${url.search}${url.hash}`
  if (url.pathname.startsWith('/api/')) return path
  return `${API_CONFIG.BASE_URL}${path.startsWith('/') ? path : `/${path}`}`
}

export async function fetchBlobWithAuth(
  pathOrUrl: string,
  signal?: AbortSignal,
): Promise<Blob> {
  const headers = new Headers()
  const token = getToken()
  if (token) headers.set('Authorization', `Bearer ${token}`)

  const response = await fetch(resolveAuthenticatedResourceUrl(pathOrUrl), {
    headers,
    signal,
    cache: 'no-store',
  })
  if (response.status === 401) {
    clearToken()
    redirectToLogin()
    throw new Error('Unauthorized')
  }
  if (!response.ok) {
    const text = await response.text().catch(() => '')
    throw new Error(text || `HTTP error! status: ${response.status}`)
  }
  return response.blob()
}

function resolveAuthenticatedDownloadUrl(pathOrUrl: string): string {
  const url = new URL(pathOrUrl, window.location.origin)
  if (url.origin !== window.location.origin) {
    throw new Error('下载地址必须是本站鉴权接口')
  }
  if (
    !url.pathname.startsWith('/api/v1/downloads/') &&
    !url.pathname.startsWith('/api/v1/artifacts/')
  ) {
    throw new Error('下载地址不在允许的鉴权下载接口内')
  }
  return `${url.pathname}${url.search}${url.hash}`
}

function filenameFromDisposition(disposition: string | null, fallback: string): string {
  if (!disposition) return fallback
  const utf8Match = disposition.match(/filename\*=UTF-8''([^;]+)/i)
  if (utf8Match?.[1]) return decodeURIComponent(utf8Match[1])
  const plainMatch = disposition.match(/filename="?([^";]+)"?/i)
  return plainMatch?.[1] || fallback
}

export async function downloadWithAuth(pathOrUrl: string, fallbackFilename = 'download'): Promise<void> {
  const token = getToken()
  if (!token) {
    redirectToLogin()
    throw new Error('Unauthorized')
  }

  const authenticatedPath = new URL(resolveAuthenticatedDownloadUrl(pathOrUrl), window.location.origin)
  authenticatedPath.searchParams.set('direct', 'true')
  const response = await fetch(`${authenticatedPath.pathname}${authenticatedPath.search}`, {
    headers: {
      Authorization: `Bearer ${token}`,
    },
  })

  if (response.status === 401) {
    clearToken()
    redirectToLogin()
    throw new Error('Unauthorized')
  }
  if (!response.ok) {
    const text = await response.text().catch(() => '')
    throw new Error(text || `HTTP error! status: ${response.status}`)
  }

  if (response.headers.get('content-type')?.includes('application/json')) {
    const payload = await response.json() as { url?: string; filename?: string }
    if (!payload.url) {
      throw new Error('对象存储未返回下载地址')
    }
    const directUrl = new URL(payload.url)
    if (directUrl.protocol !== 'https:') {
      throw new Error('对象存储下载地址必须使用 HTTPS')
    }
    const link = document.createElement('a')
    link.href = directUrl.toString()
    link.download = payload.filename || fallbackFilename
    link.rel = 'noopener noreferrer'
    document.body.appendChild(link)
    link.click()
    link.remove()
    return
  }

  const blob = await response.blob()
  const filename = filenameFromDisposition(response.headers.get('content-disposition'), fallbackFilename)
  const objectUrl = URL.createObjectURL(blob)
  const link = document.createElement('a')
  link.href = objectUrl
  link.download = filename
  document.body.appendChild(link)
  link.click()
  link.remove()
  window.setTimeout(() => URL.revokeObjectURL(objectUrl), 1000)
}
