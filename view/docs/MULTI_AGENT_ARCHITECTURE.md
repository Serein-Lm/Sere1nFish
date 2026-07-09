# 多智能体思维链架构设计方案

> 本文档用于前后端协商，定义可扩展的 SSE 流式协议和前端渲染方案

## 一、当前问题

### 1.1 现有实现的局限性

| 问题 | 描述 |
|------|------|
| 扁平化结构 | graph、classify、agents、tools 在同一层级，无法表达父子关系 |
| 硬编码事件 | 新增 Agent 类型需要修改 `handleEvent` 的 switch-case |
| Tool 展示受限 | 工具调用只能显示为文本列表，无法展示嵌套的子工具链 |
| 状态管理复杂 | 使用多个 Map 分别管理 nodes、agents，更新逻辑分散 |

### 1.2 未来需求

- 支持 Agent 嵌套调用（Agent A 调用 Agent B）
- 支持 Tool 嵌套（Tool 内部调用子 Tool）
- 支持并行执行的可视化
- 支持用户交互（暂停、重试、取消特定节点）
- 支持执行时间统计和性能分析

---

## 二、推荐方案：树形节点 + 路径定位

### 2.1 核心思想

用 **统一的树形结构** 替代当前的扁平结构，通过 **路径（path）** 定位节点位置，实现：
- 无限层级嵌套
- 动态扩展（无需修改代码即可支持新 Agent/Tool）
- 前端使用嵌套 `ThoughtChain` 组件渲染

### 2.2 节点数据结构

```typescript
interface ExecutionNode {
  id: string                    // 唯一标识（由后端生成）
  type: NodeType                // 节点类型
  name: string                  // 内部名称（如 "browser", "web_search"）
  displayName: string           // 显示名称（如 "🌐 浏览器智能体"）
  status: NodeStatus            // 执行状态
  
  // 可选字段
  icon?: string                 // 图标（emoji 或 icon 名称）
  description?: string          // 描述信息
  content?: string              // 流式内容（逐字累加）
  metadata?: Record<string, any>// 扩展元数据
  
  // 时间信息
  startTime?: number            // 开始时间戳
  endTime?: number              // 结束时间戳
  duration?: number             // 执行耗时（ms）
  
  // 树形结构
  children: ExecutionNode[]     // 子节点
}

type NodeType = 
  | 'graph'      // 顶层 Graph
  | 'phase'      // 阶段（classify, synthesis）
  | 'agent'      // 智能体
  | 'tool'       // 工具调用
  | 'subgraph'   // 子图（Agent 嵌套调用）

type NodeStatus = 
  | 'pending'    // 等待执行
  | 'loading'    // 执行中
  | 'success'    // 成功
  | 'error'      // 失败
  | 'abort'      // 用户取消
```

---

## 三、SSE 协议设计（需与后端协商）

### 3.1 统一事件格式

```typescript
interface SSEEvent {
  event: EventType              // 事件类型
  path: string                  // 节点路径（用于定位）
  data: EventData               // 事件数据
  timestamp: string             // ISO 时间戳
  traceId?: string              // 追踪 ID（用于调试）
}

type EventType = 
  | 'node_start'    // 节点开始
  | 'node_update'   // 节点更新（状态、描述等）
  | 'node_end'      // 节点结束
  | 'content'       // 流式内容
  | 'error'         // 错误
  | 'heartbeat'     // 心跳（保持连接）

interface EventData {
  type?: NodeType
  name?: string
  displayName?: string
  icon?: string
  status?: NodeStatus
  description?: string
  content?: string              // 流式内容片段
  error?: string                // 错误信息
  metadata?: Record<string, any>
}
```

### 3.2 路径规范

路径使用 `/` 分隔，表示节点的层级关系：

```
graph                           # 顶层 Graph
graph/classify                  # 分类阶段
graph/agents                    # Agents 容器（可选）
graph/agents/browser            # Browser Agent
graph/agents/browser/tools/search   # Browser 的 search 工具
graph/agents/browser/tools/click    # Browser 的 click 工具
graph/agents/xhs                # 小红书 Agent
graph/agents/xhs/tools/fetch    # 小红书的 fetch 工具
graph/synthesis                 # 汇总阶段
```

### 3.3 完整事件流示例

