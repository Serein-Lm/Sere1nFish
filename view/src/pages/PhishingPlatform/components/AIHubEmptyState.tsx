import {
  DatabaseOutlined,
  FileWordOutlined,
  GlobalOutlined,
  PhoneOutlined,
  SearchOutlined,
} from '@ant-design/icons'
import { Button } from 'antd'

interface AIHubEmptyStateProps {
  onPrompt: (prompt: string) => void
  onAgent: (agentKey: string) => void
}

const COMMAND_GROUPS = [
  {
    key: 'data',
    label: '数据查询',
    icon: <DatabaseOutlined />,
    commands: [
      '当前平台有哪些项目？',
      '列出关注度最高的目标 Finding',
      '检查最近任务日志中的异常',
    ],
  },
  {
    key: 'research',
    label: '情报分析',
    icon: <SearchOutlined />,
    commands: [
      '生成某个项目的综合态势摘要',
      '对比多个项目的发现数量和进展',
      '读取链接并提炼可引用的关键事实',
    ],
  },
  {
    key: 'engagement',
    label: '人设与话术',
    icon: <PhoneOutlined />,
    commands: [
      '检索目标人物的公开背景信息',
      '基于 Finding 生成完整沟通方案',
      '匹配人设并生成三条沟通话术',
    ],
  },
  {
    key: 'artifact',
    label: '文档产物',
    icon: <FileWordOutlined />,
    commands: [
      '把分析结果整理成 Word 文档',
      '同时导出 Word 和结构化 JSON',
      '把引用内容整理成可下载报告',
    ],
  },
]

export default function AIHubEmptyState({ onPrompt, onAgent }: AIHubEmptyStateProps) {
  return (
    <div className="ai-hub-empty-state">
      <header>
        <span className="ai-hub-empty-mark" aria-hidden="true">A</span>
        <div>
          <h1>新会话</h1>
          <p>AI 中枢 · 待命</p>
        </div>
      </header>
      <div className="ai-hub-command-grid">
        {COMMAND_GROUPS.map((group) => (
          <section key={group.key} className="ai-hub-command-group">
            <div className="ai-hub-command-label">
              {group.icon}
              <span>{group.label}</span>
            </div>
            {group.commands.map((command) => (
              <button type="button" key={command} onClick={() => onPrompt(command)}>
                <span>{command}</span>
                <kbd>Enter</kbd>
              </button>
            ))}
          </section>
        ))}
      </div>
      <div className="ai-hub-agent-shortcuts" aria-label="Agent 快捷入口">
        <Button size="small" icon={<GlobalOutlined />} onClick={() => onAgent('deep_search')}>深度搜索</Button>
        <Button size="small" icon={<PhoneOutlined />} onClick={() => onAgent('social_engineering')}>话术方案</Button>
        <Button size="small" icon={<FileWordOutlined />} onClick={() => onPrompt('基于我接下来引用的信息生成一份高质量 Word 文档')}>文档生成</Button>
      </div>
    </div>
  )
}
