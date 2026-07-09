# ThoughtChain 思维链实现文档

## 概述

本文档详细介绍了基于 Ant Design X 的 ThoughtChain 组件实现 AI Agent 思维链可视化的完整方案，包括前后端对接、核心实现、调试方法和二次开发指南。

## 架构设计

```
┌─────────────────────────────────────────────────────────────────┐
│                        前端 (React)                              │
│  ┌─────────────┐    ┌──────────────┐    ┌───────────────────┐  │
│  │   Sender    │───▶│ agentService │───▶│   ThoughtChain    │  │
│  │  (输入框)   │    │  (SSE解析)   │    │   (思维链展示)    │  │
│  └─────────────┘    └──────────────┘    └───────────────────┘  │
└─────────────────────────────────────────────────────────────────┘
                              │
                              │ SSE Stream
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│                      后端 (LangGraph)                            │
│  ┌─────────────┐    ┌──────────────┐    ┌───────────────────┐  │
│  │  Classify   │───▶│   Agents     │───▶│    Synthesis      │  │
│  │  (分类节点) │    │  (并行执行)  │    │   (汇总节点)      │  │
│  └─────────────┘    └──────────────┘    └───────────────────┘  │
└─────────────────────────────────────────────────────────────────┘
```

## 前后端 SSE 事件协议

### 事件类型定义

```typescript
type SSEEventType = 
  | 'graph_start'       // Graph 开始执行
  | 'classify_start'    // 分类节点开始
  | 'classify_end'      // 分类节点结束，返回选中的 agents
  | 'agent_start'       // Agent 开始执行
  | 'agent_tool_start'  // Agent 调用工具开始
  | 'agent_tool_end'    // Agent 调用工具结束
  | 'agent_content'     // Agent 输出内容（流式）
  | 'agent_end'         // Agent 执行结束
  | 'synthesis_start'   // 汇总开始
  | 'synthesis_content' // 汇总内容（流式）
  | 'synthesis_end'     // 汇总结束
  | 'graph_end'         // Graph 执行完成
  | 'error'             // 错误
```

### 后端 SSE 数据格式示例

```
data: {"type": "graph_start", "query": "查询九章云极", "timestamp": "..."}
data: {"type": "classify_start", "timestamp": "..."}
data: {"type": "classify_end", "agents": ["browser", "weixin"], "timestamp": "..."}
data: {"type": "agent_start", "agent_name": "browser", "agent_display_name": "🌐 Browser Agent", "timestamp": "..."}
data: {"type": "agent_content", "agent_name": "browser", "data": "我需要", "timestamp": "..."}
data: {"type": "agent_tool_start", "agent_name": "browser", "tool_name": "search", "timestamp": "..."}
data: {"type": "agent_tool_end", "agent_name": "browser", "tool_name": "search", "timestamp": "..."}
data: {"type": "agent_end", "agent_name": "browser", "timestamp": "..."}
data: {"type": "synthesis_start", "timestamp": "..."}
data: {"type": "synthesis_content", "data": "综合以上信息...", "timestamp": "..."}
data: {"type": "synthesis_end", "timestamp": "..."}
data: {"type": "graph_end", "timestamp": "..."}
```

## 核心实现

### 1. 数据结构设计 (agentService.ts)

```typescript
// Agent 节点数据
interface AgentNode {
  name: string
  displayName?: string
  status: 'loading' | 'success' | 'error'
  tools: Array<{ name: string; status: 'loading' | 'success' | 'error' }>
  content: string  // Agent 思考内容
}

// 思维链数据结构
interface ThoughtChainData {
  graph: { name: string; status: 'loading' | 'success' }
  nodes: Map<string, GraphNode>   // classify 等节点
  agents: Map<string, AgentNode>  // 并行执行的 Agents
  synthesis: { status: 'idle' | 'loading' | 'success'; description?: string }
}
```

### 2. SSE 流解析 (agentService.ts)

```typescript
class AgentStreamService {
  async streamQuery(query: string, callbacks: StreamCallbacks): Promise<void> {
    const response = await fetch(url, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ query }),
    })
    
    const reader = response.body?.getReader()
    await this.processStream(reader, callbacks)
  }

  private async processStream(reader, callbacks) {
    const decoder = new TextDecoder()
    let buffer = ''

    while (true) {
      const { done, value } = await reader.read()
      if (done) break

      buffer += decoder.decode(value, { stream: true })
      const lines = buffer.split('\n')
      buffer = lines.pop() || ''

      for (const line of lines) {
        if (line.startsWith('data: ')) {
          const event = JSON.parse(line.slice(6))
          this.handleEvent(event, callbacks)
        }
      }
    }
  }
}
```

### 3. 状态管理与深拷贝 (PhishingPlatform.tsx)

**关键点：Map 对象的深拷贝**

```typescript
// ❌ 错误：浅拷贝无法触发 React 更新
setMessages(prev => prev.map(msg =>
  msg.key === messageKey
    ? { ...msg, thoughtChain: { ...thoughtChain } }  // Map 引用未变
    : msg
))

// ✅ 正确：深拷贝整个 ThoughtChainData
const cloneThoughtChain = (): ThoughtChainData => ({
  graph: { ...thoughtChain.graph },
  nodes: new Map(thoughtChain.nodes),
  agents: new Map(
    Array.from(thoughtChain.agents.entries()).map(
      ([k, v]) => [k, { ...v, tools: [...v.tools] }]
    )
  ),
  synthesis: { ...thoughtChain.synthesis },
})
```

