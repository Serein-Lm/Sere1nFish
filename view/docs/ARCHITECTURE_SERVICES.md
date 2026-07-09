# 服务层架构说明

## 架构概览

项目采用分层架构，将 UI 层和数据层分离，便于维护和扩展。

```
┌─────────────────────────────────────────┐
│         UI Layer (Components)           │
│  src/pages/PhishingPlatform/*.tsx       │
└──────────────┬──────────────────────────┘
               │
               │ 调用
               ▼
┌─────────────────────────────────────────┐
│       Service Layer (Services)          │
│     src/services/agentService.ts        │
└──────────────┬──────────────────────────┘
               │
               │ 使用配置
               ▼
┌─────────────────────────────────────────┐
│      Configuration (Config)             │
│        src/config/api.ts                │
└──────────────┬──────────────────────────┘
               │
               │ HTTP/SSE
               ▼
┌─────────────────────────────────────────┐
│         Backend API                     │
│    http://127.0.0.1:8001/sse/stream     │
└─────────────────────────────────────────┘
```

## 目录结构

```
src/
├── config/                 # 配置文件
│   └── api.ts             # API 配置（端点、超时等）
│
├── services/              # 服务层
│   ├── agentService.ts    # Agent SSE 流式服务
│   └── README.md          # 服务层文档
│
├── pages/                 # 页面组件
│   └── PhishingPlatform/
│       ├── PhishingPlatform.tsx   # UI 组件（只负责界面）
│       └── PhishingPlatform.css   # 样式
│
└── ...
```

## 各层职责

### 1. UI Layer (组件层)

**文件**: `src/pages/PhishingPlatform/PhishingPlatform.tsx`

**职责**:
- 渲染界面
- 处理用户交互
- 管理组件状态
- 调用服务层 API

**不应该做**:
- ❌ 直接使用 `fetch` 调用后端
- ❌ 处理 SSE 流解析
- ❌ 硬编码 API 地址

**示例**:
```typescript
import { agentService } from '../../services/agentService'

// ✅ 正确：通过服务层调用
await agentService.streamQuery(query, {
  onContent: (content) => {
    // 更新 UI
  }
})

// ❌ 错误：直接调用
await fetch('/api/sse/stream', { ... })
```

### 2. Service Layer (服务层)

**文件**: `src/services/agentService.ts`

**职责**:
- 封装所有 API 调用
- 处理 SSE 流解析
- 错误处理和重试
- 数据转换

**修改后端接口时只需修改这里**:
```typescript
// 修改请求逻辑
async streamQuery(query: string, callbacks: StreamCallbacks) {
  // 1. 修改请求参数
  const body = JSON.stringify({ 
    query,
    // 新增参数
    model: 'gpt-4',
  })
  
  // 2. 修改事件处理
  case 'new_event_type':
    // 处理新的事件类型
    break
}
```

### 3. Configuration Layer (配置层)

**文件**: `src/config/api.ts`

**职责**:
- 集中管理 API 配置
- 环境变量处理
- 端点定义

**修改 API 地址**:
```typescript
export const API_CONFIG = {
  // 开发环境
  BASE_URL: import.meta.env.DEV ? '/api' : 'http://127.0.0.1:8001',
  
  // 生产环境可以改为
  // BASE_URL: 'https://api.production.com',
}

export const API_ENDPOINTS = {
  SSE_STREAM: '/sse/stream',
  // 添加新端点
  NEW_ENDPOINT: '/new/endpoint',
}
```

## 修改后端接口的步骤

### 场景 1: 修改 API 地址

**只需修改**: `src/config/api.ts`

```typescript
export const API_CONFIG = {
  BASE_URL: 'http://新的地址:端口',
}
```

### 场景 2: 修改请求参数

**只需修改**: `src/services/agentService.ts`

