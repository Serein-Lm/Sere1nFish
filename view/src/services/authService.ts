import { apiFetch, clearToken } from './http'

export interface LoginRequest {
  username: string
  password: string
  key: string
}

export interface LoginResponse {
  access_token: string
  token_type: string
  server_token: string | null
}

export interface UserPermissions {
  system_management: boolean
  user_management: boolean
}

export interface CurrentUser {
  username: string
  role: 'admin' | 'user'
  is_admin: boolean
  disabled: boolean
  permissions: UserPermissions
}

export interface UserInfo {
  username: string
  role: 'admin' | 'user'
  disabled: boolean
}

export interface CreateUserRequest {
  username: string
  password: string
  role: 'admin' | 'user'
}

export interface UpdateUserRequest {
  new_username?: string
  password?: string
  role?: 'admin' | 'user'
  disabled?: boolean
}

export interface ChangePasswordRequest {
  old_password: string
  new_password: string
}

export interface ChangeLoginKeyRequest {
  old_key: string
  new_key: string
}

export async function login(request: LoginRequest): Promise<LoginResponse> {
  return apiFetch<LoginResponse>('/v1/auth/login', {
    method: 'POST',
    body: JSON.stringify(request),
  })
}

export async function logout(): Promise<{ status: string }> {
  try {
    return await apiFetch<{ status: string }>('/v1/auth/logout', {
      method: 'POST',
    })
  } finally {
    clearToken()
  }
}

export async function getCurrentUser(): Promise<CurrentUser> {
  return apiFetch<CurrentUser>('/v1/auth/me', {
    method: 'GET',
  })
}

export async function checkAuth(): Promise<boolean> {
  try {
    const user = await getCurrentUser()
    if (user.disabled) {
      clearToken()
      return false
    }
    localStorage.setItem('userInfo', JSON.stringify(user))
    return true
  } catch {
    return false
  }
}

// ============ 用户管理 API（仅管理员） ============

export async function listUsers(): Promise<{ users: UserInfo[] }> {
  return apiFetch<{ users: UserInfo[] }>('/v1/auth/users', {
    method: 'GET',
  })
}

export async function createUser(request: CreateUserRequest): Promise<{ status: string; user: UserInfo }> {
  return apiFetch<{ status: string; user: UserInfo }>('/v1/auth/users', {
    method: 'POST',
    body: JSON.stringify(request),
  })
}

export async function updateUser(username: string, request: UpdateUserRequest): Promise<{ status: string; user: UserInfo }> {
  return apiFetch<{ status: string; user: UserInfo }>(`/v1/auth/users/${encodeURIComponent(username)}`, {
    method: 'PUT',
    body: JSON.stringify(request),
  })
}

export async function deleteUser(username: string): Promise<{ status: string; message: string }> {
  return apiFetch<{ status: string; message: string }>(`/v1/auth/users/${encodeURIComponent(username)}`, {
    method: 'DELETE',
  })
}

// ============ 密码管理 API ============

export async function changePassword(request: ChangePasswordRequest): Promise<{ status: string; message: string }> {
  return apiFetch<{ status: string; message: string }>('/v1/auth/change-password', {
    method: 'POST',
    body: JSON.stringify(request),
  })
}

// ============ 登录 Key 管理 API（仅管理员） ============

export async function getLoginKey(): Promise<{ key: string }> {
  return apiFetch<{ key: string }>('/v1/auth/login-key', {
    method: 'GET',
  })
}

export async function changeLoginKey(request: ChangeLoginKeyRequest): Promise<{ status: string; message: string }> {
  return apiFetch<{ status: string; message: string }>('/v1/auth/change-login-key', {
    method: 'POST',
    body: JSON.stringify(request),
  })
}
