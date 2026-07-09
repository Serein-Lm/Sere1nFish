# XHS 笔记 API 文档

## 接口列表

### 1. 获取笔记列表

**GET** `/api/v1/xhs/{project_id}/notes`

#### 查询参数

| 参数 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| `task_id` | string | 否 | - | 按任务 ID 筛选 |
| `is_suspicious` | boolean | 否 | - | 筛选可疑笔记 |
| `sort_by` | string | 否 | `relevance` | 排序方式：`relevance`(关联度) / `created_at`(时间) |
| `limit` | int | 否 | 50 | 返回数量 |
| `skip` | int | 否 | 0 | 跳过数量 |

#### 响应示例
```json
[
  {
    "id": "6973abc123def456",
    "project_id": "6970c09e27b9715e54c7a83e",
    "task_id": "6971def456abc789",
    "note_id": "65f1234567890abcdef",
    "xsec_token": "ABCxyz123...",
    "xsec_source": "pc_search",
    "title": "字节跳动实习涨薪啦！分享我的薪资",
    "desc": "入职三个月终于涨薪了，分享一下我的实习经历...",
    "liked_count": "1.2万",
    "user": {
      "user_id": "60be48da000000002002c56d",
      "nickname": "momo（找暑期版）",
      "avatar": "https://sns-avatar-qc.xhscdn.com/avatar/xxx.jpg"
    },
    "cover": "https://sns-img-qc.xhscdn.com/xxx.jpg",
    "tagging": {
      "keyword_relevance": 85,
      "relevance_reason": "笔记明确提及字节跳动实习经历，与搜索关键词高度相关",
      "is_suspicious": true,
      "attention_score": 78,
      "attack_surface_types": ["employee_leak", "insider_info"],
      "reason": "暴露实习薪资信息和公司内部情况",
      "evidence": "公司明确(+30) + 薪资暴露(+20) + 实习经历(+15) + 内部信息(+13) = 78分",
      "company_mentioned": "字节跳动",
      "key_info_extracted": ["实习薪资", "涨薪幅度", "部门信息"]
    },
    "created_at": "2026-01-23T09:00:00.000000"
  }
]
```

---

### 2. 获取单个笔记

**GET** `/api/v1/xhs/notes/{note_id}`

#### 路径参数

| 参数 | 类型 | 说明 |
|------|------|------|
| `note_id` | string | 小红书笔记 ID |

#### 响应示例
```json
{
  "id": "6973abc123def456",
  "project_id": "6970c09e27b9715e54c7a83e",
  "task_id": "6971def456abc789",
  "note_id": "65f1234567890abcdef",
  "xsec_token": "ABCxyz123...",
  "xsec_source": "pc_search",
  "title": "字节跳动实习涨薪啦！分享我的薪资",
  "desc": "入职三个月终于涨薪了，分享一下我的实习经历...",
  "liked_count": "1.2万",
  "user": {
    "user_id": "60be48da000000002002c56d",
    "nickname": "momo（找暑期版）",
    "avatar": "https://sns-avatar-qc.xhscdn.com/avatar/xxx.jpg"
  },
  "cover": "https://sns-img-qc.xhscdn.com/xxx.jpg",
  "tagging": {
    "keyword_relevance": 85,
    "relevance_reason": "笔记明确提及字节跳动实习经历",
    "is_suspicious": true,
    "attention_score": 78,
    "attack_surface_types": ["employee_leak", "insider_info"],
    "reason": "暴露实习薪资信息和公司内部情况",
    "evidence": "公司明确(+30) + 薪资暴露(+20) + 实习经历(+15) + 内部信息(+13) = 78分",
    "company_mentioned": "字节跳动",
    "key_info_extracted": ["实习薪资", "涨薪幅度", "部门信息"]
  },
  "created_at": "2026-01-23T09:00:00.000000"
}
```

#### 错误响应
```json
{
  "detail": "笔记不存在"
}
```

---

### 3. 获取笔记详情

**GET** `/api/v1/xhs/notes/{note_id}/detail`

获取笔记的完整内容、评论摘要和详细打标结果。

#### 路径参数

| 参数 | 类型 | 说明 |
|------|------|------|
| `note_id` | string | 小红书笔记 ID |