```typescript
async streamQuery(query: string, callbacks: StreamCallbacks) {
  const response = await fetch(`${this.baseURL}${API_ENDPOINTS.SSE_STREAM}`, {
    method: 'POST',
    headers: REQUEST_HEADERS,
    body: JSON.stringify({ 
      query,
      // 添加新参数
      temperature: 0.7,
      max_tokens: 2000,
    }),
  })
}
```

### 场景 3: 处理新的事件类型

**只需修改**: `src/services/agentService.ts`

```typescript
private handleEvent(event: SSEEvent, ...) {
  switch (event.type) {
    case 'tool_start':
      // 现有逻辑
      break
    
    // 添加新事件类型
    case 'thinking':
      callbacks.onThinking?.(event.data)
      break
  }
}
```

然后更新类型定义：
```typescript
export interface SSEEvent {
  type: 'tool_start' | 'tool_end' | 'content' | 'done' | 'error' | 'thinking'
  // ...
}

export interface StreamCallbacks {
  // 添加新回调
  onThinking?: (data: string) => void
  // ...
}
```

### 场景 4: 添加新的 API 接口

1. **在 `src/config/api.ts` 添加端点**:
```typescript
export const API_ENDPOINTS = {
  SSE_STREAM: '/sse/stream',
  NEW_API: '/new/api',  // 新增
}
```

2. **在 `src/services/agentService.ts` 添加方法**:
```typescript
async callNewApi(params: any) {
  const response = await fetch(
    `${this.baseURL}${API_ENDPOINTS.NEW_API}`,
    {
      method: 'POST',
      headers: REQUEST_HEADERS,
      body: JSON.stringify(params),
    }
  )
  return response.json()
}
```

3. **在组件中使用**:
```typescript
const result = await agentService.callNewApi({ ... })
```

## 优势

### ✅ 关注点分离
- UI 组件只关心界面渲染
- 服务层只关心数据获取
- 配置层只关心配置管理

### ✅ 易于维护
- 修改后端接口不影响 UI 代码
- 修改 UI 不影响服务层
- 配置集中管理

### ✅ 易于测试
```typescript
// 可以轻松 mock 服务层
jest.mock('@/services/agentService', () => ({
  agentService: {
    streamQuery: jest.fn(),
  },
}))
```

### ✅ 代码复用
```typescript
// 多个组件可以共享同一个服务
import { agentService } from '@/services/agentService'

// 在任何组件中使用
await agentService.streamQuery(...)
```

## 最佳实践

1. **永远不要在组件中直接使用 fetch**
2. **所有 API 配置放在 `src/config/api.ts`**
3. **所有 API 调用放在 `src/services/` 目录**
4. **为所有请求和响应定义 TypeScript 类型**
5. **在服务层统一处理错误**
6. **使用环境变量区分开发和生产环境**

## 示例：完整的数据流

```typescript
// 1. 用户在 UI 输入查询
// PhishingPlatform.tsx
const handleSend = async (value: string) => {
  // 2. 调用服务层
  await streamResponse(value, messageKey)
}

// 3. 服务层处理请求
// agentService.ts
const streamResponse = async (query, messageKey) => {
  await agentService.streamQuery(query, {
    // 4. 通过回调更新 UI
    onContent: (content) => {
      setMessages(...)
    }
  })
}

// 5. 服务层从配置获取 API 地址
// api.ts
const response = await fetch(`${API_CONFIG.BASE_URL}${API_ENDPOINTS.SSE_STREAM}`)

// 6. 后端返回数据
// 7. 服务层解析 SSE 事件
// 8. 通过回调通知 UI
// 9. UI 更新界面
```

## 总结

这种架构确保了：
- **UI 组件 (.tsx)** 只负责界面构建
- **服务层 (.ts)** 负责所有后端交互
- **配置层 (.ts)** 集中管理配置

当需要修改后端接口时，只需修改 `src/services/` 和 `src/config/` 目录下的文件，UI 组件无需改动。
