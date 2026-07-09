---
name: IT 支持场景
description: IT运维、技术支持、安全部门相关的社工场景。伪造安全厂商、云服务商、内部IT身份。当目标为技术人员或需要利用技术焦虑时加载此 skill。
category: it_support
phases:
  - scenario
  - script
tags:
  - IT
  - 运维
  - 技术支持
  - 安全
  - 漏洞
priority: 3
---

# IT 支持场景 Skill

## scenario 阶段

### FakedIdentity 建议

| 身份 | position | background |
|------|----------|------------|
| 安全厂商技术支持 | 技术支持工程师 | 3年安全行业经验 |
| 云服务商客服 | 客户成功经理 | 负责企业客户安全告警 |
| 内部 IT 运维 | 运维工程师 | 负责XX系统维护 |

### LogicChainStep 模板

| 链条 | 步骤 |
|------|------|
| 漏洞通知 | 邮件告知安全漏洞(email) → 电话确认(phone) → 发送"修复工具" |
| 系统升级 | 内部通知升级(intranet) → 发送"升级客户端" |
| 安全检查 | 邮件通知安全审计(email) → 发送"自检工具" |

## script 阶段

IT 场景的 `ChannelScript` 要点：
- 使用技术术语（CVE编号、补丁版本、系统版本）
- 文件命名符合技术习惯（patch_CVE-2026-xxxx_v1.2.exe）
- 利用安全焦虑心理和运维人员的责任感
- `PayloadSpec.archive_name` 示例：`CVE-2026-XXXX_修复工具_v1.2.zip`

详细案例见 `references/it-support-cases.md`。
