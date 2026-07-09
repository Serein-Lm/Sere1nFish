---
name: 供应商/合作方场景
description: 供应商、投标方、合作伙伴相关的社工场景。当目标与采购、商务、招投标相关时加载此 skill。
category: vendor
phases:
  - scenario
  - script
tags:
  - 供应商
  - 投标
  - 合作
  - 采购
  - 商务
priority: 3
---

# 供应商/合作方场景 Skill

## scenario 阶段

### FakedIdentity 建议

| 身份 | position | background |
|------|----------|------------|
| 供应商业务代表 | 商务经理 | 3年行业经验，负责XX区域客户 |
| 投标方项目经理 | 项目经理 | 主导过多个同类项目 |
| 合作伙伴商务 | 商务总监 | 负责战略合作 |

### LogicChainStep 模板

| 链条 | 步骤 |
|------|------|
| 投标 | 邮件发送投标意向(email) → 电话确认(phone) → 发送"标书/技术方案" |
| 报价 | 邮件发送报价单(email) → 电话跟进(phone) → 微信发送"详细报价明细表"(wechat) |
| 合作 | 微信联系(wechat) → 发送"合作方案及合同模板" |

## script 阶段

供应商场景的 `ChannelScript` 要点：
- 使用商务/采购领域专业术语（标书、技术方案、报价明细、合同条款）
- 文件命名符合商务习惯（XX项目_技术方案_v2.1.docx）
- 利用业务流程的惯性（"按照流程需要您确认"）
- `PayloadSpec.archive_name` 示例：`XX项目_投标技术方案_XX科技.zip`

详细案例见 `references/vendor-cases.md`。
