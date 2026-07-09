# Vibe Coding Log — 2026-03-30

## 概述

根据新版 `FRONTEND_API.md` 全面同步前端 API 层，重构分页机制，新增项目看板，修复多个运行时崩溃和 antd 废弃 API 警告。

---

## 1. API 层全面同步

### 分页规范统一（GET → POST + JSON Body）

所有列表接口从 `GET + query params` 改为 `POST + { page, page_size }` 统一分页：

- `projectService.ts` — `listProjects`、`listProjectWebTaggingRecords`
- `taskService.ts` — `listTasks`、`listProjectFindings`
- `douyinService.ts` — `listDouyinSearchResults`、`listDouyinTaggedResults`、`listDouyinProfiles`
- `xhsService.ts` — `listXhsNotes`、`listXhsProfiles`

### 新增 API

- **看板聚合**：`getProjectDashboard`、`getProjectTimeline`、`getProjectScoreDistribution`、`getProjectSourceBreakdown`、`getProjectTypeBreakdown`、`getProjectHighValueTargets`、`getProjectCopywritingCoverage`
- **话术生成**：`generateFindingCopywriting`（按需生成）
- **配置方法**：`getAllConfig` 路径改为 `/v1/config/all`，写入方法 POST → PUT

### 类型更新

- `XhsProfile`、`DouyinProfile` 新增 `finding_id` 字段
- `XhsNote` 新增 `keyword` 字段
- `FindingsListResponse` 改为标准分页结构 `{ items, total, page, page_size }`
- `PaginatedRequest` / `PaginatedResponse<T>` 通用分页类型

### 端点配置（api.ts）

新增看板、原始数据、话术生成等端点常量。

---

## 2. 项目详情页重构（ProjectDetail.tsx）

### 看板

- 初版：独立 dashboard Tab，包含 9 个卡片（数据源分布、分数分布、任务状态、数据统计、类型分布、高分 Top10 等）
- 最终版：移出 Tab，放在基本信息下方做精简统计条（发现、高分、任务、话术、笔记、画像、打标、数据源），默认 Tab 改回 website

### 服务端分页

7 个列表全部改为真正的服务端分页（翻页时重新请求后端）：
- Web Tagging 记录
- 小红书笔记 / 画像
- 抖音搜索 / 打标 / 画像
- 任务列表

每个表格的 `pagination` 配置了 `total`（从后端取）、`onChange` 回调（触发重新请求）。

### 话术按钮

- 小红书画像表格操作列：新增"话术"按钮（当 `finding_id` 存在时显示）
- 抖音画像表格操作列：同上
- 新增 `handleViewCopywritingById` 通用方法

### 小红书笔记关键词列

笔记表格新增"关键词"列，展示 `keyword` 字段。

---

## 3. Bug 修复

| 问题 | 原因 | 修复 |
|------|------|------|
| 整个应用白屏 | 重写 `projectService.ts` 时遗漏了 `createWebTagging`、`createCompanyWebTagging` 导出 | 补回两个函数 |
| CopywritingRenderer 崩溃 | `data.scripts[0]` 在 scripts 为 undefined 时报错 | 改为 `data.scripts?.[0]`，所有数组访问加 `?? []` |
| 小红书图片 403 | xhscdn.com Referer 防盗链 | `index.html` 添加 `<meta name="referrer" content="no-referrer" />` |

---

## 4. Antd v5 废弃 API 迁移

| 废弃用法 | 新用法 |
|----------|--------|
| `valueStyle={{ ... }}` | `styles={{ content: { ... } }}` |
| `bodyStyle={{ ... }}` | `styles={{ body: { ... } }}` |
| `destroyOnClose` | `destroyOnHidden` |
| `bordered={false}` | `variant="filled"` |
| `Drawer width={720}` | `Drawer size="large"` |
| `Descriptions.Item span={3}` | `span={2}`（匹配 column 配置） |

---

## 涉及文件

```
src/config/api.ts
src/services/projectService.ts
src/services/taskService.ts
src/services/douyinService.ts
src/services/xhsService.ts
src/services/configService.ts
src/pages/ProjectDetail/ProjectDetail.tsx
src/pages/ProjectDetail/ProjectDetail.css
src/pages/ProjectManagement/ProjectManagement.tsx
src/pages/TaskDetail/TaskDetail.tsx
src/components/CopywritingRenderer/CopywritingRenderer.tsx
index.html
```