#### 响应示例
```json
{
  "id": "6973detail123456",
  "note_id": "65f1234567890abcdef",
  "project_id": "6970c09e27b9715e54c7a83e",
  "xsec_token": "ABCxyz123...",
  "xsec_source": "pc_search",
  "content": "入职三个月终于涨薪了！\n\n先说结论：从日薪280涨到了320，涨幅14%左右。\n\n我是去年10月入职的字节跳动，做搜索推荐方向的策略产品实习生...",
  "comments_summary": "评论区主要讨论：1. 询问具体部门和组；2. 询问转正难度；3. 分享类似经历",
  "tagging": {
    "keyword_relevance": 90,
    "keyword_analysis": "笔记详细描述了在字节跳动的实习经历，包含具体薪资数据和部门信息",
    "company_identified": {
      "name": "字节跳动",
      "confidence": "high",
      "evidence": ["正文明确提及字节跳动", "描述搜索推荐部门", "薪资水平符合字节实习标准"],
      "related_to_keyword": true,
      "relationship_type": "等于搜索关键词"
    },
    "attention_score": 85,
    "findings": [
      {
        "type": "insider",
        "value": "实习日薪280-320元",
        "evidence": "正文明确提及薪资数据",
        "attention_reason": "可用于验证身份或进行薪资对比"
      },
      {
        "type": "insider",
        "value": "搜索推荐部门策略产品岗",
        "evidence": "正文描述工作内容",
        "attention_reason": "可用于定向社工"
      },
      {
        "type": "process",
        "value": "入职三个月后涨薪",
        "evidence": "正文描述涨薪时间线",
        "attention_reason": "了解公司内部流程"
      }
    ],
    "summary": "该笔记详细暴露了字节跳动搜索推荐部门实习生的薪资信息和涨薪流程，具有较高的社工价值"
  },
  "created_at": "2026-01-23T09:30:00.000000"
}
```

#### 错误响应
```json
{
  "detail": "笔记详情不存在"
}
```

---

## 字段说明

### XhsNoteOut（笔记基本信息）

| 字段 | 类型 | 说明 |
|------|------|------|
| `id` | string | MongoDB ID |
| `project_id` | string | 项目 ID |
| `task_id` | string | 任务 ID |
| `note_id` | string | 小红书笔记 ID |
| `xsec_token` | string | 用于前端跳转的 token |
| `xsec_source` | string | 来源标识 |
| `title` | string | 笔记标题 |
| `desc` | string | 笔记描述 |
| `liked_count` | string | 点赞数 |
| `user` | object | 用户信息 |
| `cover` | string | 封面图 URL |
| `tagging` | object | 打标结果 |
| `created_at` | datetime | 创建时间 |

### XhsNoteTagging（笔记打标）

| 字段 | 类型 | 说明 |
|------|------|------|
| `keyword_relevance` | int | 与搜索关键词的关联度 (0-100) |
| `relevance_reason` | string | 关联度分析原因 |
| `is_suspicious` | boolean | 是否可疑 |
| `attention_score` | int | 关注度评分 (0-100) |
| `attack_surface_types` | array | 攻击面类型列表 |
| `reason` | string | 打标原因 |
| `evidence` | string | 打分依据 |
| `company_mentioned` | string | 提及的公司 |
| `key_info_extracted` | array | 提取的关键信息 |

### attack_surface_types 枚举值

| 值 | 说明 |
|----|------|
| `employee_leak` | 员工信息泄露 |
| `contact_info` | 联系方式暴露 |
| `insider_info` | 内部信息 |
| `credential_leak` | 凭证泄露 |
| `org_structure` | 组织架构 |
| `business_process` | 业务流程 |
| `technical_info` | 技术信息 |
| `location_info` | 位置信息 |
| `social_relation` | 社交关系 |
| `other` | 其他 |

### XhsNoteDetailOut（笔记详情）

| 字段 | 类型 | 说明 |
|------|------|------|
| `id` | string | MongoDB ID |
| `note_id` | string | 小红书笔记 ID |
| `project_id` | string | 项目 ID |
| `xsec_token` | string | 用于前端跳转的 token |
| `xsec_source` | string | 来源标识 |
| `content` | string | 笔记完整内容 |
| `comments_summary` | string | 评论摘要 |
| `tagging` | object | 详情打标结果 |
| `created_at` | datetime | 创建时间 |

### XhsDetailTagging（详情打标）

| 字段 | 类型 | 说明 |
|------|------|------|
| `keyword_relevance` | int | 关联度 (0-100) |
| `keyword_analysis` | string | 关键词关联分析 |
| `company_identified` | object | 识别出的公司 |
| `attention_score` | int | 关注度评分 (0-100) |
| `findings` | array | 发现列表 |
| `summary` | string | 摘要 |

### XhsDetailFinding（详情发现）

| 字段 | 类型 | 说明 |
|------|------|------|
| `type` | string | 类型：contact/insider/credential/location/relation/process/other |
| `value` | string | 具体内容 |
| `evidence` | string | 证据来源 |
| `attention_reason` | string | 关注原因 |

---

## 前端跳转

使用 `xsec_token` 和 `xsec_source` 构建小红书笔记链接：

```
https://www.xiaohongshu.com/explore/{note_id}?xsec_token={xsec_token}&xsec_source={xsec_source}
```
