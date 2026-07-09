import React, { useState } from 'react'
import { Card, Tag, Tabs, Collapse, Steps, Space, Progress, Tooltip, Typography, Badge } from 'antd'
import {
  MessageOutlined,
  MailOutlined,
  PhoneOutlined,
  MobileOutlined,
  DesktopOutlined,
  UserOutlined,
  SafetyOutlined,
  FileZipOutlined,
  QuestionCircleOutlined,
  BulbOutlined,
  AimOutlined,
  TeamOutlined,
} from '@ant-design/icons'
import type { FindingCopywriting, Script, ScriptChannel, DialogueLine, EmailTemplate, Scenario, Objection, Payload } from '../../services/taskService'
import './CopywritingRenderer.css'

const { Text, Paragraph } = Typography

// 心理策略标签颜色
const TACTIC_COLORS: Record<string, string> = {
  '互惠原则': '#3B82F6',
  '权威效应': '#8B5CF6',
  '紧迫感': '#EF4444',
  '社会认同': '#10B981',
  '虚荣心': '#F59E0B',
  '合理化': '#6B7280',
  '稀缺性': '#EC4899',
  '一致性': '#06B6D4',
}

const CHANNEL_ICONS: Record<string, React.ReactNode> = {
  wechat: <MessageOutlined />,
  email: <MailOutlined />,
  phone: <PhoneOutlined />,
  sms: <MobileOutlined />,
  intranet: <DesktopOutlined />,
}

const CHANNEL_LABELS: Record<string, string> = {
  wechat: '微信',
  email: '邮件',
  phone: '电话',
  sms: '短信',
  intranet: '内网通知',
}

// ============================================
// 微信聊天气泡
// ============================================
function WechatRenderer({ script }: { script: Script }) {
  return (
    <div className="chat-container wechat-chat">
      <div className="chat-header">
        <MessageOutlined /> 微信对话
      </div>
      <div className="chat-body">
        {script.dialogue.map((line, i) => (
          <ChatBubble key={i} line={line} variant="wechat" />
        ))}
      </div>
      {script.key_points.length > 0 && <KeyPoints points={script.key_points} />}
    </div>
  )
}

// ============================================
// 邮件卡片
// ============================================
function EmailRenderer({ script }: { script: Script }) {
  let from = '', to = '', subject = '', body = '', signature = ''

  if (script.email_template && typeof script.email_template === 'object') {
    // 新格式：对象 { from, subject, body, signature }
    const tpl = script.email_template as EmailTemplate
    from = tpl.from || ''
    subject = tpl.subject || ''
    body = tpl.body || ''
    signature = tpl.signature || ''
  } else if (typeof script.email_template === 'string' && script.email_template) {
    // 旧格式：纯文本解析
    const lines = script.email_template.split('\n')
    const bodyLines: string[] = []
    let inBody = false
    for (const line of lines) {
      if (line.startsWith('收件人：')) to = line.replace('收件人：', '').trim()
      else if (line.startsWith('发件人：')) from = line.replace('发件人：', '').trim()
      else if (line.startsWith('主题：')) subject = line.replace('主题：', '').trim()
      else { if (from && subject) inBody = true; if (inBody) bodyLines.push(line) }
    }
    body = bodyLines.join('\n').trim()
  }

  return (
    <div className="email-container">
      <div className="email-header"><MailOutlined /> 邮件</div>
      <div className="email-card">
        <div className="email-meta">
          {from && <div className="email-meta-row"><span className="email-meta-label">发件人</span><span className="email-meta-value">{from}</span></div>}
          {to && <div className="email-meta-row"><span className="email-meta-label">收件人</span><span className="email-meta-value">{to}</span></div>}
          {subject && <div className="email-meta-row"><span className="email-meta-label">主题</span><span className="email-meta-value email-subject">{subject}</span></div>}
        </div>
        <div className="email-body">
          {body.split('\n').map((line, i) => (<React.Fragment key={i}>{line}<br /></React.Fragment>))}
          {signature && (<div style={{ marginTop: 16, paddingTop: 12, borderTop: '1px solid var(--border-color)', color: 'var(--text-tertiary)', fontSize: 12 }}>{signature.split('\n').map((l, i) => (<React.Fragment key={i}>{l}<br /></React.Fragment>))}</div>)}
        </div>
      </div>
      {script.key_points.length > 0 && <KeyPoints points={script.key_points} />}
    </div>
  )
}

