# 最终输出内容指南

## 概述

前端页面分为两个展示区域：
1. **思维链区域**（上方）：展示执行过程的树形结构
2. **最终输出区域**（下方）：展示汇总的最终结果

## SSE 事件类型说明

### `content` 事件 - 思维链内部内容

用于在思维链节点内部显示内容，不会出现在底部。**支持 Markdown 格式**。

```json
{
  "event": "content",
  "id": "router_graph.agents.browser_1704067200200",
  "path": "graph.agents.browser",
  "ts": 1704067201000,
  "data": {
    "content": "## 华为公司信息\n\n- **官网**：www.huawei.com\n- **员工**：19.7万+\n- **年营收**：7000亿+"
  }
}
```

**显示位置**：在思维链的 "Browser Agent" 节点内部（使用 XMarkdown 渲染）

### `final` 事件 - 底部最终输出

用于发送最终要显示在底部的汇总结果：

```json
{
  "event": "final",
  "id": "router_final_1704067204000",
  "path": "graph",
  "ts": 1704067204000,
  "data": {
    "content": "综合以上信息，华为公司是..."
  }
}
```

**显示位置**：在思维链下方的最终结果区域

## 使用建议

### 方式 1：单段输出（简单场景）

适用于只有一次最终输出的场景：

```json
{
  "event": "final",
  "path": "graph",
  "data": {
    "content": "最终结果内容"
  }
}
```

### 方式 2：多段输出（复杂场景）

适用于需要分阶段展示结果的场景，使用 `section` 字段区分：

```json
// 第一段输出
{
  "event": "final",
  "path": "graph",
  "data": {
    "section": "phase1",
    "content": "第一阶段的结果",
    "meta": {
      "sectionTitle": "📊 第一阶段：信息收集"
    }
  }
}

// 第二段输出
{
  "event": "final",
  "path": "graph",
  "data": {
    "section": "phase2",
    "content": "第二阶段的结果",
    "meta": {
      "sectionTitle": "🎯 第二阶段：深度分析"
    }
  }
}
```

**效果**：
- 两段内容会分别显示，用不同的卡片区分
- 每段可以有自己的标题（通过 `meta.sectionTitle` 设置）
- 同一个 `section` 的多次 `final` 事件会累加到同一段中

### 典型工作流

```
1. 发送 start 事件（启动各个 agent）
2. 发送 content 事件（各 agent 的中间结果，显示在思维链内）
3. 发送 final 事件（汇总结果，显示在底部）
4. 发送 end 事件（结束）
```

### 完整示例：多阶段输出

```
# 1. 第一个 Graph 开始
data: {"event":"start","path":"graph1","data":{"type":"graph","displayName":"🔍 信息收集"}}

# 2. 执行 agents...
data: {"event":"start","path":"graph1.agents.browser","data":{"type":"agent","displayName":"Browser"}}
data: {"event":"content","path":"graph1.agents.browser","data":{"content":"找到官网..."}}
data: {"event":"end","path":"graph1.agents.browser","data":{"status":"success"}}

# 3. 第一个 Graph 的输出（第一段）
data: {"event":"final","path":"graph1","data":{"section":"collect","content":"# 信息收集结果\n\n","meta":{"sectionTitle":"📊 阶段一：信息收集"}}}
data: {"event":"final","path":"graph1","data":{"section":"collect","content":"- 官网：www.example.com\n"}}
data: {"event":"final","path":"graph1","data":{"section":"collect","content":"- 员工：1000+\n"}}

# 4. 第一个 Graph 结束
data: {"event":"end","path":"graph1","data":{"status":"success"}}

# 5. 第二个 Graph 开始
data: {"event":"start","path":"graph2","data":{"type":"graph","displayName":"🎯 深度分析"}}

# 6. 执行更多 agents...
data: {"event":"start","path":"graph2.agents.analyzer","data":{"type":"agent","displayName":"Analyzer"}}
data: {"event":"content","path":"graph2.agents.analyzer","data":{"content":"分析中..."}}
data: {"event":"end","path":"graph2.agents.analyzer","data":{"status":"success"}}

# 7. 第二个 Graph 的输出（第二段）
data: {"event":"final","path":"graph2","data":{"section":"analyze","content":"# 深度分析结果\n\n","meta":{"sectionTitle":"🎯 阶段二：深度分析"}}}
data: {"event":"final","path":"graph2","data":{"section":"analyze","content":"根据收集的信息，分析如下...\n"}}

# 8. 第二个 Graph 结束
data: {"event":"end","path":"graph2","data":{"status":"success"}}
```