```
# 1. Graph 开始
data: {"event":"node_start","path":"graph","data":{"type":"graph","name":"multi_agent_search","displayName":"🚀 多智能体搜索","icon":"rocket"},"timestamp":"2024-01-01T00:00:00Z"}

# 2. 分类阶段开始
data: {"event":"node_start","path":"graph/classify","data":{"type":"phase","displayName":"🔍 分析查询","description":"正在理解用户意图..."},"timestamp":"..."}

# 3. 分类阶段结束，返回选中的 Agents
data: {"event":"node_end","path":"graph/classify","data":{"status":"success","metadata":{"selectedAgents":["browser","xhs"],"reasoning":"用户需要搜索信息，选择浏览器和小红书智能体"}},"timestamp":"..."}

# 4. Browser Agent 开始
data: {"event":"node_start","path":"graph/agents/browser","data":{"type":"agent","name":"browser","displayName":"🌐 Browser Agent","description":"网页搜索与信息提取"},"timestamp":"..."}

# 5. Browser 调用 search 工具
data: {"event":"node_start","path":"graph/agents/browser/tools/search","data":{"type":"tool","name":"web_search","displayName":"🔎 网页搜索","metadata":{"query":"AI 最新进展"}},"timestamp":"..."}

# 6. search 工具完成
data: {"event":"node_end","path":"graph/agents/browser/tools/search","data":{"status":"success","metadata":{"resultCount":10}},"timestamp":"..."}

# 7. Browser Agent 流式输出思考内容
data: {"event":"content","path":"graph/agents/browser","data":{"content":"根据搜索结果，"},"timestamp":"..."}
data: {"event":"content","path":"graph/agents/browser","data":{"content":"AI 领域最近有以下进展..."},"timestamp":"..."}

# 8. Browser Agent 结束
data: {"event":"node_end","path":"graph/agents/browser","data":{"status":"success"},"timestamp":"..."}

# 9. 小红书 Agent（与 Browser 并行）
data: {"event":"node_start","path":"graph/agents/xhs","data":{"type":"agent","name":"xhs","displayName":"📕 小红书 Agent"},"timestamp":"..."}
# ... 类似流程

# 10. 汇总阶段
data: {"event":"node_start","path":"graph/synthesis","data":{"type":"phase","displayName":"🎯 汇总结果"},"timestamp":"..."}
data: {"event":"content","path":"graph/synthesis","data":{"content":"综合以上信息，"},"timestamp":"..."}
data: {"event":"node_end","path":"graph/synthesis","data":{"status":"success"},"timestamp":"..."}

# 11. Graph 结束
data: {"event":"node_end","path":"graph","data":{"status":"success","metadata":{"totalDuration":5230}},"timestamp":"..."}
```

---

## 四、前端需要传给后端的参数

### 4.1 请求参数

```typescript
interface QueryRequest {
  // 基础参数
  query: string                 // 用户输入
  conversationId?: string       // 会话 ID（多轮对话）
  
  // 执行控制
  config?: {
    deepThink?: boolean         // 是否启用深度思考
    selectedAgents?: string[]   // 指定使用的 Agents（可选）
    maxAgents?: number          // 最大并行 Agent 数
    timeout?: number            // 超时时间（ms）
  }
  
  // 流式控制
  streamConfig?: {
    includeContent?: boolean    // 是否返回 content 事件
    includeMetadata?: boolean   // 是否返回 metadata
    heartbeatInterval?: number  // 心跳间隔（ms）
  }
  
  // 上下文
  context?: {
    files?: FileReference[]     // 引用的文件
    history?: Message[]         // 历史消息（可选，后端可能自己管理）
  }
}

interface FileReference {
  id: string
  name: string
  type: 'image' | 'document' | 'code'
  url?: string
}
```

### 4.2 用户交互事件（WebSocket 或单独 API）

```typescript
// 前端发送给后端的控制命令
interface ControlCommand {
  type: 'abort' | 'retry' | 'pause' | 'resume'
  path?: string                 // 目标节点路径（可选，不填则作用于整个 Graph）
  traceId: string               // 当前执行的追踪 ID
}

// 示例：取消特定 Agent
{ "type": "abort", "path": "graph/agents/browser", "traceId": "xxx" }

// 示例：重试失败的工具
{ "type": "retry", "path": "graph/agents/xhs/tools/fetch", "traceId": "xxx" }
```

---

## 五、前端渲染方案

### 5.1 使用嵌套 ThoughtChain

Ant Design X 的 `ThoughtChain` 支持在 `content` 中嵌套另一个 `ThoughtChain`：

```tsx
// 递归渲染节点
function renderNode(node: ExecutionNode): ThoughtChainItemType {
  const hasChildren = node.children.length > 0
  
  return {
    key: node.id,
    title: node.displayName,
    icon: getIcon(node.icon),
    status: mapStatus(node.status),
    description: node.description,
    collapsible: hasChildren,
    
    // 嵌套渲染
    content: hasChildren ? (
      <ThoughtChain 
        items={node.children.map(renderNode)} 
        line="dashed"
      />
    ) : node.content ? (
      <div className="node-content">
        <XMarkdown content={node.content} />
      </div>
    ) : undefined,
    
    // 底部操作区
    footer: node.status === 'error' ? (
      <Button size="small" onClick={() => handleRetry(node.id)}>
        重试
      </Button>
    ) : undefined,
  }
}
```

