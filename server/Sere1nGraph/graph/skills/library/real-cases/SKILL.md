---
name: 实战案例库
description: 经过验证的社工实战案例集合。在场景构建、话术生成、质疑应对时提供真实案例参考。当需要案例灵感、话术模板、成功/失败经验时加载此 skill 的 references。
category: real_cases
phases:
  - scenario
  - script
  - objection
tags:
  - 案例
  - 参考
  - 复用
priority: 2
---

# 实战案例库

本 skill 提供经过验证的社工案例，供各阶段参考。案例按场景分类存放在 `references/` 目录中。

## 使用方式

根据当前任务的目标行业和渠道，选择性加载对应的案例文件：

| 文件 | 内容 | 适用场景 |
|------|------|----------|
| `references/bidding-cases.md` | 招投标场景案例 | 目标为采购/商务部门 |
| `references/recruitment-cases.md` | 招聘场景案例 | 目标与HR/求职相关 |
| `references/it-support-cases.md` | IT支持场景案例 | 目标为技术人员 |

## 案例格式

所有案例都以 Pydantic Schema 的 JSON 格式呈现，可直接作为输出参考：
- `ScenarioItem` 格式的完整场景示例
- `ChannelScript` 格式的话术示例
- `PayloadSpec` 格式的样本文件示例
- `ObjectionItem` 格式的质疑应对示例

## 案例使用原则

1. 案例仅供参考，需要根据实际目标信息进行**创新**
2. 重点学习每个案例的**信任建立逻辑**
3. 关注逻辑链条中每一步的**合理性**
4. 不要照搬案例，要理解背后的**心理策略**并灵活运用
