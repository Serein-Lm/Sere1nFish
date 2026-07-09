import { useState } from 'react'
import {
  Tabs,
  Card,
  Button,
  Table,
  Tag,
  Input,
  Space,
  Typography,
  Form,
  Select,
  Switch,
  Row,
  Col,
  Statistic,
  Alert,
  List,
} from 'antd'
import type { ColumnsType } from 'antd/es/table'
import {
  FileTextOutlined,
  ThunderboltOutlined,
  CloudServerOutlined,
  MailOutlined,
  SafetyOutlined,
  PlusOutlined,
  PlayCircleOutlined,
  EyeOutlined,
  EditOutlined,
  SendOutlined,
  SecurityScanOutlined,
  AimOutlined,
  RobotOutlined,
  CodeOutlined,
  CheckCircleOutlined,
  ClockCircleOutlined,
} from '@ant-design/icons'
import './EmailPhishing.css'

const { Title, Text } = Typography
const { TextArea } = Input

interface CampaignItem {
  id: string; name: string; type: string; status: 'running' | 'paused' | 'completed' | 'draft'
  targets: number; sent: number; opened: number; clicked: number; created: string
}

const MOCK_CAMPAIGNS: CampaignItem[] = [
  { id: '1', name: '系统升级通知', type: '邮件钓鱼', status: 'running', targets: 50, sent: 48, opened: 32, clicked: 8, created: '2026-05-29' },
  { id: '2', name: 'IT 安全培训', type: '定向钓鱼', status: 'completed', targets: 30, sent: 30, opened: 25, clicked: 15, created: '2026-05-25' },
  { id: '3', name: 'HR 年审通知', type: '批量钓鱼', status: 'draft', targets: 100, sent: 0, opened: 0, clicked: 0, created: '2026-05-31' },
]