### 5.2 状态管理

```typescript
// 使用 Map 快速定位节点
class ExecutionTree {
  root: ExecutionNode | null = null
  nodeMap: Map<string, ExecutionNode> = new Map()
  
  // 根据 path 获取或创建节点
  upsertNode(path: string, data: Partial<ExecutionNode>): void {
    const segments = path.split('/')
    let current = this.root
    
    for (let i = 0; i < segments.length; i++) {
      const segment = segments[i]
      const currentPath = segments.slice(0, i + 1).join('/')
      
      if (i === 0) {
        // 根节点
        if (!this.root) {
          this.root = this.createNode(currentPath, data)
          this.nodeMap.set(currentPath, this.root)
        }
        current = this.root
      } else {
        // 子节点
        let child = current.children.find(c => c.name === segment)
        if (!child) {
          child = this.createNode(currentPath, { name: segment })
          current.children.push(child)
          this.nodeMap.set(currentPath, child)
        }
        current = child
      }
    }
    
    // 更新目标节点
    Object.assign(current, data)
  }
  
  // 追加流式内容
  appendContent(path: string, content: string): void {
    const node = this.nodeMap.get(path)
    if (node) {
      node.content = (node.content || '') + content
    }
  }
  
  // 转换为 ThoughtChain items
  toItems(): ThoughtChainItemType[] {
    return this.root ? [renderNode(this.root)] : []
  }
}
```

### 5.3 视觉效果增强

```css
/* 嵌套层级缩进 */
.ant-thought-chain .ant-thought-chain {
  margin-left: 24px;
  border-left: 2px dashed #e8e8e8;
  padding-left: 16px;
}

/* Agent 节点样式 */
.node-agent {
  background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
  -webkit-background-clip: text;
  -webkit-text-fill-color: transparent;
}

/* Tool 节点样式 */
.node-tool {
  font-size: 13px;
  color: #666;
}

/* 执行中动画 */
.node-loading .ant-thought-chain-node-title {
  animation: pulse 1.5s ease-in-out infinite;
}

/* 并行执行指示器 */
.parallel-indicator {
  display: flex;
  align-items: center;
  gap: 8px;
  color: #1890ff;
  font-size: 12px;
}
```

---

## 六、对比分析

| 维度 | 当前方案 | 新方案 |
|------|---------|--------|
| 节点层级 | 固定 2 层 | 无限嵌套 |
| 新增 Agent | 需改 switch-case | 自动支持 |
| Tool 展示 | 扁平文本 | 嵌套在 Agent 下 |
| 并行可视化 | 不支持 | 支持（同级节点） |
| 用户交互 | 仅全局取消 | 支持节点级操作 |
| 后端协议 | 12+ 种事件类型 | 5 种统一事件 |
| 前端代码量 | ~400 行 | ~250 行（更简洁） |

---

## 七、实施计划

### Phase 1：协议升级（1-2 天）
- [ ] 后端实现新的 SSE 事件格式
- [ ] 保持向后兼容（可同时支持旧格式）
- [ ] 添加 traceId 用于调试

### Phase 2：前端重构（2-3 天）
- [ ] 实现 `ExecutionTree` 状态管理类
- [ ] 创建递归渲染组件
- [ ] 适配新旧两种协议

### Phase 3：功能增强（1-2 天）
- [ ] 添加节点折叠/展开控制
- [ ] 支持节点点击查看详情
- [ ] 添加执行时间统计
- [ ] 支持重试失败节点

### Phase 4：优化（持续）
- [ ] 性能优化（虚拟滚动）
- [ ] 动画效果优化
- [ ] 错误处理完善

---

## 八、FAQ

### Q1: 为什么用 path 而不是 parentId？
A: path 可以直接表达完整的层级关系，前端无需遍历查找父节点，定位更高效。

### Q2: 如何处理并行执行的 Agents？
A: 同一父节点下的多个子节点天然表示并行。前端可以通过 `startTime` 判断是否同时开始。

### Q3: 旧协议如何兼容？
A: 前端可以实现一个适配层，将旧事件转换为新格式：
```typescript
function adaptLegacyEvent(event: LegacySSEEvent): SSEEvent {
  // classify_start -> node_start + path="graph/classify"
  // agent_start -> node_start + path="graph/agents/{name}"
  // ...
}
```

### Q4: 心跳机制是否必要？
A: 建议实现。SSE 连接可能因网络问题静默断开，心跳可以帮助前端检测连接状态。

---

## 九、联系方式

如有问题，请联系：
- 前端：[前端负责人]
- 后端：[后端负责人]

文档版本：v1.0
更新日期：2024-12-25
