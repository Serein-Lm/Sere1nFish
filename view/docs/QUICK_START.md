# Sere1nFish 快速开发指南

## 环境准备

### 必需软件
- Node.js >= 18.0.0
- npm >= 9.0.0

### 安装依赖
```bash
npm install
```

## 启动项目

### 开发模式
```bash
npm run dev
```
访问: http://localhost:5173

### 生产构建
```bash
npm run build
```

### 预览构建
```bash
npm run preview
```

## 登录系统

### 测试账号
```
管理员账号:
用户名: admin
密码: admin123
访问密钥: ACCESS-KEY-001

普通用户:
用户名: user
密码: user123
访问密钥: ACCESS-KEY-002
```

## 快速开发

### 1. 添加新页面

#### 步骤 1: 创建页面组件
```bash
# 创建页面目录
mkdir -p src/pages/NewPage

# 创建文件
touch src/pages/NewPage/NewPage.tsx
touch src/pages/NewPage/NewPage.css
```

#### 步骤 2: 编写页面组件
```typescript
// src/pages/NewPage/NewPage.tsx
import { Card } from 'antd'
import './NewPage.css'

export default function NewPage() {
  return (
    <div className="new-page page-container">
      <div className="page-header">
        <h1 className="page-title">新页面</h1>
        <p className="page-description">页面描述</p>
      </div>

      <Card className="glass-card">
        {/* 页面内容 */}
      </Card>
    </div>
  )
}
```

#### 步骤 3: 添加路由
```typescript
// src/App.tsx
import NewPage from './pages/NewPage/NewPage'

// 在 Routes 中添加
<Route path="new-page" element={<NewPage />} />
```

#### 步骤 4: 添加菜单项
```typescript
// src/components/Layout/MainLayout.tsx
const menuItems: MenuProps['items'] = [
  // ... 其他菜单项
  {
    key: '/new-page',
    icon: <YourIcon />,
    label: '新页面',
  },
]
```

### 2. 使用 Mock 数据

#### 添加 Mock 数据
```typescript
// src/utils/mockData.ts
export const newPageData = [
  { id: '1', name: '示例数据' },
  // ... 更多数据
]
```

#### 在组件中使用
```typescript
import { newPageData } from '../../utils/mockData'

export default function NewPage() {
  const [data, setData] = useState(newPageData)
  
  return (
    <div>
      {data.map(item => (
        <div key={item.id}>{item.name}</div>
      ))}
    </div>
  )
}
```

### 3. 使用统一样式

#### 使用 CSS Variables
```css
.my-component {
  background: var(--card-bg);
  border: 1px solid var(--border-color);
  border-radius: var(--radius-md);
  padding: var(--spacing-lg);
  color: var(--text-primary);
}

.my-component:hover {
  border-color: var(--border-hover);
  background: var(--accent-hover);
}
```

#### 使用通用类
```tsx
<Card className="glass-card hover-lift">
  {/* 毛玻璃效果 + 悬停上浮 */}
</Card>

<div className="page-container">
  {/* 页面容器 */}
</div>

<h1 className="page-title">标题</h1>
<p className="page-description">描述</p>
```

### 4. 添加对话功能

使用 Ant Design X 的 Bubble 和 Sender 组件：

```typescript
import { Bubble, Sender } from '@ant-design/x'

const [messages, setMessages] = useState([])
const [inputValue, setInputValue] = useState('')

const handleSend = (value: string) => {
  setMessages([...messages, {
    role: 'user',
    content: value,
  }])
}

return (
  <div>
    {messages.map((msg, idx) => (
      <Bubble
        key={idx}
        placement={msg.role === 'user' ? 'end' : 'start'}
        content={msg.content}
      />
    ))}
    
    <Sender
      value={inputValue}
      onChange={setInputValue}
      onSubmit={handleSend}
    />
  </div>
)
```

### 5. 表单处理

