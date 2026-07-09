# 简化版 Mock SSE 使用指南

## 核心特点

### 1. 并行执行展示
Browser 和 Bid 两个 Agent **同时执行**，事件交错输出：

```
时间轴：
t1: browser start
t2: bid start        ← 并行开始
t3: browser.tool1 start
t4: bid.tool1 start  ← 交错执行
t5: browser.tool1 end
t6: browser.tool2 start
t7: bid.tool1 update ← 交错更新
t8: browser.tool2 end
t9: browser content  ← 交错输出内容
t10: bid.tool1 end
t11: browser end
t12: bid content
t13: bid end         ← 并行结束
```

### 2. 正确的嵌套层次结构

```
graph (router_copywriting) ← 主工作流
  │
  ├─ router (子graph) ← Router 子工作流
  │   ├─ classify (node)
  │   ├─ browser (node) ────────┐
  │   │   ├─ tools.get_domain   │ 并行
  │   │   └─ tools.web_scraper  │
  │   │                         │
  │   ├─ bid (node) ────────────┘
  │   │   └─ tools.get_bids
  │   └─ synthesize (node)
  │
  └─ copywriting (子graph) ← Copywriting 子工作流
      ├─ scenario (node)
      ├─ script (node)
      ├─ objection (node)
      └─ finalize (node)
```

### 3. 路径命名规则

| 层级 | 路径示例 | 说明 |
|------|---------|------|
| Graph | `graph` | 主工作流根节点 |
| Subgraph | `graph.router` | 子工作流（Router） |
| Subgraph | `graph.copywriting` | 子工作流（Copywriting） |
| Node | `graph.router.browser` | Router 子图中的节点 |
| Tool | `graph.router.browser.tools.get_domain` | 节点中的工具 |

## 前端嵌套展示建议

### 方案 1：树形结构（正确的嵌套）

```jsx
<Graph name="router_copywriting">
  <Subgraph name="router" displayName="📊 Router - 信息采集">
    <Node name="classify" />
    
    <ParallelGroup>
      <Node name="browser">
        <Tool name="get_domain" />
        <Tool name="web_scraper" />
        <Content>华为官网：www.huawei.com...</Content>
      </Node>
      
      <Node name="bid">
        <Tool name="get_bids" />
        <Content>招标项目23个...</Content>
      </Node>
    </ParallelGroup>
    
    <Node name="synthesize">
      <Content>华为是全球领先...</Content>
    </Node>
  </Subgraph>
  
  <Subgraph name="copywriting" displayName="✍️ Copywriting - 文案生成">
    <Node name="scenario">
      <Content>目标客户：某省政务局...</Content>
    </Node>
    <Node name="script">
      <Content>开场白：您好...</Content>
    </Node>
    <Node name="objection">
      <Content>Q: 价格太贵？...</Content>
    </Node>
    <Node name="finalize">
      <Content>完整销售文案已生成...</Content>
    </Node>
  </Subgraph>
</Graph>
```

### 方案 2：时间轴视图

```
🚀 router_copywriting (主工作流)
│
├─ 📊 Router 子工作流 (9000ms)
│   ├─ [完成] classify (300ms)
│   ├─ [并行执行]
│   │   ├─ [完成] browser (3000ms)
│   │   │   ├─ get_domain (800ms)
│   │   │   └─ web_scraper (650ms)
│   │   └─ [完成] bid (4000ms)
│   │       └─ get_bids (1200ms)
│   └─ [完成] synthesize (2000ms)
│
└─ ✍️ Copywriting 子工作流 (6900ms)
    ├─ [完成] scenario (1500ms)
    ├─ [完成] script (1800ms)
    ├─ [完成] objection (1600ms)
    └─ [完成] finalize (2000ms)
```

## 事件处理逻辑

### 1. 识别节点类型

