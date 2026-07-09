import { Card, Row, Col, Table, Button, Tag, Tabs, Modal, Form, Input, Select, Typography, Space, Statistic, Divider } from 'antd'
import { useState } from 'react'
import {
  RobotOutlined,
  ApiOutlined,
  FileTextOutlined,
  PlusOutlined,
  PlayCircleOutlined,
  PauseCircleOutlined,
  EditOutlined,
  DeleteOutlined,
  ThunderboltOutlined,
  SettingOutlined,
} from '@ant-design/icons'
import type { ColumnsType } from 'antd/es/table'
import { agentList, mcpServices, promptTemplates } from '../../utils/mockData'
import './AgentManagement.css'

const { Title, Paragraph, Text } = Typography

export default function AgentManagement() {
  const [isModalOpen, setIsModalOpen] = useState(false)
  const [modalType, setModalType] = useState<'agent' | 'mcp' | 'prompt'>('agent')

  const agentColumns: ColumnsType<any> = [
    { title: 'Agent名称', dataIndex: 'name', key: 'name' },
    { title: '类型', dataIndex: 'type', key: 'type' },
    {
      title: '状态',
      dataIndex: 'status',
      key: 'status',
      render: (status) => (
        <Tag color={status === 'running' ? 'green' : 'default'}>
          {status === 'running' ? '运行中' : '空闲'}
        </Tag>
      ),
    },
    { title: '任务数', dataIndex: 'tasks', key: 'tasks' },
    {
      title: '操作',
      key: 'action',
      render: (_, record) => (
        <Button.Group size="small">
          <Button icon={record.status === 'running' ? <PauseCircleOutlined /> : <PlayCircleOutlined />}>
            {record.status === 'running' ? '暂停' : '启动'}
          </Button>
          <Button icon={<EditOutlined />}>编辑</Button>
        </Button.Group>
      ),
    },
  ]

  const mcpColumns: ColumnsType<any> = [
    { title: 'MCP名称', dataIndex: 'name', key: 'name' },
    {
      title: '状态',
      dataIndex: 'status',
      key: 'status',
      render: (status) => <Tag color={status === 'active' ? 'green' : 'red'}>{status === 'active' ? '活跃' : '停止'}</Tag>,
    },
    { title: '调用次数', dataIndex: 'calls', key: 'calls' },
    {
      title: '操作',
      key: 'action',
      render: () => (
        <Button.Group size="small">
          <Button icon={<EditOutlined />}>配置</Button>
          <Button danger icon={<DeleteOutlined />}>删除</Button>
        </Button.Group>
      ),
    },
  ]

  const promptColumns: ColumnsType<any> = [
    { title: 'Prompt名称', dataIndex: 'name', key: 'name' },
    { title: '分类', dataIndex: 'category', key: 'category' },
    { title: '使用次数', dataIndex: 'usage', key: 'usage' },
    {
      title: '操作',
      key: 'action',
      render: () => (
        <Button.Group size="small">
          <Button icon={<EditOutlined />}>编辑</Button>
          <Button danger icon={<DeleteOutlined />}>删除</Button>
        </Button.Group>
      ),
    },
  ]

  const handleAdd = (type: 'agent' | 'mcp' | 'prompt') => {
    setModalType(type)
    setIsModalOpen(true)
  }

  const tabItems = [
    {
      key: 'agents',
      label: (
        <Space>
          <RobotOutlined /> 智能 Agent 实例
        </Space>
      ),
      children: (
        <div className="tab-pane-content fade-in">
          <div className="table-actions-header">
            <Title level={5} className="table-title-main">活跃 Agent 列表</Title>
            <Button type="primary" icon={<PlusOutlined />} onClick={() => handleAdd('agent')} className="hover-float">
              部署新 Agent
            </Button>
          </div>
          <Table 
            columns={agentColumns} 
            dataSource={agentList} 
            rowKey="id" 
            className="custom-table"
          />
        </div>
      ),
    },
    {
      key: 'mcp',
      label: (
        <Space>
          <ApiOutlined /> MCP 服务组件
        </Space>
      ),
      children: (
        <div className="tab-pane-content fade-in">
          <div className="table-actions-header">
            <Title level={5} className="table-title-main">注册服务接口</Title>
            <Button type="primary" icon={<PlusOutlined />} onClick={() => handleAdd('mcp')} className="hover-float">
              注册 MCP 服务
            </Button>
          </div>
          <Table 
            columns={mcpColumns} 
            dataSource={mcpServices} 
            rowKey="id" 
            className="custom-table"
          />
        </div>
      ),
    },
    {
      key: 'prompts',
      label: (
        <Space>
          <FileTextOutlined /> Prompt 模板库
        </Space>
      ),
      children: (
        <div className="tab-pane-content fade-in">
          <div className="table-actions-header">
            <Title level={5} className="table-title-main">指令模板预设</Title>
            <Button type="primary" icon={<PlusOutlined />} onClick={() => handleAdd('prompt')} className="hover-float">
              创建模板
            </Button>
          </div>
          <Table 
            columns={promptColumns} 
            dataSource={promptTemplates} 
            rowKey="id" 
            className="custom-table"
          />
        </div>
      ),
    },
  ]

  return (
    <div className="agent-management page-container fade-in">
      <div className="page-header slide-up">
        <div>
          <Title level={2} className="page-title">
            <RobotOutlined /> Agent 管理中心
          </Title>
          <Paragraph className="page-description">
            统筹管理 AI 智能体、模型上下文协议服务以及结构化指令模板
          </Paragraph>
        </div>
      </div>

      <Row gutter={[24, 24]} style={{ marginBottom: '24px' }}>
        <Col xs={24} sm={8} className="slide-up stagger-1">
          <Card className="glass-card hover-float stat-mini-card">
            <Statistic
              title="活跃 Agent"
              value={agentList.length}
              prefix={<ThunderboltOutlined />}
              valueStyle={{ color: 'var(--color-success)', fontWeight: 700 }}
            />
            <div className="stat-footer">
              <Text type="secondary">并发任务: 14</Text>
            </div>
          </Card>
        </Col>
        <Col xs={24} sm={8} className="slide-up stagger-2">
          <Card className="glass-card hover-float stat-mini-card">
            <Statistic
              title="MCP 连接数"
              value={mcpServices.length}
              prefix={<ApiOutlined />}
              valueStyle={{ color: 'var(--color-info)', fontWeight: 700 }}
            />
            <div className="stat-footer">
              <Text type="secondary">服务状态: 健康</Text>
            </div>
          </Card>
        </Col>
        <Col xs={24} sm={8} className="slide-up stagger-3">
          <Card className="glass-card hover-float stat-mini-card">
            <Statistic
              title="模板使用量"
              value={promptTemplates.length}
              prefix={<FileTextOutlined />}
              valueStyle={{ color: 'var(--color-warning)', fontWeight: 700 }}
            />
            <div className="stat-footer">
              <Text type="secondary">覆盖 12 个攻防场景</Text>
            </div>
          </Card>
        </Col>
      </Row>

      <div className="slide-up stagger-2">
        <Card className="glass-card table-tabs-card">
          <Tabs items={tabItems} className="custom-tabs" />
        </Card>
      </div>

      <Modal
        title={
          <Space>
            <SettingOutlined />
            {`配置${modalType === 'agent' ? ' Agent' : modalType === 'mcp' ? ' MCP 服务' : ' Prompt 模板'}`}
          </Space>
        }
        open={isModalOpen}
        onCancel={() => setIsModalOpen(false)}
        footer={null}
        className="project-modal"
        destroyOnClose
      >
        <Form layout="vertical">
          <Form.Item label="资源名称" required>
            <Input placeholder="请输入名称" />
          </Form.Item>
          {modalType === 'agent' && (
            <Form.Item label="Agent 类型" required>
              <Select placeholder="请选择类型">
                <Select.Option value="collector">信息收集型</Select.Option>
                <Select.Option value="generator">内容生成型</Select.Option>
                <Select.Option value="assistant">对话助手型</Select.Option>
              </Select>
            </Form.Item>
          )}
          {modalType === 'prompt' && (
            <Form.Item label="Prompt 模板内容" required>
              <Input.TextArea rows={6} placeholder="请输入结构化 Prompt 内容..." />
            </Form.Item>
          )}
          <Divider style={{ margin: '24px 0 16px' }} />
          <div className="modal-footer-actions">
            <Button onClick={() => setIsModalOpen(false)} style={{ marginRight: '12px' }}>
              取消
            </Button>
            <Button type="primary" className="hover-float">
              确认提交
            </Button>
          </div>
        </Form>
      </Modal>
    </div>
  )
}