### 4. ThoughtChain 组件渲染

```typescript
// 构建 ThoughtChain items
function buildThoughtChainItems(data: ThoughtChainData): ThoughtChainItemType[] {
  const items = []

  // 分类节点
  if (data.nodes.get('classify')) {
    items.push({
      key: 'classify',
      title: '🔍 分析查询',
      status: data.nodes.get('classify').status,
    })
  }

  // 每个 Agent 独立展示
  data.agents.forEach((agent, agentName) => {
    items.push({
      key: `agent-${agentName}`,
      title: agent.displayName,
      status: agent.status,
      description: agent.tools.map(t => `${t.name} ${t.status === 'success' ? '✅' : '⏳'}`).join(' → '),
      content: agent.content,  // Agent 思考内容
    })
  })

  // 汇总节点
  if (data.synthesis.status !== 'idle') {
    items.push({
      key: 'synthesis',
      title: '🎯 汇总结果',
      status: data.synthesis.status,
    })
  }

  return items
}
```

## 调试方法

### 1. 控制台日志

在 `handleEvent` 中添加日志：

```typescript
private handleEvent(event: SSEEvent, callbacks: StreamCallbacks): void {
  console.log('SSE Event:', event.type, event)
  // ...
}
```

### 2. React DevTools

检查 `messages` 状态中的 `thoughtChain` 是否正确更新：
- 确认 `agents` Map 中有数据
- 确认 `content` 字段在流式更新

### 3. 网络面板

在 Chrome DevTools Network 面板中：
- 筛选 `EventStream` 类型
- 查看 SSE 消息是否正确接收

### 4. 模拟 SSE 数据

```typescript
// 测试用的模拟数据
const mockEvents = [
  { type: 'graph_start', query: 'test' },
  { type: 'classify_end', agents: ['browser'] },
  { type: 'agent_start', agent_name: 'browser', agent_display_name: '🌐 Browser' },
  { type: 'agent_content', agent_name: 'browser', data: '正在搜索...' },
  { type: 'agent_end', agent_name: 'browser' },
  { type: 'synthesis_start' },
  { type: 'synthesis_content', data: '结果汇总...' },
  { type: 'graph_end' },
]
```

## 二次开发指南

### 添加新的 Agent 类型

1. **后端**：在 LangGraph 中添加新的 Agent 节点
2. **前端**：无需修改，自动支持新 Agent（通过 `agent_name` 区分）

### 添加新的事件类型

1. 在 `SSEEvent.type` 中添加新类型
2. 在 `StreamCallbacks` 中添加对应回调
3. 在 `handleEvent` 中处理新事件
4. 在组件中调用新回调

### 自定义 ThoughtChain 样式

```css
/* 自定义 Agent 内容区域 */
.ant-thought-chain-item-content {
  background: rgba(102, 126, 234, 0.03);
  border-radius: 8px;
  max-height: 300px;
  overflow-y: auto;
}

/* 自定义状态图标颜色 */
.ant-thought-chain-item[data-status="loading"] .ant-thought-chain-item-icon {
  color: #1890ff;
}
```

### 扩展 Sender 功能

参考 `Sender.Switch` 添加新的功能开关：

```tsx
<Sender
  footer={(actionNode) => (
    <Flex>
      <Switch value={myFeature} onChange={setMyFeature}>
        我的功能
      </Switch>
      {actionNode}
    </Flex>
  )}
/>
```

## 常见问题与坑点

### 1. ThoughtChain 不更新

**原因**：Map 对象浅拷贝，React 检测不到变化
**解决**：使用深拷贝函数 `cloneThoughtChain()`

### 2. Agent 内容显示不完整

**原因**：使用 `description` 字段，默认会截断
**解决**：使用 `content` 字段显示完整内容

### 3. 汇总结果一直 loading

**原因**：缺少 `synthesis_end` 事件处理或 `graph_end` 时未更新状态
**解决**：在 `onGraphEnd` 中设置 `synthesis.status = 'success'`

### 4. Bubble.List 属性名混淆

**注意**：不同版本的 `@ant-design/x` 可能使用 `role` 或 `roles`
**解决**：查看 TypeScript 类型定义确认正确属性名

### 5. SSE 解析缓冲区问题

**原因**：SSE 数据可能跨多个 chunk
**解决**：使用 buffer 累积数据，按 `\n` 分割处理

## 文件结构

```
src/
├── services/
│   └── agentService.ts      # SSE 解析、事件处理、数据结构
├── pages/
│   └── PhishingPlatform/
│       ├── PhishingPlatform.tsx   # 主组件、状态管理
│       └── PhishingPlatform.css   # 样式
└── config/
    └── api.ts               # API 配置
```

## 依赖版本

```json
{
  "@ant-design/x": "^1.x",
  "@ant-design/x-markdown": "^1.x",
  "antd": "^5.x",
  "react": "^18.x"
}
```
