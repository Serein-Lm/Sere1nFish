# Sere1nFish 前端架构文档

## 项目概述

Sere1nFish（AI钓鱼中台）是一个基于 React + TypeScript 的现代化前端应用，采用深灰色主题，提供响应式、动画丰富的用户界面。

## 技术选型

### 核心技术
- **React 19**: 最新版本的 React 框架
- **TypeScript**: 类型安全
- **Vite**: 快速的构建工具
- **React Router v6**: 路由管理

### UI 框架
- **Ant Design**: 企业级 UI 组件库
- **Ant Design X**: AI 对话组件（Bubble、Sender、ThoughtChain）

### 样式方案
- **CSS + CSS Variables**: 统一主题管理
- **响应式设计**: 支持多端适配
- **动画效果**: 丰富的交互反馈

## 目录结构

```
src/
├── components/              # 可复用组件
│   ├── Login/              # 登录页面
│   │   ├── Login.tsx
│   │   └── Login.css
│   ├── Layout/             # 主布局
│   │   ├── MainLayout.tsx
│   │   └── MainLayout.css
│   └── PlaceholderPage/    # 占位页面组件
│       ├── PlaceholderPage.tsx
│       └── PlaceholderPage.css
│
├── pages/                  # 页面组件
│   ├── Dashboard/          # 仪表盘
│   │   ├── Dashboard.tsx
│   │   └── Dashboard.css
│   ├── PhishingPlatform/   # 钓鱼中台
│   │   ├── PhishingPlatform.tsx
│   │   └── PhishingPlatform.css
│   ├── ProjectManagement/  # 项目管理
│   │   ├── ProjectManagement.tsx
│   │   └── ProjectManagement.css
│   └── AITools/            # AI工具
│       ├── AITools.tsx
│       └── AITools.css
│
├── router/                 # 路由配置
│   └── ProtectedRoute.tsx  # 路由守卫
│
├── utils/                  # 工具函数
│   └── mockData.ts         # Mock 数据中心
│
├── styles/                 # 全局样式
│   └── theme.css           # 主题变量
│
├── App.tsx                 # 应用入口
├── main.tsx                # 主文件
└── index.css               # 全局基础样式
```

## 核心功能模块

### 1. 仪表盘 (Dashboard)
**路径**: `/dashboard`

**功能**:
- 9个统计卡片（项目、社交账号、AI工具、案例库、数据量、信息来源、Agent、MCP、Tool）
- 项目进展列表
- 信息收集来源统计
- 系统资源监控

**数据来源**: `mockData.dashboardStats`, `mockData.projectList`, `mockData.infoSources`

### 2. 钓鱼中台 (PhishingPlatform)
**路径**: `/phishing`

**功能**:
- AI 对话式交互（使用 Ant Design X Bubble + Sender）
- 快速操作按钮
- 功能模块展示
- 实时消息流

**特点**:
- 使用 Bubble 组件展示对话
- 支持用户和 AI 角色区分
- 动画效果丰富

### 3. 项目管理 (ProjectManagement)
**路径**: `/projects`

**功能**:
- 项目列表（全部/进行中/已完成）
- 项目增删改查
- 项目详情查看
- 标签管理

**数据来源**: `mockData.projectList`

### 4. AI工具 (AITools)
**路径**: `/ai-tools`

**功能**:
- AI-TTS 语音合成
- AI 图片生成与修改
- AI 视频生成与修改
- AI 换脸
- 钓鱼网站生成
- 钓鱼小助手

**数据来源**: `mockData.aiToolsList`

### 5. IM工具
**路径**: `/im-tools`

**功能**:
- 微信可视化
- 企业微信管理
- 钉钉 Key 利用
- 飞书利用

**状态**: 占位页面

### 6. 基础设施管理
**路径**: `/infrastructure`

**功能**:
- 养号系统
- WebUI 操作手机
- 邮箱生成中心

**状态**: 占位页面

### 7. 能力复用
**路径**: `/capabilities`

**功能**:
- 官网信息收集
- 微信公众号
- 天眼查
- 小红书
- 脉脉
- 抖音
- QQ

**数据来源**: `mockData.capabilities`

### 8. 文档中心
**路径**: `/docs`

**功能**:
- 案例库
- 历史文件管理

**数据来源**: `mockData.caseLibrary`, `mockData.historyFiles`

### 9. Agent管理中心
**路径**: `/agents`

**功能**:
- Prompt 管理
- MCP 管理中心

**数据来源**: `mockData.agentList`, `mockData.mcpServices`, `mockData.promptTemplates`

## 路由系统

### 路由守卫
使用 `ProtectedRoute` 组件保护需要登录的路由：

```typescript
// 检查 localStorage 中的 token
const token = localStorage.getItem('token')
if (!token) {
  return <Navigate to="/login" replace />
}
```

### 路由配置
```
/login              - 登录页面（公开）
/                   - 主布局（需要登录）
  /dashboard        - 仪表盘
  /phishing         - 钓鱼中台
  /projects         - 项目管理
  /ai-tools         - AI工具
  /im-tools         - IM工具
  /infrastructure   - 基础设施
  /capabilities     - 能力复用
  /docs             - 文档中心
  /agents           - Agent管理
  /settings         - 系统设置
```

