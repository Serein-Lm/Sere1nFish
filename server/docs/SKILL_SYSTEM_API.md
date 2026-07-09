# Skill 系统 API 文档

## 概述

渐进式披露架构的话术生成 Skill 系统。

三层加载：Index（元数据）→ SKILL.md（指令）→ references/（案例）

所有输出为 Pydantic JSON Schema，可直接存储和前端渲染。

## Skill 列表

| ID | 名称 | 类别 | 适用阶段 | 说明 |
|----|------|------|---------|------|
| base-scenario | 基础场景构建 | general | scenario | 通用场景伪造框架 |
| real-cases | 实战案例库 | real_cases | scenario,script,objection | 跨阶段复用的案例库 |
| wechat | 微信话术 | wechat | scenario,script,objection | 微信渠道完整话术 |
| email | 邮箱话术 | email | scenario,script,objection | 邮箱渠道完整话术 |
| phone | 电话话术 | phone | scenario,script,objection | 电话渠道完整话术 |
| intranet | 内网二次钓鱼 | intranet | scenario,script,objection | 内网横向钓鱼 |
| sms | 短信话术 | sms | script | 短信渠道话术 |
| recruitment | 招聘场景 | recruitment | scenario,script | 招聘/求职场景 |
| vendor | 供应商场景 | vendor | scenario,script | 投标/合作场景 |
| it-support | IT 支持场景 | it_support | scenario,script | 安全/运维场景 |
| payload | 样本文件构造 | general | script | 压缩包/伪装指南 |
| base-objection | 通用质疑应对 | general | objection | 心理策略框架 |
| finalize | 文档整合输出 | general | finalize | Markdown 整合 |

## Skill 类别（前端选择器用）

```json
[
  {"value": "general", "label": "通用"},
  {"value": "real_cases", "label": "实战案例"},
  {"value": "wechat", "label": "微信"},
  {"value": "email", "label": "邮箱"},
  {"value": "phone", "label": "电话"},
  {"value": "intranet", "label": "内网钓鱼"},
  {"value": "sms", "label": "短信"},
  {"value": "recruitment", "label": "招聘场景"},
  {"value": "vendor", "label": "供应商场景"},
  {"value": "it_support", "label": "IT支持场景"},
  {"value": "customer", "label": "客服场景"},
  {"value": "government", "label": "政务场景"},
  {"value": "finance", "label": "财务场景"}
]
```

## API 端点（待实现 Router）

### GET /api/v1/skills

列出所有可用 skills 的索引（Layer 1）。

**响应**:
```json
{
  "skills": [
    {
      "id": "wechat",
      "name": "微信话术",
      "description": "微信渠道的完整社工话术...",
      "category": "wechat",
      "phases": ["scenario", "script", "objection"],
      "tags": ["微信", "即时通讯"],
      "priority": 1,
      "enabled": true
    }
  ],
  "summary": {
    "total": 13,
    "by_phase": {"scenario": 8, "script": 10, "objection": 7, "finalize": 1},
    "by_category": {"general": 3, "wechat": 1, "email": 1, "...": "..."}
  }
}
```

### GET /api/v1/skills/{skill_id}

获取单个 skill 的完整内容（Layer 1 + 2 + 3 文件列表）。

### GET /api/v1/skills/{skill_id}/references/{ref_name}

获取 skill 的某个 reference 文件内容（Layer 3）。