export default function EmailPhishing() {
  const [activeTab, setActiveTab] = useState('copywriting')

  const renderCopywriting = () => (
    <div className="tab-content">
      <Card className="glass-card" title={<Space><RobotOutlined /> AI 文案生成</Space>}>
        <Form layout="vertical" style={{ maxWidth: 700 }}>
          <Form.Item label="场景类型">
            <Select placeholder="选择文案场景" options={[
              { value: 'upgrade', label: '系统升级通知' },
              { value: 'security', label: '安全告警' },
              { value: 'hr', label: 'HR 通知' },
              { value: 'finance', label: '财务审批' },
              { value: 'meeting', label: '会议邀请' },
              { value: 'custom', label: '自定义' },
            ]} />
          </Form.Item>
          <Form.Item label="目标人群">
            <Select mode="multiple" placeholder="选择目标人群" options={[
              { value: 'tech', label: '技术人员' },
              { value: 'finance', label: '财务人员' },
              { value: 'hr', label: 'HR' },
              { value: 'management', label: '管理层' },
              { value: 'all', label: '全员' },
            ]} />
          </Form.Item>
          <Form.Item label="伪装身份">
            <Input placeholder="例如：IT部门、系统管理员、人力资源部" />
          </Form.Item>
          <Form.Item label="紧迫性">
            <Select defaultValue="medium" options={[
              { value: 'low', label: '低 - 常规通知' },
              { value: 'medium', label: '中 - 需尽快处理' },
              { value: 'high', label: '高 - 紧急/有截止日期' },
              { value: 'critical', label: '极高 - 安全事件/处罚' },
            ]} />
          </Form.Item>
          <Form.Item label="附加要求">
            <TextArea rows={3} placeholder="其他定制要求..." />
          </Form.Item>
          <Form.Item>
            <Space>
              <Button type="primary" icon={<RobotOutlined />}>AI 生成文案</Button>
              <Button icon={<FileTextOutlined />}>使用模板库</Button>
            </Space>
          </Form.Item>
        </Form>
      </Card>
    </div>
  )

  const renderAutomation = () => (
    <div className="tab-content">
      <Card className="glass-card" style={{ marginBottom: 16 }}>
        <Row gutter={24}>
          <Col span={6}><Statistic title="运行中" value={1} prefix={<PlayCircleOutlined />} valueStyle={{ color: '#1890ff' }} /></Col>
          <Col span={6}><Statistic title="已完成" value={1} prefix={<CheckCircleOutlined />} valueStyle={{ color: '#52c41a' }} /></Col>
          <Col span={6}><Statistic title="总发送" value={78} prefix={<SendOutlined />} /></Col>
          <Col span={6}><Statistic title="平均点击率" value={14.7} suffix="%" prefix={<AimOutlined />} valueStyle={{ color: '#faad14' }} /></Col>
        </Row>
      </Card>
      <Card className="glass-card" title={<Space><ThunderboltOutlined /> 自动化钓鱼任务</Space>}
        extra={<Button type="primary" icon={<PlusOutlined />}>新建任务</Button>}
      >
        <Table columns={[
          { title: '任务名称', dataIndex: 'name', key: 'name', render: (t: string) => <Text strong>{t}</Text> },
          { title: '类型', dataIndex: 'type', key: 'type', render: (t: string) => <Tag>{t}</Tag> },
          { title: '状态', dataIndex: 'status', key: 'status', render: (s: string) => {
            const m: Record<string, { color: string; icon: React.ReactNode; text: string }> = {
              running: { color: 'processing', icon: <PlayCircleOutlined />, text: '运行中' },
              paused: { color: 'warning', icon: <ClockCircleOutlined />, text: '已暂停' },
              completed: { color: 'success', icon: <CheckCircleOutlined />, text: '已完成' },
              draft: { color: 'default', icon: <EditOutlined />, text: '草稿' },
            }
            const c = m[s] || m.draft
            return <Tag icon={c.icon} color={c.color}>{c.text}</Tag>
          }},
          { title: '目标数', dataIndex: 'targets', key: 'targets' },
          { title: '已发送', dataIndex: 'sent', key: 'sent' },
          { title: '打开率', key: 'openRate', render: (_: any, r: CampaignItem) => r.sent > 0 ? `${Math.round(r.opened / r.sent * 100)}%` : '-' },
          { title: '点击率', key: 'clickRate', render: (_: any, r: CampaignItem) => r.sent > 0 ? <Text type={r.clicked > 0 ? 'success' : 'secondary'}>{Math.round(r.clicked / r.sent * 100)}%</Text> : '-' },
          { title: '操作', key: 'action', render: (_: any, r: CampaignItem) => (
            <Space size="small">
              <Button type="text" size="small" icon={<EyeOutlined />} />
              {r.status === 'draft' && <Button type="text" size="small" icon={<SendOutlined />} />}
            </Space>
          )},
        ] as ColumnsType<CampaignItem>} dataSource={MOCK_CAMPAIGNS} rowKey="id" pagination={false} />
      </Card>
    </div>
  )

  const renderEmailSpoof = () => (
    <div className="tab-content">
      <Card className="glass-card" title={<Space><MailOutlined /> 高仿邮箱配置</Space>}>
        <Alert message="配置高度相似的发件人邮箱，提高邮件可信度和投递率" type="info" showIcon style={{ marginBottom: 16 }} />
        <Form layout="vertical" style={{ maxWidth: 600 }}>
          <Form.Item label="发件人显示名"><Input placeholder="例如：IT Support Team" /></Form.Item>
          <Form.Item label="发件邮箱"><Input placeholder="it-support@company-notice.com" prefix={<MailOutlined />} /></Form.Item>
          <Form.Item label="SMTP 服务器"><Input placeholder="smtp.your-server.com" /></Form.Item>
          <Form.Item label="SMTP 端口"><Select defaultValue="587" options={[{ value: '25' }, { value: '465' }, { value: '587' }]} /></Form.Item>
          <Form.Item label="SPF 记录"><Input placeholder="v=spf1 include:your-server.com ~all" prefix={<CodeOutlined />} /></Form.Item>
          <Form.Item label="DKIM 签名">
            <Switch /><Text type="secondary" style={{ marginLeft: 8 }}>启用 DKIM 签名提高投递率</Text>
          </Form.Item>
          <Form.Item label="域名预热天数">
            <Select defaultValue="7" options={[
              { value: '3', label: '3 天' }, { value: '7', label: '7 天（推荐）' },
              { value: '14', label: '14 天' }, { value: '30', label: '30 天（企业级）' },
            ]} />
          </Form.Item>
          <Form.Item>
            <Space>
              <Button type="primary" icon={<PlayCircleOutlined />}>保存配置</Button>
              <Button icon={<SendOutlined />}>发送测试邮件</Button>
            </Space>
          </Form.Item>
        </Form>
      </Card>
    </div>
  )

  const renderBypass = () => (
    <div className="tab-content">
      <Card className="glass-card" title={<Space><SafetyOutlined /> 邮件网关绕过</Space>}>
        <List grid={{ gutter: 16, column: 2 }} dataSource={[
          { title: 'SPF / DKIM / DMARC', desc: '正确配置发信域名的 DNS 记录，通过邮件服务商认证', icon: <SafetyOutlined />, status: 'ready' },
          { title: '关键词混淆', desc: '使用 Unicode 同形字、零宽字符、HTML 实体编码绕过关键词过滤', icon: <CodeOutlined />, status: 'ready' },
          { title: '发信域名预热', desc: '新域名先发少量正常邮件建立信誉，逐步提高发送量', icon: <ClockCircleOutlined />, status: 'ready' },
          { title: '附件免杀', desc: '对 Office 宏文档、PDF 等附件做混淆处理，绕过沙箱检测', icon: <SecurityScanOutlined />, status: 'beta' },
          { title: '发送频率控制', desc: '控制每分钟/每小时发送量，避免触发速率限制', icon: <ThunderboltOutlined />, status: 'ready' },
          { title: '多 SMTP 轮转', desc: '多个 SMTP 服务器轮流发送，分散风险', icon: <CloudServerOutlined />, status: 'ready' },
        ]} renderItem={(item) => (
          <List.Item>
            <Card size="small" className="glass-card hover-float">
              <Space direction="vertical" style={{ width: '100%' }}>
                <Space style={{ width: '100%', justifyContent: 'space-between' }}>
                  <Space>{item.icon}<Text strong>{item.title}</Text></Space>
                  <Tag color={item.status === 'ready' ? 'green' : 'orange'}>{item.status === 'ready' ? '可用' : 'Beta'}</Tag>
                </Space>
                <Text type="secondary" style={{ fontSize: 12 }}>{item.desc}</Text>
                <Button size="small" type="primary" ghost>配置</Button>
              </Space>
            </Card>
          </List.Item>
        )} />
      </Card>
    </div>
  )

  const tabs = [
    { key: 'copywriting', label: <Space><FileTextOutlined />文案生成</Space>, children: renderCopywriting() },
    { key: 'automation', label: <Space><ThunderboltOutlined />自动化发送</Space>, children: renderAutomation() },
    { key: 'email-spoof', label: <Space><MailOutlined />高仿邮箱</Space>, children: renderEmailSpoof() },
    { key: 'bypass', label: <Space><SafetyOutlined />网关绕过</Space>, children: renderBypass() },
  ]

  return (
    <div className="email-phishing-page page-container fade-in">
      <div className="page-header slide-up">
        <div>
          <Title level={2} className="page-title"><MailOutlined /> 邮件钓鱼</Title>
          <Text type="secondary">AI 文案生成、自动化发送、高仿邮箱配置与网关绕过</Text>
        </div>
      </div>
      <Tabs activeKey={activeTab} onChange={setActiveTab} items={tabs} size="large" className="phishing-tabs" />
    </div>
  )
}
