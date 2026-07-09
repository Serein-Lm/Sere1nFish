---
name: 文档整合输出
description: 将所有阶段的结构化JSON结果整合为完整的Markdown交付文档。当所有阶段完成后需要生成最终文档时加载此 skill。
category: general
phases:
  - finalize
tags:
  - 整合
  - 文档
  - 输出
  - Markdown
priority: 1
---

# 文档整合输出 Skill

## finalize 阶段

输出到 `FinalOutput`：

| 字段 | 规则 |
|------|------|
| `markdown` | 整合后的完整 Markdown 文档 |
| `loaded_skills` | 本次加载的 skill id 列表 |

### 输入

你会收到前面三个阶段的 JSON 输出：
- `ScenarioOutput`（场景伪造）
- `ScriptOutput`（话术生成）
- `ObjectionOutput`（质疑应对）

### 整合规则

1. 根据场景分类整理，每个场景一个大章节
2. 每个分类的内容**完整显示，不可更改**
3. 逻辑链条用 `→` 符号连接
4. 质疑及应对**必须按照原有内容完整填写**

### Markdown 格式要求

**微信话术**: 使用对话形式
```
🔴 A（攻击者）: 内容
🔵 T（目标）: 内容
```

**邮箱话术**: 使用引用块
```
> 发件人: xxx
> 主题: xxx
>
> 正文...
```

**电话话术**: 使用电话图标
```
📞 A: 内容
📱 T: 内容
```

### 输出结构（每个场景必须包含）

1. 场景概述
2. 目标背景
3. 伪造背景
4. 逻辑链条
5. 话术（按渠道分类）
6. 样本文件
7. 质疑及应对

**所有部分必须按顺序完整输出。**