// ============================================
// 电话对话
// ============================================
function PhoneRenderer({ script }: { script: Script }) {
  return (
    <div className="chat-container phone-chat">
      <div className="chat-header">
        <PhoneOutlined /> 电话对话
      </div>
      <div className="chat-body">
        {script.dialogue.map((line, i) => (
          <ChatBubble key={i} line={line} variant="phone" />
        ))}
      </div>
      {script.key_points.length > 0 && <KeyPoints points={script.key_points} />}
    </div>
  )
}

// ============================================
// 短信气泡
// ============================================
function SmsRenderer({ script }: { script: Script }) {
  return (
    <div className="chat-container sms-chat">
      <div className="chat-header">
        <MobileOutlined /> 短信
      </div>
      <div className="chat-body">
        {script.dialogue.map((line, i) => (
          <ChatBubble key={i} line={line} variant="sms" />
        ))}
      </div>
      {script.key_points.length > 0 && <KeyPoints points={script.key_points} />}
    </div>
  )
}

// ============================================
// 内网通知卡片
// ============================================
function IntranetRenderer({ script }: { script: Script }) {
  return (
    <div className="intranet-container">
      <div className="intranet-header">
        <DesktopOutlined /> 企业内部通知
      </div>
      {script.dialogue.map((line, i) => (
        <div key={i} className={`intranet-message ${line.role}`}>
          {line.role === 'attacker' ? (
            <div className="intranet-notice">
              <div className="intranet-notice-badge">
                <SafetyOutlined /> 信息技术部
              </div>
              <div className="intranet-notice-body">
                {line.content.split('\n').map((l, j) => (
                  <React.Fragment key={j}>
                    {l}
                    <br />
                  </React.Fragment>
                ))}
              </div>
              {line.tactic && <TacticTag tactic={line.tactic} />}
            </div>
          ) : (
            <div className="intranet-reply">
              <UserOutlined /> <Text type="secondary">{line.content}</Text>
            </div>
          )}
        </div>
      ))}
      {script.key_points.length > 0 && <KeyPoints points={script.key_points} />}
    </div>
  )
}

// ============================================
// 共用子组件
// ============================================
function TacticTag({ tactic }: { tactic: string }) {
  const color = TACTIC_COLORS[tactic] || '#6B7280'
  return (
    <Tooltip title={`心理策略：${tactic}`}>
      <span className="tactic-tag" style={{ backgroundColor: `${color}20`, color, borderColor: `${color}40` }}>
        <BulbOutlined /> {tactic}
      </span>
    </Tooltip>
  )
}

function ChatBubble({ line, variant }: { line: DialogueLine; variant: 'wechat' | 'phone' | 'sms' }) {
  const isAttacker = line.role === 'attacker'
  const avatarIcon = variant === 'phone'
    ? (isAttacker ? '📞' : '📱')
    : (isAttacker ? '🎭' : '👤')

  // 完全用内联 style 控制气泡样式，避免被任何全局 CSS 覆盖
  const bubbleColors: Record<string, Record<string, { bg: string; fg: string }>> = {
    wechat: { attacker: { bg: '#95ec69', fg: '#1a1a1a' }, target: { bg: '#ffffff', fg: '#1a1a1a' } },
    phone: { attacker: { bg: '#3B82F6', fg: '#ffffff' }, target: { bg: '#f0f2f5', fg: '#1a1a1a' } },
    sms: { attacker: { bg: '#8B5CF6', fg: '#ffffff' }, target: { bg: '#f0f2f5', fg: '#1a1a1a' } },
  }
  const colors = bubbleColors[variant]?.[isAttacker ? 'attacker' : 'target'] || { bg: '#f0f2f5', fg: '#1a1a1a' }

  const bubbleStyle: React.CSSProperties = {
    padding: '10px 14px',
    borderRadius: '12px',
    fontSize: '13px',
    lineHeight: '1.6',
    wordBreak: 'break-word',
    backgroundColor: colors.bg,
    color: colors.fg,
    borderTopRightRadius: isAttacker ? '4px' : '12px',
    borderTopLeftRadius: isAttacker ? '12px' : '4px',
    boxShadow: !isAttacker && variant === 'wechat' ? '0 1px 2px rgba(0,0,0,0.06)' : undefined,
  }

  return (
    <div className={`chat-bubble-row ${isAttacker ? 'attacker' : 'target'}`}>
      <div className="chat-avatar">{avatarIcon}</div>
      <div className="chat-bubble-wrapper">
        <div style={bubbleStyle}>
          {line.content}
        </div>
        {line.tactic && <TacticTag tactic={line.tactic} />}
      </div>
    </div>
  )
}

