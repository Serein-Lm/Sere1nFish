# 项目管理 API 文档（前端对接）

Base URL：`http://127.0.0.1:8000`

鉴权方式：

- 先调用登录接口获取 `access_token`
- 后续所有受保护接口在请求头加入：

```http
Authorization: Bearer <access_token>
```

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
  "key": "accesskey"
}
```

- **Response 200**:

```json
{
  "access_token": "<token>",
  "token_type": "bearer",
  "server_token": null
}
```

说明：`key` 会与后端配置 `LOGIN_KEY` 比较。

- **Response 401**:

```json
{
  "detail": "用户名或密码错误"
}
```

### 1.2 校验 Token / 获取当前用户（前端路由守卫）

- **Method**: `GET`
- **Path**: `/api/auth/me`
- **Auth**: 需要（Bearer Token）

- **Response 200**:

```json
{
  "username": "admin",
  "disabled": false
}
```

- **Response 401**:

```json
{
  "detail": "无效的认证凭证"
}
```

---

## 2. 项目（Projects）

说明：项目路由统一使用 `/api/projects` 作为资源根路径。

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

- **Response 404**:

```json
{
  "detail": "项目不存在"
}
```

### 2.4 更新项目（部分更新）

- **Method**: `PATCH`
- **Path**: `/api/projects/{project_id}`
- **Auth**: 需要
- **Body (JSON)**：以下字段均可选（至少提供一个）

```json
{
  "name": "new-name",
  "description": "new description"
}
```

- **Response 200**: 同 2.1

- **Response 404**:

```json
{
  "detail": "项目不存在"
}
```

### 2.5 删除项目

- **Method**: `DELETE`
- **Path**: `/api/projects/{project_id}?cascade=false`
- **Auth**: 需要

参数：

- `cascade`：是否级联删除该项目下所有 Web Tagging 记录（默认 `false`）

- **Response 200**:

```json
{
  "ok": true
}
```

- **Response 404**:

```json
{
  "detail": "项目不存在"
}
```

### 2.6 为项目追加内容（增量）并设置/更新目标

- **Method**: `POST`
- **Path**: `/api/projects/{project_id}/append`
- **Auth**: 需要
- **Body (JSON)**:

```json
{
  "target": "对该公司进行社工攻击面评估并生成可执行的风险清单",
  "content": "WebTagging: https://www.datacanvas.com/"
}
```

说明：

- `content` 会以“追加（append）”方式写入项目的 `contents` 列表，用于记录后续要跑的任务/输入。
- `target` 可选；当提供时会覆盖/更新项目的 `target` 字段（用于后续流程）。

- **Response 200**:

```json
{
  "id": "<project_id>",
  "name": "project-name",
  "description": "optional",
  "target": "...",
  "contents": [
    "WebTagging: https://www.datacanvas.com/"
  ],
  "created_at": "...",
  "updated_at": "..."
}
```

- **Response 404**:

```json
{
  "detail": "项目不存在"
}
```

---

## 3. Web Tagging（官网社工打标）

### 3.1 执行 Web Tagging（URL 输入，入库）

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
    "intro": {
      "url": "https://example.com",
      "final_url": "https://example.com/",
      "domain": "example.com",
      "site_name": null,
      "entity_name": null,
      "summary": null
    },
    "has_findings": true,
    "no_findings_reason": null,
    "findings": [
      {
        "type": "customer_service",
        "scope": "enterprise",
        "channel": "link",
        "role": "customer_service",
        "subtype": "live_chat_third_party",
        "label": "在线咨询",
        "value": "https://...",
        "context": "在线客服入口（第三方）...",
        "source_url": "https://example.com",
        "evidence": "页面显示：在线咨询 ...",
        "attention_score": 65,
        "attention_reason": "属于可直接触达的实时沟通入口..."
      }
    ]
  }
}
```

说明：

- `data` 字段为结构化输出（见下方《Web Tagging 字段映射》）。
- 服务端返回时会尝试将历史旧结构数据自动升级为新结构，保证返回结构稳定。

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

### 3.2 执行 Web Tagging（公司名输入 -> URL 预处理，入库）

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

- **Response 400**:

```json
{
  "detail": "无法从 company_name 解析出 URL/域名，请直接提供 URL"
}
```

