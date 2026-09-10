# Skill 系统 API

## 运行模型

Skill 是数据库托管的渐进式能力，不是后端代码中的固定数组。运行时按三层披露：

```text
Layer 1: Skill 索引
  slug / name / description / tags / triggers / priority
       ↓ 只有命中场景或用户显式选择时
Layer 2: Skill 正文
  content_raw（SKILL.md 的指令正文）
       ↓ 只有正文明确需要某项资料时
Layer 3: 资源树
  references/、scripts/、模板、说明文档等资源内容
```

MongoDB 是运行时事实来源。项目内置 Skill 和 `/root/skills` 只作为导入源；同步完成后，Agent、前端和任务选择器都读取同一数据库投影。数据库中的人工编辑默认不会被后续同步覆盖。

实现边界：

- `api/routers/skills.py`：HTTP、鉴权和请求响应。
- `api/dao/skills.py`：Skill、分类和标签持久化。
- `api/dao/skill_resources.py`：资源树及内容持久化。
- `api/services/skill_library`：来源协议、文件系统 adapter、同步和选择上下文。
- `Sere1nGraph.graph.skills`：Agent 运行时索引、匹配和渐进加载。

所有接口前缀为 `/api/v1/skills`，均需登录；同步和部分治理操作要求管理员权限。

## 外部 Skill 源

默认外部目录由 `EXTERNAL_SKILLS_DIR` 指定，缺省为 `/opt/sere1nfish-skills`；该目录不存在且主机存在 `/root/skills` 时使用 `/root/skills`。

每个一级目录是一个 Skill 包，至少包含 `SKILL.md`，其余目录和文件作为资源树导入。例如：

```text
/root/skills/
├── docx/
│   ├── SKILL.md
│   ├── references/
│   └── scripts/
├── pdf/
│   ├── SKILL.md
│   ├── guides/
│   └── scripts/
├── pptx/
└── xlsx/
```

来源 adapter 只负责发现和解析 `SkillPackage`；同步 service 负责 upsert、资源替换、标签和过期项处理。后续接 Git、OSS 或远端仓库时应新增 `SkillSourceAdapter`，不能把来源判断散落到 Router 或 Agent。

## 索引与渐进读取

### `GET /api/v1/skills`

分页读取 Layer 1 索引。

常用参数：

| 参数 | 说明 |
|---|---|
| `category` | 分类筛选 |
| `tag` | 标签筛选 |
| `status` | 状态筛选 |
| `search` | 名称、描述等搜索 |
| `page` / `page_size` | 分页，`page_size <= 100` |
| `sort_by` / `sort_order` | 排序字段和 `asc/desc` |
| `include_content` | 是否携带 `content_raw`，默认 `false` |

列表默认不返回正文，这是渐进式披露和降低上下文体积的关键约束。

### `GET /api/v1/skills/detail/{skill_id}`

按 `skill_id` 或 `slug` 读取 Layer 2 详情。不存在时返回 `404`。

### `GET /api/v1/skills/detail/{skill_id}/resources`

列出指定目录的直接子项：

```text
?parent_path=
?parent_path=references
```

返回项包含稳定 `path`、`parent_path`、`kind`、`role`、`content_type`、`size` 和 `content_hash`。前端据此逐层展开树，不应一次拉取所有文件正文。

### `GET /api/v1/skills/detail/{skill_id}/resources/content?path=...`

读取一个文本资源的 Layer 3 内容。

- 路径必须是 Skill 内的规范相对路径，拒绝 `..`。
- 目录返回 `400`。
- 不可作为文本读取的二进制资源返回 `415`。
- 资源不存在返回 `404`。

## 分类、标签与树

| 接口 | 说明 |
|---|---|
| `GET /categories` | 分类列表 |
| `GET /categories/tree` | 分类树，供前端文件树式导航 |
| `GET /categories/{category_id}` | 分类详情 |
| `POST /categories` | 创建分类 |
| `PUT /categories/{category_id}` | 更新分类 |
| `DELETE /categories/{category_id}` | 删除分类 |
| `GET /tags` | 标签列表 |
| `POST /tags` | 创建标签 |
| `PUT /tags/{tag_id}` | 更新标签 |
| `DELETE /tags/{tag_id}` | 删除标签 |
| `GET /grouped` | 按分类返回 Skill 分组 |
| `GET /stats` | 按状态统计 |

