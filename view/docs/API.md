# Sere1nFish AI钓鱼中台 - 后端接入文档

## 项目概述

**项目名称**: Sere1nFish (AI钓鱼中台)  
**中文名称**: AI钓鱼中台  
**版本**: v1.0.0  
**技术栈**: React 19 + TypeScript + Ant Design + React Router

---

## 1. 认证系统

### 1.1 用户登录

**接口地址**: `POST /api/v1/login`

**请求参数**:
```json
{
  "username": "string",    // 用户名
  "password": "string",    // 密码
  "key": "string"          // 访问密钥
}
```

**响应格式**:
```json
{
  "code": 200,
  "message": "登录成功",
  "data": {
    "token": "string",     // JWT Token
    "user": {
      "id": "string",
      "username": "string",
      "email": "string",
      "role": "string",    // admin | user
      "avatar": "string"
    }
  }
}
```

**错误响应**:
```json
{
  "code": 401,
  "error": "用户名、密码或访问密钥错误",
  "data": null
}
```

### 1.2 Token 验证

**接口地址**: `GET /api/v1/auth/verify`

**请求头**:
```
Authorization: Bearer {token}
```

**响应格式**:
```json
{
  "code": 200,
  "message": "Token有效",
  "data": {
    "valid": true,
    "user": {
      "id": "string",
      "username": "string"
    }
  }
}
```

### 1.3 退出登录

**接口地址**: `POST /api/v1/logout`

**请求头**:
```
Authorization: Bearer {token}
```

**响应格式**:
```json
{
  "code": 200,
  "message": "退出成功"
}
```

---

## 2. 仪表盘数据

### 2.1 获取统计数据

**接口地址**: `GET /api/v1/dashboard/stats`

**请求头**:
```
Authorization: Bearer {token}
```

**响应格式**:
```json
{
  "code": 200,
  "data": {
    "totalCampaigns": 24,      // 总活动数
    "totalTargets": 1580,      // 目标用户数
    "successRate": 68.5,       // 成功率 (%)
    "activeCampaigns": 5,      // 进行中的活动
    "trends": {
      "campaigns": 12,         // 较上月变化 (%)
      "targets": 8,
      "successRate": -3,
      "active": 2
    }
  }
}
```

### 2.2 获取最近活动列表

**接口地址**: `GET /api/v1/dashboard/recent-campaigns`

**请求参数**:
```
page: number (默认: 1)
pageSize: number (默认: 5)
```

**响应格式**:
```json
{
  "code": 200,
  "data": {
    "list": [
      {
        "id": "string",
        "name": "string",
        "status": "进行中 | 已完成 | 计划中",
        "sent": 250,
        "opened": 180,
        "clicked": 95,
        "successRate": 72,
        "createdAt": "2024-01-01T00:00:00Z",
        "updatedAt": "2024-01-01T00:00:00Z"
      }
    ],
    "total": 100,
    "page": 1,
    "pageSize": 5
  }
}
```

---

## 3. 钓鱼活动管理

### 3.1 获取活动列表

**接口地址**: `GET /api/v1/campaigns`

**请求参数**:
```
page: number
pageSize: number
status: string (可选: 进行中 | 已完成 | 计划中)
keyword: string (可选: 搜索关键词)
```

**响应格式**:
```json
{
  "code": 200,
  "data": {
    "list": [
      {
        "id": "string",
        "name": "string",
        "description": "string",
        "status": "string",
        "templateId": "string",
        "targetGroupId": "string",
        "sent": 0,
        "opened": 0,
        "clicked": 0,
        "successRate": 0,
        "scheduledAt": "2024-01-01T00:00:00Z",
        "createdAt": "2024-01-01T00:00:00Z"
      }
    ],
    "total": 100,
    "page": 1,
    "pageSize": 10
  }
}
```

### 3.2 创建活动

**接口地址**: `POST /api/v1/campaigns`

**请求参数**:
```json
{
  "name": "string",
  "description": "string",
  "templateId": "string",
  "targetGroupId": "string",
  "scheduledAt": "2024-01-01T00:00:00Z"
}
```

### 3.3 更新活动

**接口地址**: `PUT /api/v1/campaigns/:id`

### 3.4 删除活动

**接口地址**: `DELETE /api/v1/campaigns/:id`

### 3.5 启动活动

**接口地址**: `POST /api/v1/campaigns/:id/start`

### 3.6 停止活动

**接口地址**: `POST /api/v1/campaigns/:id/stop`

---

## 4. 邮件模板管理

### 4.1 获取模板列表

**接口地址**: `GET /api/v1/templates`

**响应格式**:
```json
{
  "code": 200,
  "data": {
    "list": [
      {
        "id": "string",
        "name": "string",
        "subject": "string",
        "content": "string",
        "type": "钓鱼 | 培训 | 测试",
        "createdAt": "2024-01-01T00:00:00Z"
      }
    ]
  }
}
```

