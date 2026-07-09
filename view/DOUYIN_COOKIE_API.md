# 抖音 Cookie 管理 API 文档

## 概述

抖音 Cookie 管理模块提供完整的 Cookie 生命周期管理功能，包括：
- 添加/更新 Cookie
- 查询 Cookie 列表和详情
- 激活/切换账号
- 验证 Cookie 有效性
- 删除 Cookie

基础路径: `/api/v1/douyin`

---

## 数据模型

### CookieOut（基本信息）

```json
{
  "id": "MongoDB ObjectId",
  "account_name": "账号名称",
  "is_active": true,
  "is_valid": true,
  "last_verified_at": "2026-01-29T10:30:00Z",
  "created_at": "2026-01-28T11:00:00Z",
  "updated_at": "2026-01-29T10:30:00Z"
}
```

| 字段 | 类型 | 说明 |
|------|------|------|
| id | string | MongoDB ObjectId |
| account_name | string | 账号名称（唯一标识） |
| is_active | boolean | 是否为当前激活账号 |
| is_valid | boolean/null | Cookie 有效性（null=未验证） |
| last_verified_at | string/null | 最后验证时间 |
| created_at | string | 创建时间 |
| updated_at | string | 更新时间 |

### CookieDetail（完整详情）

```json
{
  "id": "MongoDB ObjectId",
  "account_name": "账号名称",
  "cookie_string": "完整的 Cookie 字符串...",
  "is_active": true,
  "is_valid": true,
  "last_verified_at": "2026-01-29T10:30:00Z",
  "created_at": "2026-01-28T11:00:00Z",
  "updated_at": "2026-01-29T10:30:00Z"
}
```

---

## API 接口

### 1. 添加/更新 Cookie

```http
POST /api/v1/douyin/cookies
Content-Type: application/json

{
  "account_name": "default",
  "cookie_string": "sessionid=xxx; passport_csrf_token=xxx; ..."
}
```

**说明**: 如果账号名已存在，则更新 Cookie 字符串

**响应**: `CookieOut`

```json
{
  "id": "679f1234567890abcdef1234",
  "account_name": "default",
  "is_active": false,
  "is_valid": null,
  "last_verified_at": null,
  "created_at": "2026-01-29T10:00:00Z",
  "updated_at": "2026-01-29T10:00:00Z"
}
```

---

### 2. 列出所有 Cookie

```http
GET /api/v1/douyin/cookies?limit=50&skip=0
```

**参数**:
| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| limit | int | 50 | 返回数量 |
| skip | int | 0 | 跳过数量 |

**响应**: `CookieOut[]`

```json
[
  {
    "id": "679f1234567890abcdef1234",
    "account_name": "default",
    "is_active": true,
    "is_valid": true,
    "last_verified_at": "2026-01-29T10:30:00Z",
    "created_at": "2026-01-28T11:00:00Z",
    "updated_at": "2026-01-29T10:30:00Z"
  },
  {
    "id": "679f5678901234abcdef5678",
    "account_name": "backup",
    "is_active": false,
    "is_valid": null,
    "last_verified_at": null,
    "created_at": "2026-01-29T09:00:00Z",
    "updated_at": "2026-01-29T09:00:00Z"
  }
]
```

---

### 3. 获取账号基本信息

```http
GET /api/v1/douyin/cookies/{account_name}
```

**说明**: 不返回 cookie_string，用于列表展示

**响应**: `CookieOut`

**错误**:
- `404`: 账号不存在

---

### 4. 获取账号详情（含 Cookie 字符串）

```http
GET /api/v1/douyin/cookies/{account_name}/detail
```

**说明**: 返回完整信息，包含 cookie_string

**响应**: `CookieDetail`

```json
{
  "id": "679f1234567890abcdef1234",
  "account_name": "default",
  "cookie_string": "sessionid=xxx; passport_csrf_token=xxx; ...",
  "is_active": true,
  "is_valid": true,
  "last_verified_at": "2026-01-29T10:30:00Z",
  "created_at": "2026-01-28T11:00:00Z",
  "updated_at": "2026-01-29T10:30:00Z"
}
```

**错误**:
- `404`: 账号不存在

---

### 5. 更新账号

```http
PUT /api/v1/douyin/cookies/{account_name}
Content-Type: application/json

{
  "cookie_string": "新的 Cookie 字符串...",
  "is_active": true,
  "new_account_name": "new_name"
}
```