```javascript
// 根据 path 和 data.type 识别节点类型
const pathParts = event.path.split('.');
const nodeType = event.data.type;

if (nodeType === 'graph') {
  // 主工作流：graph
  renderMainGraph(event);
} else if (nodeType === 'subgraph') {
  // 子工作流：graph.router, graph.copywriting
  renderSubgraph(event);
} else if (nodeType === 'node') {
  // 普通节点：graph.router.classify, graph.router.synthesize
  renderNode(event);
} else if (nodeType === 'agent') {
  // Agent 节点：graph.router.browser, graph.router.bid
  renderAgent(event);
} else if (nodeType === 'tool') {
  // 工具：graph.router.browser.tools.get_domain
  renderTool(event);
}
```

### 2. 识别并行节点

```javascript
// 通过 meta.parallel 标识
if (event.data.meta?.parallel === true) {
  // 这是并行节点，可以同时显示
  renderParallelNode(event);
} else {
  // 这是串行节点，按顺序显示
  renderSequentialNode(event);
}
```

### 3. 构建嵌套结构

```javascript
// 根据 path 构建层次
const pathParts = event.path.split('.');
// ["graph", "router", "browser", "tools", "get_domain"]

const level = pathParts.length;
const nodeType = event.data.type;

switch(level) {
  case 1: // graph
    renderGraph(event);
    break;
  case 2: // graph.router (subgraph)
    renderSubgraph(event);
    break;
  case 3: // graph.router.browser (node or agent)
    if (nodeType === 'agent') {
      renderAgent(event);
    } else {
      renderNode(event);
    }
    break;
  case 5: // graph.router.browser.tools.get_domain (tool)
    renderTool(event);
    break;
}
```

### 4. 内容累积

```javascript
// 每个节点独立累积内容
const contentMap = new Map();

if (event.event === 'content') {
  const nodePath = event.path; // "graph.router.browser"
  const current = contentMap.get(nodePath) || '';
  contentMap.set(nodePath, current + event.data.content);
}
```

### 5. 子图分组

```javascript
// 根据 meta.subgraph 分组
const subgraph = event.data.meta?.subgraph;
if (subgraph === 'router') {
  addToRouterSubgraph(event);
} else if (subgraph === 'copywriting') {
  addToCopywritingSubgraph(event);
}
```

## 测试方法

### 1. 启动服务

```bash
cd test_server
python run.py
```

### 2. 测试简化版 Mock

```bash
curl -X POST http://127.0.0.1:8001/mock-simple/stream-simple \
  -H "Content-Type: application/json" \
  -d '{
    "query": "查询华为信息",
    "workflow": "router_copywriting",
    "delay": 0.1
  }'
```

### 3. 调整延迟参数

```javascript
// 快速测试（看整体结构）
{ "delay": 0.01 }

// 正常速度（看并行效果）
{ "delay": 0.1 }

// 慢速演示（看清楚每个事件）
{ "delay": 0.5 }
```

## 事件序列示例