### 4.2 创建模板

**接口地址**: `POST /api/v1/templates`

**请求参数**:
```json
{
  "name": "string",
  "subject": "string",
  "content": "string",
  "type": "string"
}
```

### 4.3 更新模板

**接口地址**: `PUT /api/v1/templates/:id`

### 4.4 删除模板

**接口地址**: `DELETE /api/v1/templates/:id`

---

## 5. 目标管理

### 5.1 获取目标列表

**接口地址**: `GET /api/v1/targets`

**响应格式**:
```json
{
  "code": 200,
  "data": {
    "list": [
      {
        "id": "string",
        "name": "string",
        "email": "string",
        "department": "string",
        "position": "string",
        "groupId": "string",
        "createdAt": "2024-01-01T00:00:00Z"
      }
    ]
  }
}
```

### 5.2 批量导入目标

**接口地址**: `POST /api/v1/targets/import`

**请求参数**:
```json
{
  "targets": [
    {
      "name": "string",
      "email": "string",
      "department": "string",
      "position": "string"
    }
  ]
}
```

### 5.3 创建目标分组

**接口地址**: `POST /api/v1/target-groups`

**请求参数**:
```json
{
  "name": "string",
  "description": "string",
  "targetIds": ["string"]
}
```

---

## 6. 数据分析

### 6.1 获取活动详细数据

**接口地址**: `GET /api/v1/analytics/campaign/:id`

**响应格式**:
```json
{
  "code": 200,
  "data": {
    "campaignId": "string",
    "overview": {
      "sent": 250,
      "opened": 180,
      "clicked": 95,
      "submitted": 45,
      "reported": 10
    },
    "timeline": [
      {
        "timestamp": "2024-01-01T00:00:00Z",
        "event": "sent | opened | clicked | submitted",
        "count": 10
      }
    ],
    "targets": [
      {
        "targetId": "string",
        "email": "string",
        "sent": true,
        "opened": true,
        "clicked": false,
        "submitted": false,
        "openedAt": "2024-01-01T00:00:00Z"
      }
    ]
  }
}
```

### 6.2 获取趋势数据

**接口地址**: `GET /api/v1/analytics/trends`

**请求参数**:
```
startDate: string (YYYY-MM-DD)
endDate: string (YYYY-MM-DD)
```

---

## 7. 系统设置

### 7.1 获取系统配置

**接口地址**: `GET /api/v1/settings`

### 7.2 更新系统配置

**接口地址**: `PUT /api/v1/settings`

---

## 8. 通用规范

### 8.1 请求头

所有需要认证的接口都需要携带以下请求头:

```
Authorization: Bearer {token}
Content-Type: application/json
```

### 8.2 错误码

| 错误码 | 说明 |
|--------|------|
| 200 | 成功 |
| 400 | 请求参数错误 |
| 401 | 未授权/Token无效 |
| 403 | 无权限 |
| 404 | 资源不存在 |
| 500 | 服务器错误 |

### 8.3 分页参数

```
page: 页码 (从1开始)
pageSize: 每页数量 (默认10, 最大100)
```

### 8.4 时间格式

所有时间字段使用 ISO 8601 格式: `YYYY-MM-DDTHH:mm:ssZ`

---

## 9. 前端存储

### 9.1 LocalStorage

```javascript
// Token存储
localStorage.setItem('token', 'xxx')
localStorage.getItem('token')

// 用户信息存储
localStorage.setItem('userInfo', JSON.stringify(userInfo))
JSON.parse(localStorage.getItem('userInfo'))
```

### 9.2 路由守卫

前端已实现路由守卫，未登录用户访问受保护路由会自动跳转到登录页。

---

## 10. 开发建议

### 10.1 API 基础配置

建议在前端创建统一的 API 请求封装:

```typescript
// src/utils/request.ts
import axios from 'axios'

const request = axios.create({
  baseURL: '/api/v1',
  timeout: 10000,
})

request.interceptors.request.use(config => {
  const token = localStorage.getItem('token')
  if (token) {
    config.headers.Authorization = `Bearer ${token}`
  }
  return config
})

request.interceptors.response.use(
  response => response.data,
  error => {
    if (error.response?.status === 401) {
      localStorage.removeItem('token')
      window.location.href = '/login'
    }
    return Promise.reject(error)
  }
)

export default request
```

### 10.2 环境变量

```env
# .env.development
VITE_API_BASE_URL=http://localhost:3000/api/v1

# .env.production
VITE_API_BASE_URL=https://api.sere1nfish.com/api/v1
```

---

## 11. 联系方式

如有问题，请联系开发团队。
