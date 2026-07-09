import { Card, Row, Col, Button, Table, Tag, Progress, Statistic, Typography, Space } from 'antd'
import {
  CloudServerOutlined,
  MobileOutlined,
  MailOutlined,
  PlusOutlined,
  PlayCircleOutlined,
  PauseCircleOutlined,
  DashboardOutlined,
  SafetyCertificateOutlined,
} from '@ant-design/icons'
import type { ColumnsType } from 'antd/es/table'
import './Infrastructure.css'

const { Title, Paragraph, Text } = Typography

interface Account {
  id: string
  platform: string
  account: string
  status: string
  health: number
  lastActive: string
}

export default function Infrastructure() {
  const accountColumns: ColumnsType<Account> = [
    { title: '平台', dataIndex: 'platform', key: 'platform' },
    { title: '账号', dataIndex: 'account', key: 'account' },
    {
      title: '状态',
      dataIndex: 'status',
      key: 'status',
      render: (status) => (
        <Tag color={status === '正常' ? 'green' : 'orange'}>{status}</Tag>
      ),
    },
    {
      title: '健康度',
      dataIndex: 'health',
      key: 'health',
      render: (health) => <Progress percent={health} size="small" />,
    },
    { title: '最后活跃', dataIndex: 'lastActive', key: 'lastActive' },
    {
      title: '操作',
      key: 'action',
      render: () => (
        <Button.Group size="small">
          <Button icon={<PlayCircleOutlined />}>启动</Button>
          <Button icon={<PauseCircleOutlined />}>暂停</Button>
        </Button.Group>
      ),
    },
  ]

  const accountData: Account[] = [
    { id: '1', platform: '微信', account: 'wx_user_001', status: '正常', health: 95, lastActive: '2分钟前' },
    { id: '2', platform: '微博', account: 'wb_user_002', status: '正常', health: 88, lastActive: '5分钟前' },
    { id: '3', platform: '抖音', account: 'dy_user_003', status: '维护中', health: 60, lastActive: '1小时前' },
  ]

  const emailData = [
    { id: '1', email: 'test001@example.com', status: '可用', created: '2024-01-20' },
    { id: '2', email: 'test002@example.com', status: '可用', created: '2024-01-20' },
    { id: '3', email: 'test003@example.com', status: '已使用', created: '2024-01-19' },
  ]

  return (
    <div className="infrastructure page-container fade-in">
      <div className="page-header slide-up">
        <div>
          <Title level={2} className="page-title">
            <CloudServerOutlined /> 基础设施管理
          </Title>
          <Paragraph className="page-description">管理养号系统、WebUI 虚拟设备及邮箱自动化生成中心</Paragraph>
        </div>
      </div>

      <Row gutter={[24, 24]} style={{ marginBottom: '24px' }}>
        <Col xs={24} sm={8} className="slide-up stagger-1">
          <Card className="glass-card hover-float stat-mini-card">
            <Statistic
              title="养号总数"
              value={156}
              prefix={<DashboardOutlined />}
              valueStyle={{ color: 'var(--color-success)', fontWeight: 700 }}
            />
            <div className="stat-footer">
              <Text type="secondary">在线: 142</Text>
              <Tag color="green" bordered={false}>+5%</Tag>
            </div>
          </Card>
        </Col>
        <Col xs={24} sm={8} className="slide-up stagger-2">
          <Card className="glass-card hover-float stat-mini-card">
            <Statistic
              title="在线设备"
              value={12}
              prefix={<MobileOutlined />}
              valueStyle={{ color: 'var(--color-info)', fontWeight: 700 }}
            />
            <div className="stat-footer">
              <Text type="secondary">负载: 65%</Text>
              <Tag color="blue" bordered={false}>正常</Tag>
            </div>
          </Card>
        </Col>
        <Col xs={24} sm={8} className="slide-up stagger-3">
          <Card className="glass-card hover-float stat-mini-card">
            <Statistic
              title="可用邮箱"
              value={89}
              prefix={<MailOutlined />}
              valueStyle={{ color: 'var(--color-warning)', fontWeight: 700 }}
            />
            <div className="stat-footer">
              <Text type="secondary">存量充足</Text>
              <Tag color="orange" bordered={false}>New</Tag>
            </div>
          </Card>
        </Col>
      </Row>

      <Row gutter={[24, 24]}>
        <Col xs={24} lg={16} className="slide-up stagger-2">
          <Card
            title={<Space><SafetyCertificateOutlined /> 养号系统</Space>}
            className="glass-card table-card"
            extra={
              <Button type="primary" icon={<PlusOutlined />} className="hover-float">
                添加账号
              </Button>
            }
          >
            <Table
              columns={accountColumns}
              dataSource={accountData}
              rowKey="id"
              pagination={false}
              className="custom-table"
            />
          </Card>
        </Col>

        <Col xs={24} lg={8} className="slide-up stagger-3">
          <Card 
            title={<Space><MailOutlined /> 邮箱生成中心</Space>} 
            className="glass-card side-list-card" 
            style={{ marginBottom: '24px' }}
          >
            <Button type="primary" block icon={<PlusOutlined />} className="action-btn-main">
              批量生成邮箱
            </Button>
            <div className="email-list">
              {emailData.map((email) => (
                <div key={email.id} className="email-item-new">
                  <div className="email-info-main">
                    <Text className="email-address-text">{email.email}</Text>
                    <Tag color={email.status === '可用' ? 'success' : 'default'} bordered={false}>
                      {email.status}
                    </Tag>
                  </div>
                  <div className="email-meta-info">
                    <Text type="secondary">创建于 {email.created}</Text>
                  </div>
                </div>
              ))}
            </div>
          </Card>

          <Card title={<Space><MobileOutlined /> WebUI 控制台</Space>} className="glass-card side-action-card">
            <Button type="primary" block icon={<PlayCircleOutlined />} className="action-btn-secondary">
              启动控制面板
            </Button>
            <div className="feature-tips">
              <div className="tip-item"><div className="tip-dot"></div> 支持多设备并发远程控制</div>
              <div className="tip-item"><div className="tip-dot"></div> 毫秒级低延迟屏幕投影</div>
              <div className="tip-item"><div className="tip-dot"></div> 可视化工作流脚本录制</div>
            </div>
          </Card>
        </Col>
      </Row>
    </div>
  )
}