### 3.3 查询项目下 Web Tagging 记录（从数据库读取）

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
    "created_at": "2026-01-01T00:00:00+00:00",
    "data": {
      "intro": {
        "url": "https://example.com",
        "final_url": "https://example.com/",
        "domain": "example.com",
        "site_name": null,
        "entity_name": null,
        "summary": null
      },
      "has_findings": false,
      "no_findings_reason": "...",
      "findings": []
    }
  }
]
```

---

## Web Tagging 字段映射（Field Map）

以下内容来自 `WEB_TAGGING_FIELD_MAP.md`，用于前端渲染枚举映射。

## 1. 顶层结构

- `intro`
  - `url`：输入 URL
  - `final_url`：最终落地 URL（如有重定向）
  - `domain`：主域名
  - `site_name`：站点名称（可为空）
  - `entity_name`：主体名称（可为空）
  - `summary`：一句话介绍（可为空）

- `has_findings`：是否存在社工攻击面信息
- `no_findings_reason`：当 `has_findings=false` 时的原因（中文）
- `findings`：社工攻击面条目数组

## 2. finding 字段（前端渲染建议）

每条 finding 字段：

- `type`：大类（主分类，用于一级分组/颜色/icon）
- `scope`：归属范围（官方/私人/企业）
- `channel`：触达渠道（邮箱/电话/微信/链接/表单…）
- `role`：业务角色（HR/商务/媒体/客服…）
- `subtype`：细分类型（string，可为空；用于二级标签/细分 icon）
- `label`：页面原始标签文案（可为空）
- `value`：具体值（可为空）
- `context`：上下文（中文）
- `source_url`：来源页面
- `evidence`：证据（中文，<=120字）
- `attention_score`：关注度分（0-100，仅该条）
- `attention_reason`：评分理由（中文）

## 3. 枚举映射表（英文 -> 中文）

### 3.1 type（大类）

- `personal_mobile`：个人手机号
- `personal_email`：个人邮箱
- `personal_wechat`：个人微信（号/二维码）
- `enterprise_wechat`：企业微信（客服/二维码/添加入口）
- `hr_contact`：招聘/HR 联系方式
- `business_contact`：商务/销售/合作联系方式
- `media_contact`：媒体/公关联系方式
- `customer_service`：客服/工单/反馈渠道
- `group_chat`：群聊/社群
- `other`：其它社工入口

### 3.2 scope（归属范围）

- `official`：官方公开渠道
- `personal`：私人信息
- `enterprise`：企业级渠道（含第三方客服/工单/反馈渠道等）

### 3.3 channel（触达渠道）

- `email`：邮箱
- `phone`：电话
- `wechat`：微信
- `link`：链接
- `form`：表单
- `other`：其它

### 3.4 role（业务角色）

- `hr`：招聘/人力
- `business`：商务合作
- `sales`：销售
- `support`：技术支持/售后支持
- `customer_service`：客服/工单/反馈
- `media`：媒体沟通
- `pr`：公关/品牌
- `other`：其它

## 4. subtype（细分类型）建议映射（string）

说明：`subtype` 不强制枚举，前端可按需映射；当遇到未知值时可按默认样式渲染。

### 4.1 客服/工单/反馈（通常 type=customer_service）

- `live_chat_third_party`：第三方在线客服/IM
- `live_chat_native`：站内在线客服（自建）
- `ticket_system`：工单系统
- `feedback_form`：反馈表单
- `support_portal`：帮助中心/支持门户
- `hotline_400`：400 热线
- `hotline_landline`：座机热线/总机
- `service_wechat`：客服微信/客服二维码

### 4.2 HR（通常 type=hr_contact）

- `resume_email`：简历投递邮箱
- `resume_phone`：招聘电话
- `job_portal`：招聘入口/招聘系统链接
- `campus_recruit`：校招入口

### 4.3 商务/销售（通常 type=business_contact）

- `business_email`：商务合作邮箱
- `sales_email`：销售邮箱
- `partner_program`：渠道/生态合作入口
- `supplier_portal`：供应商入口
- `procurement_contact`：采购联系

### 4.4 媒体/公关（通常 type=media_contact）

- `media_email`：媒体邮箱
- `pr_email`：公关邮箱
- `press_contact`：新闻/媒体联系入口

### 4.5 私人信息（通常 scope=personal）

- `mobile_personal`：个人手机号
- `email_personal`：个人邮箱
- `wechat_id_personal`：个人微信号
- `wechat_qr_personal`：个人微信二维码

### 4.6 群聊/社群（通常 type=group_chat）

- `qq_group`：QQ群
- `wechat_group`：微信群
- `telegram`：Telegram
- `discord`：Discord
- `community_invite`：社群邀请链接
