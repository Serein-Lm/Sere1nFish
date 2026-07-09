# 多阶段输出方案

## 问题场景

在复杂的多 Graph 执行流程中，需要：
1. 第一个 Graph 执行完 → 显示第一阶段结果
2. 思维链继续增长（第二个 Graph 执行）
3. 第二个 Graph 执行完 → 显示第二阶段结果

如果所有 `final` 事件的内容都混在一起，用户无法区分不同阶段的输出。

## 解决方案：分段输出

使用 `section` 字段来标识不同的输出段落。

### 数据结构

```typescript
interface EventData {
  content?: string
  section?: string  // 段落标识符
  meta?: {
    sectionTitle?: string  // 段落标题
  }
}

interface FinalSection {
  section: string      // 段落 ID
  title?: string       // 段落标题
  content: string      // 段落内容
}

interface ExecutionState {
  finalContent: string           // 不带 section 的内容（向后兼容）
  finalSections: FinalSection[]  // 分段内容
}
```

## 使用方式

### 方式 1：简单输出（不分段）

```json
{
  "event": "final",
  "path": "graph",
  "data": {
    "content": "所有内容混在一起"
  }
}
```

**效果**：内容累加到 `finalContent`，显示为一整块

### 方式 2：分段输出（推荐）

```json
// 第一阶段输出
{
  "event": "final",
  "path": "graph1",
  "data": {
    "section": "stage1",
    "content": "第一阶段的内容",
    "meta": {
      "sectionTitle": "📊 阶段一：信息收集"
    }
  }
}

// 第二阶段输出
{
  "event": "final",
  "path": "graph2",
  "data": {
    "section": "stage2",
    "content": "第二阶段的内容",
    "meta": {
      "sectionTitle": "🎯 阶段二：深度分析"
    }
  }
}
```

**效果**：
- 两段内容分别显示在不同的卡片中
- 每段有自己的标题和边框样式
- 清晰区分不同阶段的输出

## 完整示例

### 后端 SSE 事件流

```
# === 第一个 Graph：信息收集 ===
data: {"event":"start","path":"graph1","data":{"type":"graph","displayName":"🔍 信息收集"}}

# 执行 Browser Agent
data: {"event":"start","path":"graph1.browser","data":{"type":"agent","displayName":"Browser"}}
data: {"event":"content","path":"graph1.browser","data":{"content":"## 正在访问官网\n\n- 状态：连接中...\n- 进度：50%"}}}
data: {"event":"end","path":"graph1.browser","data":{"status":"success"}}

# 执行 XHS Agent
data: {"event":"start","path":"graph1.xhs","data":{"type":"agent","displayName":"小红书"}}
data: {"event":"content","path":"graph1.xhs","data":{"content":"## 正在搜索小红书\n\n找到 **100+** 篇笔记"}}}
data: {"event":"end","path":"graph1.xhs","data":{"status":"success"}}

# 第一阶段输出（分段）
data: {"event":"final","path":"graph1","data":{"section":"collect","content":"# 信息收集结果\n\n","meta":{"sectionTitle":"📊 阶段一：信息收集"}}}
data: {"event":"final","path":"graph1","data":{"section":"collect","content":"## 官网信息\n- 域名：www.example.com\n- 员工：1000+\n\n"}}
data: {"event":"final","path":"graph1","data":{"section":"collect","content":"## 社交媒体\n- 小红书：100+ 篇笔记\n"}}

data: {"event":"end","path":"graph1","data":{"status":"success"}}

# === 第二个 Graph：深度分析 ===
data: {"event":"start","path":"graph2","data":{"type":"graph","displayName":"🎯 深度分析"}}

# 执行 Analyzer Agent
data: {"event":"start","path":"graph2.analyzer","data":{"type":"agent","displayName":"分析器"}}
data: {"event":"content","path":"graph2.analyzer","data":{"content":"正在分析数据..."}}
data: {"event":"end","path":"graph2.analyzer","data":{"status":"success"}}

# 第二阶段输出（分段）
data: {"event":"final","path":"graph2","data":{"section":"analyze","content":"# 深度分析结果\n\n","meta":{"sectionTitle":"🎯 阶段二：深度分析"}}}
data: {"event":"final","path":"graph2","data":{"section":"analyze","content":"根据收集的信息，该公司具有以下特征：\n\n"}}
data: {"event":"final","path":"graph2","data":{"section":"analyze","content":"1. 规模较大，员工超过1000人\n"}}
data: {"event":"final","path":"graph2","data":{"section":"analyze","content":"2. 社交媒体活跃度高\n"}}

data: {"event":"end","path":"graph2","data":{"status":"success"}}

# === 第三个 Graph：生成报告 ===
data: {"event":"start","path":"graph3","data":{"type":"graph","displayName":"📝 生成报告"}}

# 执行 Writer Agent
data: {"event":"start","path":"graph3.writer","data":{"type":"agent","displayName":"报告生成器"}}
data: {"event":"content","path":"graph3.writer","data":{"content":"正在生成报告..."}}
data: {"event":"end","path":"graph3.writer","data":{"status":"success"}}

# 第三阶段输出（分段）
data: {"event":"final","path":"graph3","data":{"section":"report","content":"# 综合报告\n\n","meta":{"sectionTitle":"📝 阶段三：综合报告"}}}
data: {"event":"final","path":"graph3","data":{"section":"report","content":"综合以上信息，建议采取以下行动...\n"}}

data: {"event":"end","path":"graph3","data":{"status":"success"}}
```