function KeyPoints({ points }: { points: string[] }) {
  return (
    <div className="key-points">
      <div className="key-points-title"><AimOutlined /> 关键要点</div>
      <ul>
        {points.map((p, i) => <li key={i}>{p}</li>)}
      </ul>
    </div>
  )
}

// ============================================
// 场景卡片
// ============================================
function ScenarioCard({ scenario }: { scenario: Scenario }) {
  return (
    <Card className="scenario-card" size="small">
      <div className="scenario-header">
        <TeamOutlined /> <Text strong>{scenario.scenario_name}</Text>
      </div>
      <Paragraph type="secondary" className="scenario-overview">{scenario.scenario_overview}</Paragraph>
      
      <div className="faked-identity">
        <div className="identity-title">伪造身份</div>
        <div className="identity-grid">
          <div className="identity-item"><span className="identity-label">姓名</span><span>{scenario.faked_identity.name}</span></div>
          <div className="identity-item"><span className="identity-label">公司</span><span>{scenario.faked_identity.company}</span></div>
          <div className="identity-item"><span className="identity-label">职位</span><span>{scenario.faked_identity.position}</span></div>
          <div className="identity-item"><span className="identity-label">背景</span><span>{scenario.faked_identity.background}</span></div>
          <div className="identity-item"><span className="identity-label">性格</span><span>{scenario.faked_identity.personality}</span></div>
        </div>
      </div>

      <div className="logic-chain">
        <div className="identity-title">逻辑链条</div>
        <Steps
          direction="vertical"
          size="small"
          current={scenario.logic_chain.length}
          items={scenario.logic_chain.map(step => ({
            title: (
              <Space>
                <Tag color="blue">{CHANNEL_LABELS[step.channel] || step.channel}</Tag>
                {step.action}
              </Space>
            ),
            description: step.fallback ? <Text type="secondary">备选：{step.fallback}</Text> : undefined,
          }))}
        />
      </div>

      {scenario.risk_notes && (
        <div className="risk-notes">
          ⚠️ <Text type="warning">{scenario.risk_notes}</Text>
        </div>
      )}
    </Card>
  )
}

// ============================================
// 样本文件卡片
// ============================================
function PayloadCard({ payload }: { payload: Payload }) {
  return (
    <Card className="payload-card" size="small">
      <div className="payload-header"><FileZipOutlined /> 样本文件</div>
      <div className="payload-grid">
        <div className="payload-item"><span className="payload-label">压缩包名</span><span>{payload.archive_name}</span></div>
        <div className="payload-item"><span className="payload-label">文件名</span><span>{payload.exe_name}</span></div>
        <div className="payload-item"><span className="payload-label">图标伪装</span><span>{payload.icon_disguise}</span></div>
        <div className="payload-item"><span className="payload-label">压缩方式</span><Tag>{payload.compression_method}</Tag></div>
        <div className="payload-item"><span className="payload-label">密码</span><code>{payload.password}</code></div>
      </div>
      {payload.notes && <Paragraph type="secondary" style={{ marginTop: 8, marginBottom: 0 }}>{payload.notes}</Paragraph>}
    </Card>
  )
}

