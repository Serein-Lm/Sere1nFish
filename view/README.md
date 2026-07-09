# Sere1nFish - AI钓鱼中台

一个现代化的AI驱动钓鱼测试与安全意识培训平台。

## 技术栈

- **前端框架**: React 19 + TypeScript
- **构建工具**: Vite
- **UI组件库**: Ant Design 5 + Ant Design X
- **路由**: React Router v7
- **HTTP客户端**: Axios
- **样式**: CSS + CSS Variables (深灰色主题)

## 功能模块

### 1. 用户权限系统 ⭐ NEW
- 支持管理员/普通用户角色
- 基于权限的菜单显示
- 用户管理（管理员专属）
- Cookie管理（多平台支持）

### 2. 仪表盘
- 项目总数、社交媒体账号、AI工具数统计
- 案例库、数据量、信息收集来源
- 项目进展、Agent总数、MCP和Tool数量
- 实时监控系统状态

### 3. 钓鱼中台
- AI对话式钓鱼内容生成
- 使用 Ant Design X 的 Bubble、Sender 组件
- 快速生成钓鱼邮件、网站克隆、社工话术
- 水坑攻击设计辅助

### 4. 项目管理
- 项目增删改查与分组管理
- 小红书笔记分析与人物画像
- 攻击面识别与关键信息提取
- SSE实时任务进度推送

### 5. AI工具
- AI-TTS 语音合成
- AI图片/视频生成与修改
- AI换脸 (DeepFake)
- 钓鱼网站快速生成
- 水坑伪造

### 6. IM工具
- 微信/企业微信可视化
- 钉钉/飞书利用
- 企业微信Key利用

### 7. 基础设施管理
- 养号系统
- WebUI操作手机
- 邮箱生成中心

### 8. 能力复用
- 官网信息收集
- 微信公众号/天眼查/小红书
- 脉脉/抖音/QQ群聊

### 9. 系统管理（管理员）
- 用户管理：创建、编辑、删除用户
- Cookie管理：多平台Cookie配置

## 快速开始

### 安装依赖

```bash
npm install
```

### 启动开发服务器

```bash
npm run dev
```

访问 http://localhost:5173

### 构建生产版本

```bash
npm run build
```

### 代码检查

```bash
npm run lint
```

## 测试账号

| 用户名 | 密码 | 角色 | 权限 |
|--------|------|------|------|
| admin | admin123 | 管理员 | 全部功能 |
| user | user123 | 普通用户 | 基础功能 |

## 项目结构

```
src/
├── components/           # 公共组件
│   ├── Layout/          # 布局组件 (MainLayout)
│   ├── Login/           # 登录页面
│   ├── ProfileDrawer/   # 人物画像弹窗
│   └── PlaceholderPage/ # 占位页面
├── pages/               # 页面模块
│   ├── Dashboard/       # 仪表盘
│   ├── PhishingPlatform/# 钓鱼中台
│   ├── ProjectManagement/# 项目管理
│   ├── ProjectDetail/   # 项目详情
│   ├── AITools/         # AI工具
│   ├── IMTools/         # IM工具
│   ├── Infrastructure/  # 基础设施
│   ├── Capabilities/    # 能力复用
│   ├── DocumentCenter/  # 文档中心
│   ├── AgentManagement/ # Agent管理
│   ├── UserManagement/  # 用户管理
│   ├── CookieManagement/# Cookie管理
│   └── Settings/        # 设置
├── services/            # API服务
│   ├── http.ts          # Axios封装
│   ├── authService.ts   # 认证服务
│   ├── projectService.ts# 项目服务
│   ├── xhsService.ts    # 小红书服务
│   └── agentService.ts  # Agent服务
├── config/              # 配置
│   └── api.ts           # API配置
├── contexts/            # React Context
│   └── ThemeContext.tsx # 主题上下文
├── styles/              # 全局样式
│   └── theme.css        # 主题变量
├── utils/               # 工具函数
│   ├── mockData.ts      # Mock数据
│   ├── colorUtils.ts    # 颜色工具
│   └── webTaggingMap.ts # 标签映射
├── App.tsx              # 应用入口
└── main.tsx             # 主文件

docs/                    # 文档
├── API.md               # API文档
├── ARCHITECTURE.md      # 架构文档
├── QUICK_START.md       # 快速开始
└── ...
```

## API服务

### 认证相关
- `POST /api/v1/auth/login` - 登录
- `GET /api/v1/auth/me` - 获取当前用户
- `POST /api/v1/auth/logout` - 登出

### 用户管理（管理员）
- `GET /api/v1/auth/users` - 用户列表
- `POST /api/v1/auth/users` - 创建用户
- `PUT /api/v1/auth/users/{username}` - 更新用户
- `DELETE /api/v1/auth/users/{username}` - 删除用户

### 项目管理
- `GET /api/v1/projects` - 项目列表
- `POST /api/v1/projects` - 创建项目
- `GET /api/v1/projects/{id}` - 项目详情

### 小红书分析
- `GET /api/v1/xhs/notes` - 笔记列表
- `GET /api/v1/xhs/notes/{id}` - 笔记详情
- `GET /api/v1/xhs/profiles` - 人物画像列表
- `GET /api/v1/xhs/profiles/{id}` - 画像详情

## 设计特性

### 主题系统
- 暗色/亮色主题切换
- CSS Variables统一管理
- 自动保存偏好设置

### 响应式设计
- 桌面端/平板/手机端适配
- 自适应布局

### 交互体验
- 卡片悬停动画
- 平滑滚动
- 毛玻璃效果
- 消息气泡动画

## 开发规范

- TypeScript 类型检查
- ESLint 代码规范
- 函数式组件 + Hooks
- 独立CSS文件
- 统一API服务层

## License

MIT