### 前端显示效果

```
┌─────────────────────────────────────────┐
│ 思维链（ThoughtChain）                   │
├─────────────────────────────────────────┤
│ 🔍 信息收集                              │
│   ├─ Browser (已完成)                    │
│   └─ 小红书 (已完成)                     │
│                                          │
│ 🎯 深度分析                              │
│   └─ 分析器 (已完成)                     │
│                                          │
│ 📝 生成报告                              │
│   └─ 报告生成器 (已完成)                 │
└─────────────────────────────────────────┘

┌─ 📊 阶段一：信息收集 ───────────────────┐
│ # 信息收集结果                           │
│                                          │
│ ## 官网信息                              │
│ - 域名：www.example.com                  │
│ - 员工：1000+                            │
│                                          │
│ ## 社交媒体                              │
│ - 小红书：100+ 篇笔记                    │
└─────────────────────────────────────────┘

┌─ 🎯 阶段二：深度分析 ───────────────────┐
│ # 深度分析结果                           │
│                                          │
│ 根据收集的信息，该公司具有以下特征：     │
│                                          │
│ 1. 规模较大，员工超过1000人              │
│ 2. 社交媒体活跃度高                      │
└─────────────────────────────────────────┘

┌─ 📝 阶段三：综合报告 ───────────────────┐
│ # 综合报告                               │
│                                          │
│ 综合以上信息，建议采取以下行动...        │
└─────────────────────────────────────────┘
```

## 技术细节

### 前端处理逻辑

```typescript
// 1. 接收 final 事件
case 'final': {
  // 累加到 finalContent（向后兼容）
  state.finalContent += data.content || ''
  
  // 如果有 section，分段存储
  if (data.section) {
    let section = state.finalSections.find(s => s.section === data.section)
    
    if (!section) {
      section = {
        section: data.section,
        title: data.meta?.sectionTitle,
        content: '',
      }
      state.finalSections.push(section)
    }
    
    section.content += data.content || ''
  }
  break
}

// 2. 渲染时优先显示分段内容
{msg.executionState?.finalSections?.length > 0 ? (
  // 显示分段内容
  <Flex vertical gap={16}>
    {msg.executionState.finalSections.map(section => (
      <div key={section.section} className="section-card">
        {section.title && <div className="section-title">{section.title}</div>}
        <XMarkdown content={section.content} />
      </div>
    ))}
  </Flex>
) : (
  // 显示普通内容
  <XMarkdown content={msg.content} />
)}
```

## 最佳实践

1. **使用有意义的 section ID**
   - ✅ `"collect"`, `"analyze"`, `"report"`
   - ❌ `"1"`, `"2"`, `"3"`

2. **设置清晰的段落标题**
   ```json
   "meta": {
     "sectionTitle": "📊 阶段一：信息收集"
   }
   ```

3. **同一阶段的内容使用相同的 section**
   ```json
   // 这些会累加到同一段
   {"section": "collect", "content": "第一部分"}
   {"section": "collect", "content": "第二部分"}
   {"section": "collect", "content": "第三部分"}
   ```

4. **支持 Markdown 格式**
   ```json
   {
     "section": "report",
     "content": "# 标题\n\n## 子标题\n\n- 列表项\n"
   }
   ```

5. **向后兼容**
   - 不带 `section` 的 `final` 事件仍然有效
   - 会累加到 `finalContent` 并显示为一整块

## 注意事项

- `section` 字段是可选的，不影响现有功能
- 相同 `section` 的内容会累加到同一段
- 段落按照首次出现的顺序显示
- 每个段落支持独立的标题和样式
- **所有内容都支持 Markdown 格式**（思维链内部和底部输出都使用 XMarkdown 渲染）
- 可以在 `content` 和 `final` 事件中使用标题、列表、粗体、代码块等 Markdown 语法