```json
// 1. 主 Graph 开始
{"event": "start", "path": "graph", "data": {"type": "graph"}}

// 2. Router 子图开始
{"event": "start", "path": "graph.router", "data": {"type": "subgraph"}}

// 3. Classify 节点
{"event": "start", "path": "graph.router.classify", "data": {"type": "node"}}
{"event": "update", "path": "graph.router.classify", "data": {"agents": ["browser", "bid"]}}
{"event": "end", "path": "graph.router.classify", "data": {"status": "success"}}

// 4. Browser 和 Bid 并行开始
{"event": "start", "path": "graph.router.browser", "data": {"parallel": true}}
{"event": "start", "path": "graph.router.bid", "data": {"parallel": true}}

// 5. 工具调用交错
{"event": "start", "path": "graph.router.browser.tools.get_domain"}
{"event": "start", "path": "graph.router.bid.tools.get_bids"}
{"event": "end", "path": "graph.router.browser.tools.get_domain"}
{"event": "start", "path": "graph.router.browser.tools.web_scraper"}
{"event": "update", "path": "graph.router.bid.tools.get_bids"}
{"event": "end", "path": "graph.router.browser.tools.web_scraper"}

// 6. 内容输出交错
{"event": "content", "path": "graph.router.browser", "data": {"content": "华"}}
{"event": "content", "path": "graph.router.browser", "data": {"content": "为"}}
{"event": "end", "path": "graph.router.bid.tools.get_bids"}
{"event": "end", "path": "graph.router.browser"}
{"event": "content", "path": "graph.router.bid", "data": {"content": "招"}}
{"event": "end", "path": "graph.router.bid"}

// 7. Synthesize 节点
{"event": "start", "path": "graph.router.synthesize"}
{"event": "content", "path": "graph.router.synthesize", "data": {"content": "..."}}
{"event": "end", "path": "graph.router.synthesize"}

// 8. Router 子图结束
{"event": "end", "path": "graph.router", "data": {"status": "success"}}

// 9. Copywriting 子图开始
{"event": "start", "path": "graph.copywriting", "data": {"type": "subgraph"}}

// 10. Copywriting 节点串行执行
{"event": "start", "path": "graph.copywriting.scenario"}
{"event": "content", "path": "graph.copywriting.scenario"}
{"event": "end", "path": "graph.copywriting.scenario"}

{"event": "start", "path": "graph.copywriting.script"}
{"event": "content", "path": "graph.copywriting.script"}
{"event": "end", "path": "graph.copywriting.script"}

{"event": "start", "path": "graph.copywriting.objection"}
{"event": "content", "path": "graph.copywriting.objection"}
{"event": "end", "path": "graph.copywriting.objection"}

{"event": "start", "path": "graph.copywriting.finalize"}
{"event": "content", "path": "graph.copywriting.finalize"}
{"event": "end", "path": "graph.copywriting.finalize"}

// 11. Copywriting 子图结束
{"event": "end", "path": "graph.copywriting", "data": {"status": "success"}}

// 12. 主 Graph 结束
{"event": "end", "path": "graph", "data": {"status": "success"}}
```

## 关键差异对比

| 特性 | 详细版 (/mock) | 简化版 (/mock-simple) |
|------|---------------|---------------------|
| 内容长度 | 15,000+ 字 | 200 字左右 |
| 重点 | 真实数据展示 | 结构和并行展示 |
| 工具调用 | 详细的进度更新 | 简化的开始/结束 |
| Meta 信息 | 丰富的统计数据 | 核心标识字段 |
| 适用场景 | 内容展示测试 | 结构和交互测试 |

## 前端实现建议

### 1. 状态管理（正确的嵌套结构）

```javascript
const state = {
  graph: { 
    status: 'running', 
    startTime: 0,
    subgraphs: ['router', 'copywriting']
  },
  subgraphs: {
    'graph.router': { 
      status: 'completed', 
      duration: 9000,
      nodes: ['classify', 'browser', 'bid', 'synthesize']
    },
    'graph.copywriting': { 
      status: 'running', 
      duration: 0,
      nodes: ['scenario', 'script', 'objection', 'finalize']
    }
  },
  nodes: {
    'graph.router.classify': { status: 'completed', duration: 300 },
    'graph.router.browser': { status: 'completed', parallel: true },
    'graph.router.bid': { status: 'completed', parallel: true },
    'graph.router.synthesize': { status: 'completed' },
    'graph.copywriting.scenario': { status: 'running' }
  },
  tools: {
    'graph.router.browser.tools.get_domain': { status: 'completed' },
    'graph.router.browser.tools.web_scraper': { status: 'completed' },
    'graph.router.bid.tools.get_bids': { status: 'completed' }
  },
  contents: {
    'graph.router.browser': '华为官网：www.huawei.com...',
    'graph.router.bid': '招标项目23个...',
    'graph.router.synthesize': '华为是全球领先...',
    'graph.copywriting.scenario': '目标客户：某省政务局...'
  }
};
```

### 2. 嵌套渲染

