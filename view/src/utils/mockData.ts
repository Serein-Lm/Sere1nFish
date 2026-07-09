// 统一的 Mock 数据管理中心

// 仪表盘统计数据
export const dashboardStats = {
  projects: 12,
  socialAccounts: 45,
  aiTools: 8,
  caseLibrary: 156,
  dataVolume: '2.3TB',
  infoSources: 23,
  projectProgress: 68,
  agentTotal: 15,
  mcpCount: 7,
  toolCount: 32,
}

// 项目列表
export const projectList = [
  {
    id: '1',
    name: '某科技公司渗透测试',
    target: '某科技有限公司',
    status: '进行中',
    progress: 75,
    startDate: '2024-01-15',
    members: ['张三', '李四'],
    tags: ['高优先级', '技术类'],
  },
  {
    id: '2',
    name: '金融机构安全评估',
    target: '某银行',
    status: '计划中',
    progress: 20,
    startDate: '2024-02-01',
    members: ['王五'],
    tags: ['金融', '合规'],
  },
  {
    id: '3',
    name: '教育行业钓鱼演练',
    target: '某大学',
    status: '已完成',
    progress: 100,
    startDate: '2023-12-01',
    members: ['赵六', '钱七'],
    tags: ['教育', '培训'],
  },
]

// 社交媒体账号
export const socialAccounts = [
  { platform: '微信公众号', count: 12, status: 'active' },
  { platform: '小红书', count: 8, status: 'active' },
  { platform: '抖音', count: 15, status: 'active' },
  { platform: '微博', count: 10, status: 'active' },
]

// AI工具列表
export const aiToolsList = [
  { name: 'AI-TTS', type: '语音合成', status: '可用', usage: 234 },
  { name: 'AI图片生成', type: '图像生成', status: '可用', usage: 156 },
  { name: 'AI视频生成', type: '视频生成', status: '可用', usage: 89 },
  { name: 'AI换脸', type: '图像处理', status: '可用', usage: 67 },
  { name: '钓鱼网站生成', type: '网站克隆', status: '可用', usage: 123 },
  { name: '水坑伪造', type: '网站伪造', status: '可用', usage: 45 },
]

// 案例库
export const caseLibrary = [
  {
    id: '1',
    title: '某科技公司邮件钓鱼案例',
    category: '邮件钓鱼',
    date: '2024-01-10',
    successRate: 72,
    tags: ['邮件', '技术'],
  },
  {
    id: '2',
    title: '金融行业社交工程案例',
    category: '社交工程',
    date: '2024-01-05',
    successRate: 65,
    tags: ['金融', '社交'],
  },
]

// 信息收集来源
export const infoSources = [
  { name: '官网', count: 45, lastUpdate: '2024-01-20' },
  { name: '微信公众号', count: 32, lastUpdate: '2024-01-19' },
  { name: '小红书', count: 28, lastUpdate: '2024-01-18' },
  { name: '天眼查', count: 15, lastUpdate: '2024-01-17' },
  { name: '脉脉', count: 12, lastUpdate: '2024-01-16' },
]

// Agent列表
export const agentList = [
  { id: '1', name: '信息收集Agent', type: 'collector', status: 'running', tasks: 15 },
  { id: '2', name: '内容生成Agent', type: 'generator', status: 'running', tasks: 8 },
  { id: '3', name: '钓鱼助手Agent', type: 'assistant', status: 'idle', tasks: 0 },
]

// MCP服务列表
export const mcpServices = [
  { id: '1', name: 'Web搜索MCP', status: 'active', calls: 1234 },
  { id: '2', name: '数据库MCP', status: 'active', calls: 567 },
  { id: '3', name: '文件处理MCP', status: 'active', calls: 890 },
]

// 目标信息
export const targetInfo = {
  basic: {
    name: '某科技有限公司',
    website: 'https://example.com',
    industry: '互联网',
    scale: '500-1000人',
  },
  social: {
    wechat: '已找到3个公众号',
    xiaohongshu: '已找到5个账号',
    weibo: '已找到2个账号',
  },
  leaked: [
    { type: '邮箱', content: 'admin@example.com', source: '数据泄露', date: '2023-12-01' },
    { type: '密码', content: '******', source: '暗网', date: '2023-11-15' },
  ],
}

// IM工具配置
export const imConfigs = [
  { type: 'wechat', name: '微信', count: 5, status: 'configured' },
  { type: 'wecom', name: '企业微信', count: 3, status: 'configured' },
  { type: 'dingtalk', name: '钉钉', count: 2, status: 'configured' },
  { type: 'feishu', name: '飞书', count: 1, status: 'configured' },
]

// 语音模板
export const voiceTemplates = [
  { id: '1', name: '男声-专业', language: 'zh-CN', gender: 'male', style: 'professional' },
  { id: '2', name: '女声-亲切', language: 'zh-CN', gender: 'female', style: 'friendly' },
  { id: '3', name: '男声-严肃', language: 'zh-CN', gender: 'male', style: 'serious' },
  { id: '4', name: '女声-活泼', language: 'zh-CN', gender: 'female', style: 'cheerful' },
]

// 能力列表
export const capabilities = [
  {
    id: 'official-website',
    name: '官网信息收集',
    description: '收集目标官网的商务联系、在线客服等信息',
    category: 'info-collection',
  },
  {
    id: 'wechat-official',
    name: '微信公众号',
    description: '获取招投标信息、群聊等',
    category: 'info-collection',
  },
  {
    id: 'tianyancha',
    name: '天眼查',
    description: '获取招投标文件',
    category: 'info-collection',
  },
  {
    id: 'xiaohongshu',
    name: '小红书',
    description: '查找内部员工、实习信息',
    category: 'info-collection',
  },
  {
    id: 'maimai',
    name: '脉脉',
    description: '查找内部员工信息',
    category: 'info-collection',
  },
  {
    id: 'douyin',
    name: '抖音',
    description: '获取投稿邮箱、内部员工信息',
    category: 'info-collection',
  },
]

// Prompt模板
export const promptTemplates = [
  {
    id: '1',
    name: '信息收集Prompt',
    content: '请帮我收集目标公司的基础信息...',
    category: 'collection',
    usage: 45,
  },
  {
    id: '2',
    name: '钓鱼邮件生成Prompt',
    content: '请生成一封针对技术人员的钓鱼邮件...',
    category: 'phishing',
    usage: 32,
  },
]

// 历史文件
export const historyFiles = [
  {
    id: '1',
    name: '某科技公司渗透测试报告.pdf',
    type: 'report',
    size: '2.3MB',
    date: '2024-01-15',
  },
  {
    id: '2',
    name: '钓鱼邮件模板集合.docx',
    type: 'template',
    size: '1.5MB',
    date: '2024-01-10',
  },
]