```typescript
import { Form, Input, Button } from 'antd'

const [form] = Form.useForm()

const handleSubmit = () => {
  form.validateFields().then(values => {
    console.log('表单数据:', values)
  })
}

return (
  <Form form={form} layout="vertical">
    <Form.Item
      name="name"
      label="名称"
      rules={[{ required: true, message: '请输入名称' }]}
    >
      <Input placeholder="请输入" />
    </Form.Item>
    
    <Button onClick={handleSubmit}>提交</Button>
  </Form>
)
```

### 6. 表格展示

```typescript
import { Table } from 'antd'
import type { ColumnsType } from 'antd/es/table'

interface DataType {
  id: string
  name: string
}

const columns: ColumnsType<DataType> = [
  {
    title: '名称',
    dataIndex: 'name',
    key: 'name',
  },
  {
    title: '操作',
    key: 'action',
    render: (_, record) => (
      <Button onClick={() => handleEdit(record)}>
        编辑
      </Button>
    ),
  },
]

return (
  <Table
    columns={columns}
    dataSource={data}
    rowKey="id"
  />
)
```

## 常用组件

### 统计卡片
```typescript
<Card className="stat-card glass-card hover-lift">
  <div className="stat-content">
    <div className="stat-icon" style={{ color: '#1890ff' }}>
      <YourIcon />
    </div>
    <div className="stat-info">
      <div className="stat-title">标题</div>
      <Statistic value={123} />
    </div>
  </div>
</Card>
```

### 项目卡片
```typescript
<div className="project-item">
  <div className="project-header">
    <h3 className="project-name">项目名称</h3>
    <Tag color="blue">进行中</Tag>
  </div>
  <Progress percent={75} />
</div>
```

### 对话气泡
```typescript
<Bubble
  avatar={<Avatar icon={<UserOutlined />} />}
  placement="end"
  content="消息内容"
  className="message-bubble"
/>
```

## 调试技巧

### 1. React DevTools
安装 React DevTools 浏览器扩展

### 2. 查看 Mock 数据
```typescript
import * as mockData from '../../utils/mockData'
console.log('所有Mock数据:', mockData)
```

### 3. 检查路由
```typescript
import { useLocation } from 'react-router-dom'

const location = useLocation()
console.log('当前路由:', location.pathname)
```

### 4. 调试样式
使用浏览器开发者工具查看 CSS Variables：
```javascript
getComputedStyle(document.documentElement)
  .getPropertyValue('--primary-bg')
```

## 常见问题

### Q: 页面空白？
1. 检查路由配置
2. 检查组件导入
3. 查看浏览器控制台错误

### Q: 样式不生效？
1. 确认 CSS 文件已导入
2. 检查类名拼写
3. 查看 CSS 优先级

### Q: Mock 数据不显示？
1. 确认数据已导出
2. 检查导入路径
3. 查看数据结构

### Q: 路由守卫失效？
1. 检查 token 是否存在
2. 确认 ProtectedRoute 包裹正确
3. 查看 localStorage

## 开发工具推荐

### VS Code 扩展
- ESLint
- Prettier
- TypeScript Vue Plugin (Volar)
- Auto Rename Tag
- Path Intellisense

### Chrome 扩展
- React Developer Tools
- Redux DevTools (如果使用)

## 性能优化建议

### 1. 使用 React.memo
```typescript
const MyComponent = React.memo(({ data }) => {
  return <div>{data}</div>
})
```

### 2. 使用 useMemo
```typescript
const expensiveValue = useMemo(() => {
  return computeExpensiveValue(data)
}, [data])
```

### 3. 使用 useCallback
```typescript
const handleClick = useCallback(() => {
  console.log('clicked')
}, [])
```

### 4. 懒加载
```typescript
const LazyComponent = React.lazy(() => import('./Component'))

<Suspense fallback={<Loading />}>
  <LazyComponent />
</Suspense>
```

## 部署

### 构建
```bash
npm run build
```

### 预览
```bash
npm run preview
```

### 部署到服务器
```bash
# 将 dist 目录上传到服务器
scp -r dist/* user@server:/path/to/web
```

## 下一步

1. 阅读 [架构文档](./ARCHITECTURE.md)
2. 查看 [API 文档](./API.md)
3. 开始开发你的功能！

## 获取帮助

- 查看项目 README
- 阅读 Ant Design 文档
- 查看 React 官方文档