**请求体**（所有字段可选）:
| 字段 | 类型 | 说明 |
|------|------|------|
| cookie_string | string | 新的 Cookie 字符串 |
| is_active | boolean | 激活状态 |
| new_account_name | string | 新账号名（重命名） |

**响应**: `CookieOut`

**错误**:
- `404`: 账号不存在
- `400`: 新账号名已存在

---

### 6. 激活 Cookie

```http
POST /api/v1/douyin/cookies/{account_name}/activate
```

**说明**: 
- 激活指定账号
- 自动取消其他账号的激活状态
- 截图和视觉分析接口会使用激活的 Cookie

**响应**: `CookieOut`

```json
{
  "id": "679f1234567890abcdef1234",
  "account_name": "default",
  "is_active": true,
  "is_valid": true,
  "last_verified_at": "2026-01-29T10:30:00Z",
  "created_at": "2026-01-28T11:00:00Z",
  "updated_at": "2026-01-29T10:35:00Z"
}
```

**错误**:
- `404`: 账号不存在

---

### 7. 验证 Cookie 有效性

```http
POST /api/v1/douyin/cookies/{account_name}/verify
```

**说明**: 
- 通过访问抖音页面测试 Cookie 是否有效
- 会启动浏览器进行实际访问
- 验证结果会更新到数据库

**响应**: `CookieOut`

```json
{
  "id": "679f1234567890abcdef1234",
  "account_name": "default",
  "is_active": true,
  "is_valid": true,
  "last_verified_at": "2026-01-29T10:40:00Z",
  "created_at": "2026-01-28T11:00:00Z",
  "updated_at": "2026-01-29T10:40:00Z"
}
```

**错误**:
- `404`: 账号不存在
- `400`: Cookie 为空

---

### 8. 删除 Cookie

```http
DELETE /api/v1/douyin/cookies/{account_name}
```

**响应**:

```json
{
  "ok": true
}
```

**错误**:
- `404`: 账号不存在

---

## 使用流程

### 典型使用流程

```
1. 添加 Cookie
   POST /api/v1/douyin/cookies
   {"account_name": "my_account", "cookie_string": "..."}

2. 激活 Cookie
   POST /api/v1/douyin/cookies/my_account/activate

3. （可选）验证 Cookie
   POST /api/v1/douyin/cookies/my_account/verify

4. 使用截图/视觉分析接口
   POST /api/v1/douyin/screenshot/stream
   POST /api/v1/douyin/vision-analysis/stream
   （自动使用激活的 Cookie）
```

### 多账号管理

```
1. 添加多个账号
   POST /api/v1/douyin/cookies {"account_name": "account1", ...}
   POST /api/v1/douyin/cookies {"account_name": "account2", ...}

2. 查看所有账号
   GET /api/v1/douyin/cookies

3. 切换激活账号
   POST /api/v1/douyin/cookies/account2/activate

4. 当 Cookie 失效时更新
   PUT /api/v1/douyin/cookies/account1
   {"cookie_string": "新的 Cookie..."}
```

---

## 获取抖音 Cookie

### 方法一：浏览器开发者工具

1. 打开 Chrome 浏览器，访问 https://www.douyin.com
2. 登录抖音账号
3. 按 F12 打开开发者工具
4. 切换到 Network（网络）标签
5. 刷新页面
6. 点击任意请求，在 Headers 中找到 Cookie
7. 复制完整的 Cookie 字符串

### 方法二：浏览器扩展

使用 Cookie 导出扩展（如 EditThisCookie）直接导出

### 重要字段

抖音 Cookie 中的关键字段：
- `sessionid` - 会话 ID
- `passport_csrf_token` - CSRF Token
- `ttwid` - 设备标识
- `msToken` - 安全 Token

---

## 错误处理

| 状态码 | 说明 |
|--------|------|
| 200 | 成功 |
| 400 | 请求参数错误（如 Cookie 为空、账号名已存在） |
| 404 | 账号不存在 |
| 500 | 服务器内部错误 |

---

## 注意事项

1. **Cookie 有效期**: 抖音 Cookie 通常有效期较短，建议定期验证和更新
2. **同时只能激活一个账号**: 激活新账号会自动取消其他账号的激活状态
3. **验证会启动浏览器**: verify 接口会实际访问抖音，可能需要几秒钟
4. **Cookie 安全**: cookie_string 包含敏感信息，detail 接口需谨慎使用
