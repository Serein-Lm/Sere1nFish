---
name: 电话话术
description: 电话渠道的完整社工话术。包含开场白、业务切入、信任建立、文件引导、应急话术、质疑应对。当逻辑链条涉及电话沟通时加载此 skill。
category: phone
phases:
  - scenario
  - script
  - objection
tags:
  - 电话
  - 语音
  - 口语
priority: 1
---

# 电话话术 Skill

本 skill 跨三个阶段复用。根据当前阶段填充对应的 Schema 字段。

## scenario 阶段

在 `LogicChainStep` 中，当 `channel = "phone"` 时：
- `action` 描述电话环节的具体动作（确认身份/跟进邮件/引导加微信）
- `fallback` 考虑目标不接电话的备选（短信 → 再打）

## script 阶段

输出到 `ScriptOutput.scripts[].channel_scripts[]`，其中 `channel = "phone"`。

### DialogueTurn 填充规则

| 字段 | 规则 |
|------|------|
| `role` | `"attacker"` 或 `"target"` |
| `content` | **口语化**表达（不是书面语） |
| `tactic` | 使用的心理策略名称 |

- 对话不少于 **6 轮**（12 条 DialogueTurn）
- `key_points` 填入：语气控制建议、应急话术

### 关键节点（必须覆盖）

1. **开场白**（30秒内建立身份）→ tactic: 权威效应
2. **业务切入**（自然过渡到目的）→ tactic: 社会认同
3. **信任建立**（提供可验证信息）→ tactic: 互惠原则
4. **文件引导**（引导到发送文件环节）→ tactic: 紧迫感
5. **收尾**（确认后续联系方式）→ tactic: 一致性

### 渠道切换约束（极其重要）

电话对话中经常会自然地切换到其他渠道（"我发邮件给您""加个微信"）。

**如果对话中提到了任何其他渠道，必须同时在 scripts 中提供该渠道的完整内容：**
- 提到"发邮件" → 必须有完整的 email_template（发件人、主题、正文、签名、附件说明）
- 提到"加微信" → 必须有完整的微信对话话术
- 提到"发短信" → 必须有完整的短信内容

**绝不允许"提到但不提供"。**

### 应急话术（填入 key_points）

| 目标反应 | 应对 | 后续渠道要求 |
|----------|------|------------|
| "我很忙" | 简化请求，约定回拨时间 | 无需额外渠道 |
| "发邮件吧" | 顺势转到邮件渠道 | **必须提供完整邮件模板** |
| "你是谁？" | 提供工号/前台电话 | 无需额外渠道 |
| 直接挂断 | 10分钟后发短信跟进 | **必须提供完整短信内容** |
| "加我微信说" | 顺势加微信 | **必须提供完整微信对话** |

详细案例见 `references/phone-dialogue-cases.md`。

## objection 阶段

输出到 `ObjectionOutput.scenario_objections[].objections[]`。

电话场景必须覆盖的质疑类型：
1. "你这个号码我没见过" → tactic: 提供验证途径
2. "我需要核实你的身份" → tactic: 配合验证+自信态度
3. "为什么不通过正式渠道？" → tactic: 权威效应+虚荣心
4. "我先确认一下再说" → tactic: 紧迫感+承诺一致性
5. "你怎么知道我电话的？" → tactic: 合理化（"您同事/前台给的""官网上有"）

每条 `ObjectionItem` 的 `context_note` 必须说明与当前场景的具体关联。