以上相对路径均需加 `/api/v1/skills` 前缀。

## CRUD 与治理

| 接口 | 说明 |
|---|---|
| `POST /api/v1/skills` | 创建 Skill；管理员直接 approved，普通用户进入 pending_review |
| `PUT /api/v1/skills/detail/{skill_id}` | 更新 Skill 和 Markdown 正文 |
| `DELETE /api/v1/skills/detail/{skill_id}` | 删除 Skill 及资源树 |
| `POST /api/v1/skills/detail/{skill_id}/submit-review` | 提交审核 |
| `GET /api/v1/skills/review/pending` | 待审核列表 |
| `POST /api/v1/skills/detail/{skill_id}/review` | 审核 |
| `POST /api/v1/skills/detail/{skill_id}/archive` | 归档 |
| `POST /api/v1/skills/batch/delete` | 批量删除 |
| `POST /api/v1/skills/batch/review` | 批量审核 |
| `POST /api/v1/skills/batch/tags` | 批量维护标签 |
| `GET /api/v1/skills/export/all` | 导出分类、标签和完整 Skill 数据 |

前端编辑器以 `content_raw` 作为 Markdown 主编辑面，分类、标签、触发器和其他元数据默认折叠；预览在编辑区域内通过弹窗打开。树形视图与表格视图必须读取同一 API 数据，不能再维护本地 Skill 列表。

## 同步

### `GET /api/v1/skills/sources`

只返回已配置来源及可用状态，不读取包正文：

```json
{
  "items": [
    {
      "key": "external-documents",
      "name": "external-skill-library",
      "available": true
    }
  ]
}
```

### `POST /api/v1/skills/sync/external`

同步配置的外部来源，支持：

| 参数 | 默认 | 含义 |
|---|---:|---|
| `overwrite` | `false` | 是否覆盖数据库中同 slug 的正文和元数据 |
| `prune_stale` | `false` | 是否删除该来源中已不存在的 Skill |
| `dry_run` | `false` | 只返回变化计划，不写数据库 |

默认 `overwrite=false` 时保留数据库编辑；若来源资源清单发生变化，仍可幂等更新来源拥有的资源树及 manifest 元数据。

### `POST /api/v1/skills/sync/from-files`

同步项目内置 Skill，并继续同步配置的外部来源。写入后刷新 Agent 运行时索引。生产操作建议先执行外部源 `dry_run`，确认后再同步；`prune_stale` 需谨慎开启。

## Agent 与任务选择

可选择 Skill 的项目任务使用：

```json
{
  "selected_skill_ids": ["docx", "pdf"]
}
```

当前 `url_scan`、`company_scan`、`fofa_collect` 支持显式选择。后端会：

1. 规范化并去重 ID，最多 32 个。
2. 根据运行时数据库索引校验 Skill 是否存在且可用。
3. 将选择写入任务参数，保证恢复和审计可重放。
4. 通过上下文选择器限制当前 Agent 可渐进加载的 Skill。

空数组表示不人为限制，由场景匹配器先读取 Layer 1，再按触发条件加载正文和必要资源。显式选择也不能把整个资源树一次性塞入 Prompt。

AI 中枢、任务弹窗和话术流程复用同一 Skill 索引与选择语义。新增使用方应调用 skill library/runtime service，不直接查询 collection 或读取 `/root/skills`。

## 安全与验证

- `SKILL.md` 和资源属于指令数据，导入不等于可信执行；脚本资源默认只供 Agent 阅读，不能因同步而自动执行。
- 文件路径在 adapter 和 API 两层规范化，禁止目录穿越。
- 管理员同步操作需要保留来源、manifest hash、资源数量和变更摘要。
- CRUD 或同步完成后必须刷新运行时，否则前端与 Agent 会看到不同版本。
- 回归至少覆盖：列表不含正文、slug/ID 详情、资源逐层读取、路径校验、dry-run、非覆盖同步、manifest 变化、陈旧资源清理及任务 Skill 校验。
