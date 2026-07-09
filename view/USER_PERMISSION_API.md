# 用户权限系统 API 文档

## 概述

系统支持两种用户角色：
- **普通用户 (user)** - 基本功能访问权限
- **管理员 (admin)** - 拥有系统管理权限

## 默认账户

| 用户名 | 密码 | 角色 |
|--------|------|------|
| admin | admin123 | 管理员 |
| user | user123 | 普通用户 |

默认登录 Key: `accesskey`

---

## 通用 API

### 1. 登录

```
POST /api/v1/auth/login
```

**请求体：**
```json
{
  "username": "admin",
  "password": "admin123",
  "key": "accesskey"
}
```

**响应：**
```json
{
  "access_token": "xxx",
  "token_type": "bearer"
}
```

---

### 2. 获取当前用户信息

```
GET /api/v1/auth/me
```

**Headers：** `Authorization: Bearer {token}`

**响应：**
```json
{
  "username": "admin",
  "role": "admin",
  "is_admin": true,
  "disabled": false,
  "permissions": {
    "system_management": true,
    "user_management": true
  }
}
```

---

### 3. 登出

```
POST /api/v1/auth/logout
```

**Headers：** `Authorization: Bearer {token}`

**响应：**
```json
{
  "status": "ok"
}
```

---

### 4. 修改自己的密码

> 所有用户都可以修改自己的密码

```
POST /api/v1/auth/change-password
```

**Headers：** `Authorization: Bearer {token}`

**请求体：**
```json
{
  "old_password": "原密码",
  "new_password": "新密码"
}
```

**响应：**
```json
{
  "status": "ok",
  "message": "密码修改成功"
}
```

**错误响应（原密码错误）：**
```json
{
  "detail": "原密码错误"
}
```
HTTP Status: `400 Bad Request`

---

## 系统管理 API（仅管理员）

> ⚠️ 以下接口仅管理员可访问，普通用户调用返回 `403 Forbidden`

### 5. 获取用户列表

```
GET /api/v1/auth/users
```

**Headers：** `Authorization: Bearer {admin_token}`

**响应：**
```json
{
  "users": [
    {"username": "admin", "role": "admin", "disabled": false},
    {"username": "user", "role": "user", "disabled": false}
  ]
}
```

---

### 6. 创建用户

```
POST /api/v1/auth/users
```

**Headers：** `Authorization: Bearer {admin_token}`

**请求体：**
```json
{
  "username": "newuser",
  "password": "password123",
  "role": "user"
}
```

`role` 可选值：`"user"` | `"admin"`

**响应：**
```json
{
  "status": "ok",
  "user": {"username": "newuser", "role": "user"}
}
```

---

### 7. 更新用户

```
PUT /api/v1/auth/users/{username}
```

**Headers：** `Authorization: Bearer {admin_token}`

**请求体（所有字段可选）：**
```json
{
  "new_username": "新用户名",
  "password": "新密码",
  "role": "admin",
  "disabled": false
}
```

**响应：**
```json
{
  "status": "ok",
  "user": {"username": "新用户名", "role": "admin"}
}
```

**注意：**
- 不能修改默认管理员 `admin` 的用户名
- 新用户名不能与已有用户重复

---

### 8. 删除用户

```
DELETE /api/v1/auth/users/{username}
```

**Headers：** `Authorization: Bearer {admin_token}`

**响应：**
```json
{
  "status": "ok",
  "message": "用户 newuser 已删除"
}
```

**注意：** 不能删除默认管理员账户 `admin`

---

### 9. 获取当前登录 Key

```
GET /api/v1/auth/login-key
```

**Headers：** `Authorization: Bearer {admin_token}`

**响应：**
```json
{
  "key": "accesskey"
}
```

---

### 10. 修改登录 Key

```
POST /api/v1/auth/change-login-key
```

**Headers：** `Authorization: Bearer {admin_token}`

**请求体：**
```json
{
  "old_key": "原key",
  "new_key": "新key"
}
```

**响应：**
```json
{
  "status": "ok",
  "message": "登录 Key 已更新"
}
```

**注意：**
- 需要验证原 Key
- 新 Key 长度至少 6 位
- 修改后所有新登录都需要使用新 Key

---

## 前端集成示例

### 判断是否显示系统管理菜单

```javascript
const response = await fetch('/api/v1/auth/me', {
  headers: { 'Authorization': `Bearer ${token}` }
});
const userInfo = await response.json();

if (userInfo.permissions.system_management) {
  showSystemManagementMenu();
} else {
  hideSystemManagementMenu();
}
```

### 用户修改密码

```javascript
async function changePassword(oldPassword, newPassword) {
  const response = await fetch('/api/v1/auth/change-password', {
    method: 'POST',
    headers: {
      'Authorization': `Bearer ${token}`,
      'Content-Type': 'application/json'
    },
    body: JSON.stringify({ old_password: oldPassword, new_password: newPassword })
  });
  
  if (response.status === 400) {
    const error = await response.json();
    alert(error.detail);  // "原密码错误"
    return false;
  }
  
  return response.ok;
}
```

### 处理 403 错误

```javascript
async function fetchUsers() {
  const response = await fetch('/api/v1/auth/users', {
    headers: { 'Authorization': `Bearer ${token}` }
  });
  
  if (response.status === 403) {
    alert('您没有权限访问此功能');
    return null;
  }
  
  return response.json();
}
```

---

## 权限对照表

| API | 普通用户 | 管理员 |
|-----|---------|--------|
| POST /login | ✅ | ✅ |
| GET /me | ✅ | ✅ |
| POST /logout | ✅ | ✅ |
| POST /change-password | ✅ | ✅ |
| GET /users | ❌ 403 | ✅ |
| POST /users | ❌ 403 | ✅ |
| PUT /users/{username} | ❌ 403 | ✅ |
| DELETE /users/{username} | ❌ 403 | ✅ |
| GET /login-key | ❌ 403 | ✅ |
| POST /change-login-key | ❌ 403 | ✅ |
