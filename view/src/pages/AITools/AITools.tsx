import { useState } from 'react'
import { Card, Row, Col, Button, Tabs, Typography, Tag } from 'antd'
import {
  AudioOutlined,
  PictureOutlined,
  VideoCameraOutlined,
  SwapOutlined,
  GlobalOutlined,
  RobotOutlined,
  ArrowRightOutlined,
} from '@ant-design/icons'
import VoiceClone from './VoiceClone'
import BailianMedia from './BailianMedia'
import './AITools.css'

const { Title, Paragraph } = Typography

export default function AITools() {
  const [activeTab, setActiveTab] = useState('all')
  const tools = [
    {
      icon: <AudioOutlined />,
      title: 'AI-TTS 语音合成',
      description: '文字转语音，支持多种语音模板',
      color: '#1890ff',
      tabKey: 'voice',
    },
    {
      icon: <PictureOutlined />,
      title: 'AI图片生成',
      description: '生成和修改图片',
      color: '#52c41a',
      tabKey: 'image',
    },
    {
      icon: <VideoCameraOutlined />,
      title: 'AI视频生成',
      description: '生成和编辑视频内容',
      color: '#722ed1',
      tabKey: 'video',
    },
    {
      icon: <SwapOutlined />,
      title: 'AI换脸',
      description: 'DeepFake 换脸技术',
      color: '#faad14',
      path: '/ai-tools/deepfake',
      tabKey: undefined,
    },
    {
      icon: <GlobalOutlined />,
      title: '钓鱼网站生成',
      description: '快速克隆目标网站',
      color: '#13c2c2',
      path: '/ai-tools/website',
      tabKey: undefined,
    },
    {
      icon: <RobotOutlined />,
      title: '钓鱼小助手',
      description: 'AI对话辅助生成钓鱼内容',
      color: '#eb2f96',
      path: '/ai-tools/assistant',
      tabKey: undefined,
    },
  ]

  const tabItems = [
    {
      key: 'all',
      label: '全部工具',
      children: (
        <Row gutter={[24, 24]}>
          {tools.map((tool, idx) => {
            const delayClass = `stagger-${Math.min(idx + 1, 6)}`
            return (
              <Col xs={24} sm={12} lg={8} key={idx} className={`slide-up ${delayClass}`}>
                <Card className="tool-card glass-card hover-float">
                  <div className="tool-header">
                    <div className="tool-icon-wrapper" style={{ background: `${tool.color}15`, color: tool.color }}>
                      {tool.icon}
                    </div>
                    <div className="tool-info">
                      <Title level={4} className="tool-title">{tool.title}</Title>
                      <Tag color={tool.tabKey ? tool.color : 'default'} variant={tool.tabKey ? 'filled' : 'outlined'} className="tool-tag">
                        {tool.tabKey ? 'Active' : 'Pending'}
                      </Tag>
                    </div>
                  </div>
                  <Paragraph className="tool-description" ellipsis={{ rows: 2 }}>
                    {tool.description}
                  </Paragraph>
                  <Button
                    type="primary"
                    block
                    className="tool-btn"
                    icon={<ArrowRightOutlined />}
                    onClick={() => tool.tabKey && setActiveTab(tool.tabKey)}
                    disabled={!tool.tabKey}
                  >
                    {tool.tabKey ? '立即使用' : '未接入'}
                  </Button>
                </Card>
              </Col>
            )
          })}
        </Row>
      ),
    },
    {
      key: 'voice',
      label: '语音工具',
      children: <VoiceClone />,
    },
    {
      key: 'image',
      label: '图像工具',
      children: <BailianMedia mode="image" />,
    },
    {
      key: 'video',
      label: '视频工具',
      children: <BailianMedia mode="video" />,
    },
  ]

  return (
    <div className="ai-tools page-container fade-in">
      <div className="page-header slide-up">
        <div>
          <Title level={2} className="page-title">
            <RobotOutlined /> AI工具箱
          </Title>
          <Paragraph className="page-description">AI驱动的多媒体生成和处理工具，助力更高效的自动化流程</Paragraph>
        </div>
      </div>

      <div className="slide-up stagger-1">
        <Card className="glass-card tool-tabs-container">
          <Tabs items={tabItems} className="custom-tabs" activeKey={activeTab} onChange={setActiveTab} />
        </Card>
      </div>
    </div>
  )
}
