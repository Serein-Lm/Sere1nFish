# AI Agent API 文档（前端对接）

Base URL：`http://127.0.0.1:8000`

鉴权方式：
- 先调用登录接口获取 `access_token`
- 后续所有受保护接口在请求头加入：
  - `Authorization: Bearer <access_token>`

---

## 1. 认证

### 1.1 登录（签发 Token）

- **Method**: `POST`
- **Path**: `/api/auth/login`
- **Auth**: 无
- **Body (JSON)**:

```json
{
  "username": "admin",
  "password": "admin123",
  "key": "CHANGE_ME"
}
```

- **Response 200**:

```json
{
  "access_token": "<server_token>",
  "token_type": "bearer",
  "server_token": null
}
```

说明：`key` 会与后端配置 `LOGIN_KEY` 比较。

- **[本地开发]** 在项目根目录 `.env` 中设置：

```bash
LOGIN_KEY=your_login_key
```

- **Response 401**:

```json
{
  "detail": "用户名或密码错误"
}
```

### 1.2 登出（撤销 Token）

- **Method**: `POST`
- **Path**: `/api/auth/logout`
- **Auth**: 需要
- **Headers**:
  - `Authorization: Bearer <access_token>`

- **Response 200**:

```json
{
  "status": "ok"
}
```

---

## 2. 项目（Projects）

说明：项目路由已调整为更简洁的风格：统一使用 `/api/projects` 作为资源根路径。

### 2.1 创建项目

- **Method**: `POST`
- **Path**: `/api/projects`
- **Auth**: 需要
- **Body (JSON)**:

```json
{
  "name": "project-name",
  "description": "optional"
}
```

- **Response 200**:

```json
{
  "id": "<project_id>",
  "name": "project-name",
  "description": "optional",
  "created_at": "2026-01-01T00:00:00+00:00",
  "updated_at": "2026-01-01T00:00:00+00:00"
}
```

### 2.2 获取项目列表

- **Method**: `GET`
- **Path**: `/api/projects?limit=50&skip=0`
- **Auth**: 需要

- **Response 200**:

```json
[
  {
    "id": "<project_id>",
    "name": "project-name",
    "description": null,
    "created_at": "...",
    "updated_at": "..."
  }
]
```

### 2.3 获取单个项目

- **Method**: `GET`
- **Path**: `/api/projects/{project_id}`
- **Auth**: 需要

- **Response 200**: 同 2.1

---

## 3. Web Tagging（官网社工打标）

### 3.1 执行 Web Tagging（URL 输入）

- **Method**: `POST`
- **Path**: `/api/projects/web-tagging`
- **Auth**: 需要
- **Body (JSON)**:

```json
{
  "project_id": "<project_id>",
  "url": "https://example.com"
}
```

- **Response 200**:

```json
{
  "id": "<record_id>",
  "project_id": "<project_id>",
  "url": "https://example.com",
  "created_at": "2026-01-01T00:00:00+00:00",
  "data": {
    "input": {"url": "https://example.com"},
    "site_info": {
      "final_url": "https://example.com",
      "domain": "example.com",
      "site_name": null,
      "brand_name": null,
      "entity_name": null,
      "site_type": "official_site",
      "summary": null,
      "keywords": [],
      "languages": [],
      "countries_or_regions": []
    },
    "online_customer_service": {
      "has_online_customer_service": false,
      "entries": []
    },
    "contacts": {
      "official": [],
      "personal": [],
      "enterprise_wechat": [],
      "group": []
    },
    "social_accounts": [],
    "exposed_endpoints": [],
    "tags": [],
    "sources": [],
    "notes": []
  }
}
```

- **Response 404**:

```json
{
  "detail": "项目不存在"
}
```

- **Response 502**:

```json
{
  "detail": "Agent 未返回可解析内容"
}
```

或：

```json
{
  "detail": "结构化输出校验失败: ..."
}
```

### 3.2 执行 Web Tagging（公司名输入 -> URL 预处理）

- **Method**: `POST`
- **Path**: `/api/projects/company/web-tagging`
- **Auth**: 需要
- **Body (JSON)**:

```json
{
  "project_id": "<project_id>",
  "company_name": "example.com"
}
```

- **Response**: 同 3.1

### 3.3 查询项目下 Web Tagging 记录

- **Method**: `GET`
- **Path**: `/api/projects/{project_id}/web-tagging?limit=50&skip=0`
- **Auth**: 需要

- **Response 200**:

```json
[
  {
    "id": "<record_id>",
    "project_id": "<project_id>",
    "url": "https://example.com",
    "created_at": "...",
    "data": {"...": "..."}
  }
]
```

---

## 4. 其它

### 4.1 健康检查

- **Method**: `GET`
- **Path**: `/health`
- **Auth**: 无

- **Response 200**:

```json
{
  "status": "ok"
}
```
