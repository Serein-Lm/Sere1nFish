# Services 目录说明

这个目录包含所有与后端 API 交互的服务层代码。

## 目录结构

```
src/services/
├── agentService.ts    # Agent SSE 流式服务
└── README.md          # 本文档
```

## agentService.ts

### 核心类：AgentStreamService

负责处理与后端 Agent 的 SSE 流式通信。

#### 主要方法

- `streamQuery(query, callbacks)` - 发送查询并处理流式响应

#### 使用示例

```typescript
import { agentService } from '@/services/agentService'

await agentService.streamQuery('你的查询', {
  onToolStart: (toolName, toolKey) => {
    console.log('工具开始:', toolName)
  },
  onToolEnd: (toolKey) => {
    console.log('工具完成')
  },
  onContent: (content) => {
    console.log('内容:', content)
  },
  onDone: () => {
    console.log('完成')
  },
  onError: (error) => {
    console.error('错误:', error)
  },
})
```

### 工具函数

- `createThoughtChainItem()` - 创建 ThoughtChain 条目
- `updateThoughtChainItem()` - 更新 ThoughtChain 条目状态

## 修改后端接口

如果需要修改后端 API 地址或配置，请编辑：

1. **API 配置**: `src/config/api.ts`
   - 修改 `API_CONFIG.BASE_URL`
   - 修改 `API_ENDPOINTS`

2. **服务实现**: `src/services/agentService.ts`
   - 修改请求逻辑
   - 修改事件处理

## 添加新的服务

创建新的服务文件，例如 `userService.ts`：

```typescript
import { API_CONFIG, REQUEST_HEADERS } from '../config/api'

export class UserService {
  async getUser(id: string) {
    const response = await fetch(`${API_CONFIG.BASE_URL}/users/${id}`, {
      headers: REQUEST_HEADERS,
    })
    return response.json()
  }
}

export const userService = new UserService()
```

## 注意事项

1. **不要在组件中直接使用 fetch** - 始终通过服务层
2. **错误处理** - 服务层应该处理所有网络错误
3. **类型安全** - 为所有请求和响应定义 TypeScript 类型
4. **配置集中** - 所有 API 配置应在 `src/config/api.ts` 中管理