// ============================================
// 质疑应对
// ============================================
function ObjectionsPanel({ objections }: { objections: Objection[] }) {
  return (
    <div className="objections-panel">
      <div className="objections-title"><QuestionCircleOutlined /> 质疑应对</div>
      <Collapse
        ghost
        items={objections.map((obj, i) => ({
          key: i,
          label: (
            <Space>
              <Badge status="warning" />
              <Text>"{obj.objection}"</Text>
            </Space>
          ),
          children: (
            <div className="objection-content">
              <div className="objection-response">
                <Text>{obj.response}</Text>
              </div>
              <div className="objection-meta">
                <TacticTag tactic={obj.tactic} />
                <Text type="secondary" className="objection-context">{obj.context_note}</Text>
              </div>
            </div>
          ),
        }))}
      />
    </div>
  )
}

// ============================================
// 话术渲染器（按 channel 分 Tab）
// ============================================
function ScriptRenderer({ script }: { script: Script }) {
  switch (script.channel) {
    case 'wechat': return <WechatRenderer script={script} />
    case 'email': return <EmailRenderer script={script} />
    case 'phone': return <PhoneRenderer script={script} />
    case 'sms': return <SmsRenderer script={script} />
    case 'intranet': return <IntranetRenderer script={script} />
    default: return <PhoneRenderer script={script} />
  }
}

// ============================================
// 主组件：完整话术渲染
// ============================================
interface CopywritingRendererProps {
  data: FindingCopywriting
}

export default function CopywritingRenderer({ data }: CopywritingRendererProps) {
  const [activeScriptTab, setActiveScriptTab] = useState(data.scripts?.[0]?.channel || '')

  return (
    <div className="copywriting-renderer">
      {/* 目标分析 */}
      <div className="analysis-section">
        <div className="analysis-item">
          <span className="analysis-label">目标分析</span>
          <Paragraph>{data.target_analysis}</Paragraph>
        </div>
        <div className="analysis-item">
          <span className="analysis-label">心理策略</span>
          <Paragraph>{data.psychology_strategy}</Paragraph>
        </div>
        <div className="analysis-item">
          <span className="analysis-label">案例参考</span>
          <Paragraph type="secondary">{data.case_reference}</Paragraph>
        </div>
        <div className="analysis-skills">
          {(data.loaded_skills ?? []).map(s => <Tag key={s} color="blue">{s}</Tag>)}
        </div>
      </div>

      {/* 场景卡片 */}
      {data.scenario && <ScenarioCard scenario={data.scenario} />}

      {/* 话术 Tab */}
      <div className="scripts-section">
        <Tabs
          activeKey={activeScriptTab}
          onChange={(key) => setActiveScriptTab(key as ScriptChannel)}
          items={(data.scripts ?? []).map(script => ({
            key: script.channel,
            label: (
              <Space>
                {CHANNEL_ICONS[script.channel]}
                {CHANNEL_LABELS[script.channel] || script.channel}
              </Space>
            ),
            children: <ScriptRenderer script={script} />,
          }))}
        />
      </div>

      {/* 样本文件 */}
      {data.payload && <PayloadCard payload={data.payload} />}

      {/* 质疑应对 */}
      {(data.objections ?? []).length > 0 && <ObjectionsPanel objections={data.objections} />}
    </div>
  )
}

// ============================================
// Finding 类型图标
// ============================================
export const FINDING_TYPE_ICONS: Record<string, string> = {
  hr_contact: '👤',
  business_contact: '💼',
  customer_service: '🎧',
  tech_support: '🔧',
  social_media: '📱',
  download: '⬇️',
  form: '📝',
  other: '📌',
}

export const FINDING_TYPE_LABELS: Record<string, string> = {
  hr_contact: 'HR/招聘',
  business_contact: '商务联系',
  customer_service: '客服入口',
  tech_support: '技术支持',
  social_media: '社交媒体',
  download: '下载入口',
  form: '表单入口',
  other: '其他',
}

export const CHANNEL_TYPE_LABELS: Record<string, string> = {
  email: '邮箱',
  phone: '电话',
  wechat: '微信',
  qq: 'QQ',
  form: '表单',
  app: 'APP',
  other: '其他',
}

export function AttentionScoreBar({ score }: { score: number }) {
  const color = score >= 80 ? '#ff4d4f' : score >= 60 ? '#faad14' : score >= 40 ? '#1890ff' : '#52c41a'
  return (
    <Tooltip title={`关注度: ${score}`}>
      <Progress percent={score} size="small" strokeColor={color} format={p => p} />
    </Tooltip>
  )
}