```javascript
// 渲染主 Graph
<MainGraph path="graph">
  {state.graph.subgraphs.map(subgraphName => (
    <Subgraph 
      key={subgraphName} 
      path={`graph.${subgraphName}`}
    >
      {/* 渲染子图中的节点 */}
      {state.subgraphs[`graph.${subgraphName}`].nodes.map(nodeName => (
        <Node 
          key={nodeName}
          path={`graph.${subgraphName}.${nodeName}`}
        />
      ))}
    </Subgraph>
  ))}
</MainGraph>
```

### 3. 并行节点渲染

```javascript
// 在 Router 子图中检测并行节点
const routerNodes = state.subgraphs['graph.router'].nodes;
const parallelNodes = routerNodes
  .filter(nodeName => {
    const nodePath = `graph.router.${nodeName}`;
    return state.nodes[nodePath]?.parallel === true;
  });

// 并排显示
<div className="parallel-container">
  {parallelNodes.map(nodeName => (
    <NodeCard key={nodeName} path={`graph.router.${nodeName}`} />
  ))}
</div>
```

### 4. 工具折叠展示

```javascript
<Node path="graph.router.browser">
  <NodeHeader>🌐 官网采集</NodeHeader>
  <ToolsCollapse>
    <Tool path="graph.router.browser.tools.get_domain" />
    <Tool path="graph.router.browser.tools.web_scraper" />
  </ToolsCollapse>
  <NodeContent>{contents['graph.router.browser']}</NodeContent>
</Node>
```

## 总结

简化版 Mock 的核心价值：
1. ✅ **清晰展示并行执行**：browser 和 bid 交错输出
2. ✅ **正确的嵌套层次**：graph → subgraph → node/agent → tool
3. ✅ **两个子工作流**：router（信息采集）和 copywriting（文案生成）
4. ✅ **最小化内容干扰**：重点是结构，不是数据
5. ✅ **易于前端测试**：快速验证嵌套和并行逻辑

## 关键层次对应

| 概念 | 路径示例 | 类型 | 说明 |
|------|---------|------|------|
| 主工作流 | `graph` | graph | router_copywriting |
| 子工作流 | `graph.router` | subgraph | Router 信息采集 |
| 子工作流 | `graph.copywriting` | subgraph | Copywriting 文案生成 |
| 普通节点 | `graph.router.classify` | node | 分类节点 |
| Agent 节点 | `graph.router.browser` | agent | Browser Agent |
| 工具 | `graph.router.browser.tools.get_domain` | tool | 查询域名工具 |


---

## Final 事件（最终结果）⭐ 新增

### 说明

`final` 事件在整个工作流执行完成后发送，包含最终的汇总结果，显示在前端界面底部。

### 事件格式

```json
{
  "event": "final",
  "id": "router_copywriting_final_1704441603400",
  "path": "graph",
  "ts": 1704441603400,
  "data": {
    "content": "# 完整销售方案\n\n## 企业信息\n...",
    "summary": {
      "total_duration": 15900,
      "nodes_executed": 8,
      "agents_used": ["browser", "bid"],
      "success": true
    }
  },
  "workflow": "router_copywriting",
  "agent": null
}
```

### 字段说明

| 字段 | 类型 | 说明 |
|------|------|------|
| `event` | string | 固定为 "final" |
| `path` | string | 固定为 "graph" |
| `data.content` | string | 最终结果内容（Markdown 格式） |
| `data.summary` | object | 执行摘要（可选） |

### 触发时机

- 在 `graph` 的 `end` 事件之后
- 所有子图和节点都已完成
- 作为整个执行流程的最后一个事件

### 前端处理

```javascript
if (event.event === 'final') {
  // 显示在界面底部的最终结果区域
  renderFinalResult(event.data.content);
  
  // 显示执行摘要
  if (event.data.summary) {
    renderSummary(event.data.summary);
  }
}
```

### 完整事件序列

```
1. graph (start)
2. graph.router (start)
3. ... router 子图执行 ...
4. graph.router (end)
5. graph.copywriting (start)
6. ... copywriting 子图执行 ...
7. graph.copywriting (end)
8. graph (end)
9. graph (final) ← 最终结果
```
