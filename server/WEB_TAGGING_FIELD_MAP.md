# Web Tagging 前端字段映射（Field Map）

本文档面向前端，用于对 Web Tagging 结果中的 `findings` 做枚举映射与 UI 渲染。

## 1. 顶层结构

- `intro`
  - `url`：输入 URL
  - `final_url`：最终落地 URL（如有重定向）
  - `domain`：主域名
  - `site_name`：站点名称（可为空）
  - `entity_name`：主体名称（可为空）
  - `summary`：一句话介绍（可为空）

- `has_findings`：是否存在社工攻击面信息
- `no_findings_reason`：当 `has_findings=false` 时的原因（中文）
- `findings`：社工攻击面条目数组

## 2. finding 字段（前端渲染建议）

每条 finding 字段：

- `type`：大类（主分类，用于一级分组/颜色/icon）
- `scope`：归属范围（官方/私人/企业）
- `channel`：触达渠道（邮箱/电话/微信/链接/表单…）
- `role`：业务角色（HR/商务/媒体/客服…）
- `subtype`：细分类型（string，可为空；用于二级标签/细分 icon）
- `label`：页面原始标签文案（可为空）
- `value`：具体值（可为空）
- `context`：上下文（中文）
- `source_url`：来源页面
- `evidence`：证据（中文，<=120字）
- `attention_score`：关注度分（0-100，仅该条）
- `attention_reason`：评分理由（中文）

## 3. 枚举映射表（英文 -> 中文）

### 3.1 type（大类）

- `personal_mobile`：个人手机号
- `personal_email`：个人邮箱
- `personal_wechat`：个人微信（号/二维码）
- `enterprise_wechat`：企业微信（客服/二维码/添加入口）
- `hr_contact`：招聘/HR 联系方式
- `business_contact`：商务/销售/合作联系方式
- `media_contact`：媒体/公关联系方式
- `customer_service`：客服/工单/反馈渠道
- `group_chat`：群聊/社群
- `other`：其它社工入口

### 3.2 scope（归属范围）

- `official`：官方公开渠道
- `personal`：私人信息
- `enterprise`：企业级渠道（含第三方客服/工单/反馈渠道等）

### 3.3 channel（触达渠道）

- `email`：邮箱
- `phone`：电话
- `wechat`：微信
- `link`：链接
- `form`：表单
- `other`：其它

### 3.4 role（业务角色）

- `hr`：招聘/人力
- `business`：商务合作
- `sales`：销售
- `support`：技术支持/售后支持
- `customer_service`：客服/工单/反馈
- `media`：媒体沟通
- `pr`：公关/品牌
- `other`：其它

## 4. subtype（细分类型）建议映射（string）

说明：`subtype` 不强制枚举，前端可按需映射；当遇到未知值时可按默认样式渲染。

### 4.1 客服/工单/反馈（通常 type=customer_service）

- `live_chat_third_party`：第三方在线客服/IM
- `live_chat_native`：站内在线客服（自建）
- `ticket_system`：工单系统
- `feedback_form`：反馈表单
- `support_portal`：帮助中心/支持门户
- `hotline_400`：400 热线
- `hotline_landline`：座机热线/总机
- `service_wechat`：客服微信/客服二维码

### 4.2 HR（通常 type=hr_contact）

- `resume_email`：简历投递邮箱
- `resume_phone`：招聘电话
- `job_portal`：招聘入口/招聘系统链接
- `campus_recruit`：校招入口

### 4.3 商务/销售（通常 type=business_contact）

- `business_email`：商务合作邮箱
- `sales_email`：销售邮箱
- `partner_program`：渠道/生态合作入口
- `supplier_portal`：供应商入口
- `procurement_contact`：采购联系

### 4.4 媒体/公关（通常 type=media_contact）

- `media_email`：媒体邮箱
- `pr_email`：公关邮箱
- `press_contact`：新闻/媒体联系入口

### 4.5 私人信息（通常 scope=personal）

- `mobile_personal`：个人手机号
- `email_personal`：个人邮箱
- `wechat_id_personal`：个人微信号
- `wechat_qr_personal`：个人微信二维码

### 4.6 群聊/社群（通常 type=group_chat）

- `qq_group`：QQ群
- `wechat_group`：微信群
- `telegram`：Telegram
- `discord`：Discord
- `community_invite`：社群邀请链接