**前端显示效果**：

```
┌─ 思维链 ─────────────────┐
│ 🔍 信息收集              │
│   └─ Browser (已完成)    │
│ 🎯 深度分析              │
│   └─ Analyzer (已完成)   │
└──────────────────────────┘

┌─ 📊 阶段一：信息收集 ────┐
│ # 信息收集结果           │
│ - 官网：www.example.com  │
│ - 员工：1000+            │
└──────────────────────────┘

┌─ 🎯 阶段二：深度分析 ────┐
│ # 深度分析结果           │
│ 根据收集的信息，分析...  │
└──────────────────────────┘
```

```
# 1. 启动 Browser Agent
data: {"event":"start","path":"graph.agents.browser","data":{"type":"agent","displayName":"🌐 Browser"}}

# 2. Browser 的中间结果（显示在思维链内，支持 Markdown）
data: {"event":"content","path":"graph.agents.browser","data":{"content":"## 华为官网信息\n\n- 域名：www.huawei.com\n- 员工：19.7万+"}}

# 3. 结束 Browser Agent
data: {"event":"end","path":"graph.agents.browser","data":{"status":"success"}}

# 4. 启动 XHS Agent
data: {"event":"start","path":"graph.agents.xhs","data":{"type":"agent","displayName":"📱 小红书"}}

# 5. XHS 的中间结果（显示在思维链内，支持 Markdown）
data: {"event":"content","path":"graph.agents.xhs","data":{"content":"## 小红书数据\n\n找到 **100+** 篇相关笔记"}}

# 6. 结束 XHS Agent
data: {"event":"end","path":"graph.agents.xhs","data":{"status":"success"}}

# 7. 启动汇总阶段
data: {"event":"start","path":"graph.synthesis","data":{"type":"phase","displayName":"📝 汇总结果"}}

# 8. 发送最终输出（显示在底部）- 支持流式
data: {"event":"final","path":"graph","data":{"content":"# 华为公司信息汇总\n\n"}}
data: {"event":"final","path":"graph","data":{"content":"## 基本信息\n"}}
data: {"event":"final","path":"graph","data":{"content":"- 官网：www.huawei.com\n"}}
data: {"event":"final","path":"graph","data":{"content":"- 员工数：19.7万+\n"}}
data: {"event":"final","path":"graph","data":{"content":"- 年营收：7000亿+\n\n"}}
data: {"event":"final","path":"graph","data":{"content":"## 社交媒体\n"}}
data: {"event":"final","path":"graph","data":{"content":"小红书上有100+篇相关笔记...\n"}}

# 9. 结束汇总阶段
data: {"event":"end","path":"graph.synthesis","data":{"status":"success"}}

# 10. 结束整个流程
data: {"event":"end","path":"graph","data":{"status":"success"}}
```

## 关键点

1. **`content` 事件**：
   - 必须指定具体的节点 `path`（如 `graph.agents.browser`）
   - 内容显示在对应节点内部
   - 用于展示中间过程
   - **支持 Markdown 格式**（使用 XMarkdown 渲染）

2. **`final` 事件（不带 section）**：
   - 所有内容累加到一起
   - 适合简单场景
   - **支持 Markdown 格式**

3. **`final` 事件（带 section）**：
   - 使用 `data.section` 字段标识段落
   - 相同 `section` 的内容会累加到同一段
   - 不同 `section` 会分开显示
   - 通过 `meta.sectionTitle` 设置段落标题
   - 适合多阶段输出场景
   - **支持 Markdown 格式**

4. **混合使用**：
   - 可以同时使用带 section 和不带 section 的 final 事件
   - 不带 section 的会累加到 `finalContent`
   - 带 section 的会分段显示在 `finalSections`
   - 前端优先显示 `finalSections`，如果为空则显示 `finalContent`

5. **Markdown 支持**：
   - 所有 `content` 和 `final` 事件都支持 Markdown 格式
   - 可以使用标题、列表、粗体、代码块等
   - 思维链内部和底部输出都使用 XMarkdown 渲染

## 前端处理逻辑

```typescript
// 前端会累加所有 final 事件的内容
state.finalContent += data.content

// 最终显示在底部（使用 Markdown 渲染）
<XMarkdown content={state.finalContent} />
```

## 注意事项

- 如果不发送 `final` 事件，底部将显示 "执行完成"
- `final` 事件可以在任何时候发送，不一定要在最后
- 建议在有实质性汇总结果时才发送 `final` 事件
