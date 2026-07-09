---
name: 招聘场景
description: 招聘/求职相关的社工场景。伪造HR、猎头、面试官身份。当目标与招聘、求职、HR相关，或目标在社交平台暴露求职状态时加载此 skill。
category: recruitment
phases:
  - scenario
  - script
tags:
  - 招聘
  - HR
  - 猎头
  - 面试
  - 求职
priority: 3
---

# 招聘场景 Skill

## scenario 阶段

### FakedIdentity 建议

| 身份 | position | background |
|------|----------|------------|
| 猎头顾问 | 高级猎头顾问 | 5年互联网猎头经验，专注XX领域 |
| HR | 人力资源经理 | 负责XX部门招聘 |
| 面试官 | 技术总监 | 10年技术经验，负责技术面试 |

### LogicChainStep 模板

| 链条 | 步骤 |
|------|------|
| 猎头推荐 | Boss直聘联系(wechat) → 微信沟通(wechat) → 发送"候选人资料包" |
| 面试邀请 | 邮件发送面试通知(email) → 附带"面试须知及公司介绍" |
| Offer 发放 | 邮件发送 offer(email) → 附带"入职材料模板" |

## script 阶段

招聘场景的 `ChannelScript` 要点：
- 使用招聘行业专业术语（JD、HC、薪资结构、背调）
- 文件命名符合招聘习惯（XX公司_高级产品经理_JD.pdf）
- 利用求职者的期待心理和信息不对称
- `PayloadSpec.archive_name` 示例：`候选人简历汇总_2026Q1.zip`

详细案例见 `references/recruitment-cases.md`。