## 数据管理

### Mock 数据中心
所有 Mock 数据统一存放在 `src/utils/mockData.ts`：

```typescript
export const dashboardStats = { ... }
export const projectList = [ ... ]
export const socialAccounts = [ ... ]
export const aiToolsList = [ ... ]
// ... 更多数据
```

### 数据替换策略
后续接入真实 API 时，只需：
1. 创建 API 请求函数
2. 替换组件中的 Mock 数据导入
3. 添加 loading 和 error 处理

## 主题系统

### CSS Variables
在 `src/styles/theme.css` 中定义：

```css
:root {
  /* 背景色 */
  --primary-bg: #0a0a0a;
  --secondary-bg: #141414;
  --tertiary-bg: #1f1f1f;
  --card-bg: rgba(255, 255, 255, 0.03);
  
  /* 边框色 */
  --border-color: rgba(255, 255, 255, 0.1);
  --border-hover: rgba(255, 255, 255, 0.2);
  
  /* 文字色 */
  --text-primary: rgba(255, 255, 255, 0.9);
  --text-secondary: rgba(255, 255, 255, 0.65);
  
  /* 其他 */
  --accent-color: rgba(255, 255, 255, 0.15);
  --radius-md: 12px;
  --spacing-lg: 24px;
}
```

### Ant Design 主题
使用 `ConfigProvider` 配置暗色主题：

```typescript
<ConfigProvider
  theme={{
    algorithm: theme.darkAlgorithm,
    token: {
      colorBgContainer: 'rgba(255, 255, 255, 0.03)',
      colorBorder: 'rgba(255, 255, 255, 0.1)',
      // ...
    },
  }}
>
```

## 设计规范

### 响应式断点
- **xs**: < 576px (手机)
- **sm**: ≥ 576px (平板竖屏)
- **md**: ≥ 768px (平板横屏)
- **lg**: ≥ 992px (桌面)
- **xl**: ≥ 1200px (大屏)

### 动画效果
1. **卡片悬停**: `hover-lift` 类
   - `transform: translateY(-4px)`
   - `box-shadow` 增强

2. **消息动画**: `messageSlideIn`
   - 从下方滑入
   - 透明度渐变

3. **按钮交互**:
   - 悬停: 上移 2px
   - 点击: 下移 1px

### 视觉效果
1. **毛玻璃**: `backdrop-filter: blur(20px)`
2. **圆角**: 统一使用 `var(--radius-md)`
3. **阴影**: 三级阴影系统
4. **渐变**: 用于强调元素

## 性能优化

### 代码分割
- 使用 React.lazy 懒加载页面组件
- 路由级别的代码分割

### 图片优化
- 使用 WebP 格式
- 懒加载图片

### 打包优化
- Vite 自动进行 Tree Shaking
- 生产环境自动压缩

## 开发规范

### 命名规范
- **组件**: PascalCase (如 `Dashboard.tsx`)
- **文件夹**: PascalCase (如 `ProjectManagement/`)
- **CSS类**: kebab-case (如 `.project-item`)
- **变量**: camelCase (如 `projectList`)

### 组件规范
```typescript
// 1. 导入
import { useState } from 'react'
import { Card, Button } from 'antd'
import './Component.css'

// 2. 类型定义
interface Props {
  title: string
}

// 3. 组件定义
export default function Component({ title }: Props) {
  // 4. Hooks
  const [state, setState] = useState()
  
  // 5. 事件处理
  const handleClick = () => {}
  
  // 6. 渲染
  return <div>...</div>
}
```

### CSS 规范
```css
/* 1. 容器 */
.component-container {
  padding: 24px;
}

/* 2. 元素 */
.component-item {
  /* 布局 */
  display: flex;
  
  /* 尺寸 */
  width: 100%;
  
  /* 样式 */
  background: var(--card-bg);
  border-radius: var(--radius-md);
  
  /* 动画 */
  transition: all 0.3s ease;
}

/* 3. 状态 */
.component-item:hover {
  transform: translateY(-2px);
}

/* 4. 响应式 */
@media (max-width: 768px) {
  .component-container {
    padding: 16px;
  }
}
```

## 后续开发计划

### 短期 (1-2周)
- [ ] 完善 IM 工具页面
- [ ] 实现基础设施管理
- [ ] 开发能力复用模块

### 中期 (1个月)
- [ ] 接入真实 API
- [ ] 实现文档中心
- [ ] 完善 Agent 管理

### 长期 (3个月)
- [ ] 性能优化
- [ ] 国际化支持
- [ ] 移动端 App

## 常见问题

### Q: 如何添加新页面？
1. 在 `src/pages/` 创建页面文件夹
2. 创建 `.tsx` 和 `.css` 文件
3. 在 `App.tsx` 添加路由
4. 在 `MainLayout.tsx` 添加菜单项

### Q: 如何修改主题颜色？
修改 `src/styles/theme.css` 中的 CSS Variables

### Q: Mock 数据在哪里？
统一在 `src/utils/mockData.ts` 管理

### Q: 如何接入真实 API？
1. 创建 `src/api/` 目录
2. 使用 axios 封装请求
3. 替换组件中的 Mock 数据

## 联系方式

如有问题，请联系开发团队。
