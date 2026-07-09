import { useState } from 'react'
import { Card, Row, Col, Badge, Tag, Space, Typography } from 'antd'
import { Bubble, Sender } from '@ant-design/x'
import {
  ApiOutlined,
  GlobalOutlined,
  WechatOutlined,
  SearchOutlined,
  UserOutlined,
  RobotOutlined,
  ThunderboltOutlined,
  ArrowRightOutlined,
} from '@ant-design/icons'
import { capabilities } from '../../utils/mockData'
import './Capabilities.css'

const { Title, Paragraph, Text } = Typography

interface Message {
  id: string
  role: 'user' | 'assistant'
  content: string
}

export default function Capabilities() {
  const [selectedCapability, setSelectedCapability] = useState<string | null>(null)
  const [messages, setMessages] = useState<Message[]>([])
  const [inputValue, setInputValue] = useState('')

  const capabilityIcons: Record<string, React.ReactNode> = {
    'official-website': <GlobalOutlined />,
    'wechat-official': <WechatOutlined />,
    'tianyancha': <SearchOutlined />,
    'xiaohongshu': <UserOutlined />,
    'maimai': <UserOutlined />,
    'douyin': <ThunderboltOutlined />,
  }

  const handleCapabilityClick = (capId: string) => {
    setSelectedCapability(capId)
    const cap = capabilities.find((c) => c.id === capId)
    if (cap) {
      const msg: Message = {
        id: Date.now().toString(),
        role: 'assistant',
        content: `已选择能力：${cap.name}\n\n${cap.description}\n\n请输入目标信息开始收集。`,
      }
      setMessages([msg])
    }
  }

  const handleSend = (value: string) => {
    if (!value.trim()) return

    const userMsg: Message = {
      id: Date.now().toString(),
      role: 'user',
      content: value,
    }
    setMessages((prev) => [...prev, userMsg])
    setInputValue('')

    setTimeout(() => {
      const aiMsg: Message = {
        id: (Date.now() + 1).toString(),
        role: 'assistant',
        content: `正在使用 ${selectedCapability} 能力收集信息...\n\n这是模拟数据，实际应用中会调用相应的API进行信息收集。`,
      }
      setMessages((prev) => [...prev, aiMsg])
    }, 1000)
  }

  return (
    <div className="capabilities page-container fade-in">
      <div className="page-header slide-up">
        <div>
          <Title level={2} className="page-title">
            <ApiOutlined /> 能力复用
          </Title>
          <Paragraph className="page-description">
            标准化信息收集能力管理与交互式使用
          </Paragraph>
        </div>
      </div>

      <Row gutter={[24, 24]}>
        <Col xs={24} lg={8} className="slide-up stagger-1">
          <Card 
            title={<Space><ThunderboltOutlined /> 可用能力</Space>} 
            className="glass-card capability-list-card"
          >
            <div className="capability-list-wrapper">
              {capabilities.map((cap, idx) => (
                <div
                  key={cap.id}
                  className={`capability-item-card ${selectedCapability === cap.id ? 'active' : ''} slide-up stagger-${(idx % 3) + 1}`}
                  onClick={() => handleCapabilityClick(cap.id)}
                >
                  <div className="cap-item-header">
                    <div className="cap-item-icon" style={{ color: 'var(--primary-color)' }}>
                      {capabilityIcons[cap.id] || <ApiOutlined />}
                    </div>
                    <Badge status="success" text={<Text type="secondary" style={{ fontSize: '12px' }}>在线</Text>} />
                  </div>
                  <div className="cap-item-content">
                    <Title level={5} className="cap-item-name">{cap.name}</Title>
                    <Paragraph className="cap-item-desc" ellipsis={{ rows: 1 }}>
                      {cap.description}
                    </Paragraph>
                    <div className="cap-item-footer">
                      <Tag color="blue" bordered={false}>{cap.category}</Tag>
                      {selectedCapability === cap.id && <ArrowRightOutlined className="active-arrow" />}
                    </div>
                  </div>
                </div>
              ))}
            </div>
          </Card>
        </Col>

        <Col xs={24} lg={16} className="slide-up stagger-2">
          <Card 
            title={<Space><RobotOutlined /> 能力交互控制台</Space>} 
            className="glass-card chat-card-advanced"
          >
            <div className="chat-interface-wrapper">
              <div className="chat-messages-scroll">
                {messages.length === 0 ? (
                  <div className="chat-empty-state">
                    <div className="empty-icon-wrapper">
                      <RobotOutlined className="empty-icon" />
                    </div>
                    <Title level={4}>准备就绪</Title>
                    <Paragraph type="secondary">请在左侧选择一项原子能力，然后在此输入指令开始工作</Paragraph>
                  </div>
                ) : (
                  <div className="message-list">
                    {messages.map((msg) => (
                      <div key={msg.id} className={`message-item-row ${msg.role}`}>
                        <Bubble
                          avatar={
                            msg.role === 'assistant' ? (
                              <div className="avatar-icon assistant">
                                <RobotOutlined />
                              </div>
                            ) : (
                              <div className="avatar-icon user">
                                <UserOutlined />
                              </div>
                            )
                          }
                          placement={msg.role === 'assistant' ? 'start' : 'end'}
                          content={msg.content}
                          className={`custom-bubble ${msg.role}`}
                        />
                      </div>
                    ))}
                  </div>
                )}
              </div>

              <div className="chat-sender-area">
                <Sender
                  value={inputValue}
                  onChange={setInputValue}
                  onSubmit={handleSend}
                  placeholder={selectedCapability ? `正在使用 [${capabilities.find(c => c.id === selectedCapability)?.name}]，请输入目标...` : "请先选择能力..."}
                  disabled={!selectedCapability}
                  className="advanced-sender"
                />
              </div>
            </div>
          </Card>
        </Col>
      </Row>
    </div>
  )
}
