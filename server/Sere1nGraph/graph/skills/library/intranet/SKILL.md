---
name: 内网二次钓鱼
description: 已获取内网部分权限后的横向钓鱼话术。利用内部邮件/IM的高信任度进行二次攻击。当目标环境为内网、已有初始权限、需要横向移动时加载此 skill。
category: intranet
phases:
  - scenario
  - script
  - objection
tags:
  - 内网
  - 横向移动
  - 二次钓鱼
  - 权限提升
priority: 3
---

# 内网二次钓鱼 Skill

本 skill 跨三个阶段复用。

## scenario 阶段

在 `LogicChainStep` 中，当 `channel = "intranet"` 时：
- `action` 描述内网环节的具体动作（内部邮件/IM消息/文件共享）
- `fallback` 考虑内部安全告警触发后的备选

### 可伪造身份（填入 FakedIdentity）
- IT 部门（系统通知/安全告警）
- 行政部门（通知公告/文件下发）
- 上级领导（紧急任务/文件审批）
- 同事（文件共享/协作请求）

### 逻辑链条模板
- 系统升级: 内部邮件通知 → 附带"升级工具"下载链接
- 文件共享: IM 发送"共享文档" → 引导打开
- 紧急任务: 领导名义发送邮件 → 附带"紧急文件"

## script 阶段

输出到 `ScriptOutput.scripts[].channel_scripts[]`，其中 `channel = "intranet"`。

### 特殊规则
- `email_template` 必须模仿内部邮件格式（含内部签名模板）
- `dialogue` 模仿同事/上级的沟通风格
- `key_points` 包含：内部术语、项目名称、发送时间建议

详细案例见 `references/intranet-cases.md`。

## objection 阶段

内网场景质疑概率低，但仍需覆盖：
1. "这个通知我没在OA上看到" → tactic: 权威效应（领导直接交代的）
2. "为什么不走正式流程？" → tactic: 紧迫感（系统紧急升级）
