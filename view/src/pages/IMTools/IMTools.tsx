import { useState } from 'react'
import { Card, Row, Col, Button, Upload, Tag, Modal, Input, Space, Tabs, Typography, Divider } from 'antd'
import {
  WechatOutlined,
  DingdingOutlined,
  CloudUploadOutlined,
  EyeOutlined,
  DeleteOutlined,
  CheckCircleOutlined,
  SettingOutlined,
  MessageOutlined,
} from '@ant-design/icons'
import { imConfigs } from '../../utils/mockData'
import './IMTools.css'

const { Title, Paragraph, Text } = Typography

export default function IMTools() {
  const [selectedIM, setSelectedIM] = useState<string>('wechat')
  const [isModalOpen, setIsModalOpen] = useState(false)
  const uploadedFiles = [
    { id: '1', name: '微信聊天记录_2024-01-20', type: 'wechat', status: 'active' },
    { id: '2', name: '企业微信通讯录', type: 'wecom', status: 'active' },
  ]

  const imTypes = [
    { key: 'wechat', name: '微信', icon: <WechatOutlined />, color: '#07c160' },
    { key: 'wecom', name: '企业微信', icon: <WechatOutlined />, color: '#2d8cf0' },
    { key: 'dingtalk', name: '钉钉', icon: <DingdingOutlined />, color: '#0089ff' },
    { key: 'feishu', name: '飞书', icon: <CloudUploadOutlined />, color: '#00d6b9' },
  ]

  const tabItems = [
    {
      key: 'visualization',
      label: '可视化展示',
      children: (
        <div className="visualization-content fade-in">
          <Row gutter={[24, 24]}>
            {uploadedFiles.map((file, idx) => (
              <Col xs={24} sm={12} lg={8} key={file.id} className={`slide-up stagger-${idx + 1}`}>
                <Card className="file-card glass-card hover-float">
                  <div className="file-header">
                    <div className="file-icon-wrapper">
                      <MessageOutlined className="file-icon" />
                    </div>
                    <Tag color="success" bordered={false} icon={<CheckCircleOutlined />}>
                      {file.status === 'active' ? '已激活' : '未激活'}
                    </Tag>
                  </div>
                  <Title level={4} className="file-name">{file.name}</Title>
                  <Divider style={{ margin: '12px 0', opacity: 0.5 }} />
                  <div className="file-actions">
                    <Button type="link" icon={<EyeOutlined />} size="small">
                      查看数据
                    </Button>
                    <Button type="text" danger icon={<DeleteOutlined />} size="small">
                      移除
                    </Button>
                  </div>
                </Card>
              </Col>
            ))}
            <Col xs={24} sm={12} lg={8} className="slide-up stagger-3">
              <Card className="upload-card glass-card hover-float" onClick={() => setIsModalOpen(true)}>
                <div className="upload-content">
                  <div className="upload-icon-wrapper">
                    <CloudUploadOutlined className="upload-icon" />
                  </div>
                  <Title level={4}>上传新数据</Title>
                  <Paragraph type="secondary">支持导入微信/企微聊天记录</Paragraph>
                </div>
              </Card>
            </Col>
          </Row>
        </div>
      ),
    },
    {
      key: 'key-usage',
      label: 'Key利用',
      children: (
        <div className="key-usage-content fade-in">
          <Row gutter={[24, 24]}>
            {imConfigs.map((config, idx) => (
              <Col xs={24} md={12} key={idx} className={`slide-up stagger-${(idx % 2) + 1}`}>
                <Card className="glass-card hover-float">
                  <div className="config-header">
                    <Title level={4}>{config.name}</Title>
                    <Tag color={config.status === 'configured' ? 'processing' : 'warning'} bordered={false}>
                      {config.status === 'configured' ? '已配置' : '未配置'}
                    </Tag>
                  </div>
                  <div className="config-body">
                    <div className="config-info-row">
                      <Text type="secondary">已配置数量</Text>
                      <Text strong>{config.count}</Text>
                    </div>
                    <Button type="primary" block icon={<SettingOutlined />} className="config-btn">
                      配置 Key 令牌
                    </Button>
                  </div>
                </Card>
              </Col>
            ))}
          </Row>
        </div>
      ),
    },
  ]

  return (
    <div className="im-tools page-container fade-in">
      <div className="page-header slide-up">
        <div>
          <Title level={2} className="page-title">
            <WechatOutlined /> IM工具管理
          </Title>
          <Paragraph className="page-description">即时通讯工具自动化利用与数据分析平台</Paragraph>
        </div>
      </div>

      <div className="im-type-selector slide-up stagger-1">
        <Row gutter={[16, 16]}>
          {imTypes.map((im) => (
            <Col xs={12} sm={6} key={im.key}>
              <Card
                className={`im-type-card glass-card hover-float ${selectedIM === im.key ? 'active' : ''}`}
                onClick={() => setSelectedIM(im.key)}
              >
                <div className="im-type-icon" style={{ color: im.color }}>
                  {im.icon}
                </div>
                <div className="im-type-name">{im.name}</div>
              </Card>
            </Col>
          ))}
        </Row>
      </div>

      <div className="slide-up stagger-2" style={{ marginTop: '24px' }}>
        <Card className="glass-card main-tabs-container">
          <Tabs items={tabItems} className="custom-tabs" />
        </Card>
      </div>

      <Modal
        title="上传IM数据"
        open={isModalOpen}
        onCancel={() => setIsModalOpen(false)}
        footer={null}
      >
        <Space direction="vertical" style={{ width: '100%' }} size="large">
          <Input placeholder="输入文件名称" />
          <Upload.Dragger>
            <p className="ant-upload-drag-icon">
              <CloudUploadOutlined />
            </p>
            <p className="ant-upload-text">点击或拖拽文件到此区域上传</p>
            <p className="ant-upload-hint">支持单个或批量上传</p>
          </Upload.Dragger>
          <Button type="primary" block>
            确认上传
          </Button>
        </Space>
      </Modal>
    </div>
  )
}
