import { useState, useRef, useEffect } from 'react'
import { Modal, Button, Input, Form, Avatar, Spin, Tag, Descriptions, Progress, Collapse, Space, message } from 'antd'
import { UserOutlined, LoadingOutlined, CheckCircleOutlined, ExclamationCircleOutlined, AimOutlined, EyeOutlined, SafetyOutlined, StopOutlined, ExpandOutlined, CompressOutlined } from '@ant-design/icons'
import XMarkdown from '@ant-design/x-markdown'
import { generateProfileStream, cancelProfileTask, type XhsProfile, type ProfileStage } from '../../services/xhsService'
import './ProfileDrawer.css'

const { Panel } = Collapse

interface ProfileDrawerProps {
  open: boolean
  onClose: () => void
  projectId: string
  keyword?: string
  onSuccess?: () => void
}

type GenerateStatus = 'idle' | 'generating' | 'success' | 'error' | 'cancelled'

interface StatusItem {
  message: string
  timestamp: number
}

export default function ProfileDrawer({ open, onClose, projectId, keyword, onSuccess }: ProfileDrawerProps) {
  const [form] = Form.useForm()
  const [status, setStatus] = useState<GenerateStatus>('idle')
  const [statusHistory, setStatusHistory] = useState<StatusItem[]>([])
  const [avatarUrl, setAvatarUrl] = useState('')
  const [visionContent, setVisionContent] = useState('')
  const [profile, setProfile] = useState<XhsProfile | null>(null)
  const [userId, setUserId] = useState('')
  const [taskId, setTaskId] = useState('')
  const [currentStage, setCurrentStage] = useState<ProfileStage>('screenshot')
  const [cancelling, setCancelling] = useState(false)
  const [visionExpanded, setVisionExpanded] = useState(false)
  const contentRef = useRef<HTMLDivElement>(null)

  // 自动滚动到底部
  useEffect(() => {
    if (contentRef.current) {
      contentRef.current.scrollTop = contentRef.current.scrollHeight
    }
  }, [visionContent, statusHistory])

  // 重置状态
  const resetState = () => {
    setStatus('idle')
    setStatusHistory([])
    setAvatarUrl('')
    setVisionContent('')
    setProfile(null)
    setUserId('')
    setTaskId('')
    setCurrentStage('screenshot')
    setCancelling(false)
    setVisionExpanded(false)
  }

  // 添加状态消息
  const addStatus = (message: string) => {
    setStatusHistory(prev => [...prev, { message, timestamp: Date.now() }])
  }

  const handleClose = () => {
    if (status === 'generating') {
      message.warning('正在生成中，请先取消任务')
      return
    }
    resetState()
    form.resetFields()
    onClose()
  }

  const handleCancel = async () => {
    if (!taskId) {
      message.warning('任务尚未开始')
      return
    }
    
    setCancelling(true)
    try {
      await cancelProfileTask(taskId)
      // 根据当前阶段显示不同提示
      if (currentStage === 'vision' || currentStage === 'format') {
        message.info('正在取消任务，需等待当前 API 请求完成...')
      } else {
        message.info('正在取消任务...')
      }
    } catch (e) {
      const msg = e instanceof Error ? e.message : '取消失败'
      message.error(msg)
      setCancelling(false)
    }
  }

  const handleGenerate = async () => {
    try {
      const values = await form.validateFields()
      resetState()
      setStatus('generating')

      await generateProfileStream(
        {
          user_url: values.user_url,
          project_id: projectId,
          keyword: keyword || values.keyword,
        },
        {
          onInit: (id, uid, stage) => {
            setTaskId(id)
            setUserId(uid)
            setCurrentStage(stage)
            addStatus(`🚀 任务初始化 (ID: ${id.slice(0, 8)}...)`)
          },
          onStatus: (msg, stage) => {
            if (stage) setCurrentStage(stage)
            addStatus(msg)
          },
          onAvatar: (url) => {
            setAvatarUrl(url)
          },
          onContent: (_, accumulated) => {
            setVisionContent(accumulated)
          },
          onProfile: (data) => {
            setProfile(data)
          },
          onDone: (uid) => {
            setUserId(uid)
            setStatus('success')
            message.success('人物画像生成完成')
            onSuccess?.()
          },
          onCancelled: (msg, stage) => {
            setStatus('cancelled')
            const stageText = stage === 'vision' ? '（视觉分析阶段）' : 
                             stage === 'format' ? '（格式化阶段）' : ''
            addStatus(`⚠️ ${msg} ${stageText}`)
            setCancelling(false)
            message.info('任务已取消')
          },
          onError: (error) => {
            setStatus('error')
            addStatus(`❌ 错误: ${error}`)
            setCancelling(false)
            message.error(error)
          },
        }
      )
    } catch (e) {
      const msg = e instanceof Error ? e.message : '生成失败'
      setStatus('error')
      addStatus(`❌ ${msg}`)
      setCancelling(false)
      message.error(msg)
    }
  }

  const renderStatusIcon = () => {
    switch (status) {
      case 'generating':
        return <LoadingOutlined spin style={{ color: '#1890ff' }} />
      case 'success':
        return <CheckCircleOutlined style={{ color: '#52c41a' }} />
      case 'error':
        return <ExclamationCircleOutlined style={{ color: '#ff4d4f' }} />
      case 'cancelled':
        return <StopOutlined style={{ color: '#faad14' }} />
      default:
        return null
    }
  }

  const renderProfile = () => {
    if (!profile) return null

    return (
      <div className="profile-result">
        <Collapse defaultActiveKey={['basic', 'identity', 'risk', 'attack']} ghost>
          {/* 基本信息 */}
          <Panel header={<Space><UserOutlined /> 基本信息</Space>} key="basic">
            <Descriptions size="small" column={1} bordered>
              {profile.nickname && (
                <Descriptions.Item label="昵称">{profile.nickname}</Descriptions.Item>
              )}
              {profile.basic_info?.ip_location && (
                <Descriptions.Item label="IP 归属">{profile.basic_info.ip_location}</Descriptions.Item>
              )}
              {profile.basic_info?.account_type && (
                <Descriptions.Item label="账号类型">{profile.basic_info.account_type}</Descriptions.Item>
              )}
              {profile.gender_analysis?.conclusion && (
                <Descriptions.Item label="性别">
                  {profile.gender_analysis.conclusion}
                  <Tag style={{ marginLeft: 8 }}>{profile.gender_analysis.confidence}</Tag>
                </Descriptions.Item>
              )}
              {profile.stats && (
                <>
                  <Descriptions.Item label="粉丝">{profile.stats.fans}</Descriptions.Item>
                  <Descriptions.Item label="关注">{profile.stats.follows}</Descriptions.Item>
                  <Descriptions.Item label="获赞与收藏">{profile.stats.likes_and_collects}</Descriptions.Item>
                  <Descriptions.Item label="笔记数">{profile.stats.notes_count}</Descriptions.Item>
                </>
              )}
            </Descriptions>

            {profile.personality_profile?.keywords && profile.personality_profile.keywords.length > 0 && (
              <div className="profile-personality">
                <div className="section-label">性格特征</div>
                <div className="personality-tags">
                  {profile.personality_profile.keywords.map((k, i) => (
                    <Tag key={i} color="purple">{k}</Tag>
                  ))}
                </div>
                {profile.personality_profile.mbti_estimate && (
                  <div className="personality-desc">MBTI: {profile.personality_profile.mbti_estimate}</div>
                )}
              </div>
            )}
          </Panel>

          {/* 身份信息 */}
          <Panel header={<Space><UserOutlined /> 身份信息</Space>} key="identity">
            <Descriptions size="small" column={1} bordered>
              {profile.identity?.company && (
                <Descriptions.Item label="公司">{profile.identity.company}</Descriptions.Item>
              )}
              {profile.identity?.position && (
                <Descriptions.Item label="职位">{profile.identity.position}</Descriptions.Item>
              )}
              {profile.identity?.department && (
                <Descriptions.Item label="部门">{profile.identity.department}</Descriptions.Item>
              )}
              {profile.identity?.industry && (
                <Descriptions.Item label="行业">{profile.identity.industry}</Descriptions.Item>
              )}
              {profile.identity?.employment_status && (
                <Descriptions.Item label="状态">{profile.identity.employment_status}</Descriptions.Item>
              )}
              {profile.bio_analysis?.education?.degree && (
                <Descriptions.Item label="学历">
                  {profile.bio_analysis.education.school_tier} {profile.bio_analysis.education.degree}
                </Descriptions.Item>
              )}
              {profile.bio_analysis?.location?.city && (
                <Descriptions.Item label="城市">{profile.bio_analysis.location.city}</Descriptions.Item>
              )}
            </Descriptions>

            {profile.tags && profile.tags.length > 0 && (
              <div className="profile-tags">
                <div className="section-label">标签</div>
                <div className="personality-tags">
                  {profile.tags.map((tag, i) => (
                    <Tag key={i} color="blue">{tag}</Tag>
                  ))}
                </div>
              </div>
            )}
          </Panel>

          {/* 风险评估 */}
          <Panel header={<Space><SafetyOutlined /> 风险评估</Space>} key="risk">
            <div className="risk-scores">
              <div className="score-item">
                <span className="score-label">关键词关联度</span>
                <Progress
                  percent={profile.keyword_relevance?.score || 0}
                  size="small"
                  strokeColor={(profile.keyword_relevance?.score || 0) >= 70 ? '#ff4d4f' : (profile.keyword_relevance?.score || 0) >= 50 ? '#faad14' : '#1890ff'}
                />
                {profile.keyword_relevance?.analysis && (
                  <div className="score-reason">{profile.keyword_relevance.analysis}</div>
                )}
              </div>

              <div className="score-item">
                <span className="score-label">关注度</span>
                <Progress
                  percent={profile.attention_score || 0}
                  size="small"
                  strokeColor={(profile.attention_score || 0) >= 70 ? '#ff4d4f' : (profile.attention_score || 0) >= 40 ? '#faad14' : '#1890ff'}
                />
              </div>

              {profile.attack_surface && (
                <div className="score-item">
                  <span className="score-label">风险等级</span>
                  <Tag color={
                    profile.attack_surface.risk_level === '高' || profile.attack_surface.risk_level === '极高' ? 'error' :
                    profile.attack_surface.risk_level === '中' ? 'warning' : 'success'
                  }>
                    {profile.attack_surface.risk_level} ({profile.attack_surface.risk_score})
                  </Tag>
                </div>
              )}
            </div>

            {profile.company_identification?.identified_company && (
              <div className="company-identified">
                <div className="section-label">公司识别</div>
                <Tag color="blue">{profile.company_identification.identified_company}</Tag>
                <Tag>{profile.company_identification.confidence}</Tag>
                {profile.company_identification.evidence && profile.company_identification.evidence.length > 0 && (
                  <div className="evidence-list">
                    {profile.company_identification.evidence.map((e, i) => (
                      <span key={i} className="evidence-item">• {e}</span>
                    ))}
                  </div>
                )}
              </div>
            )}
          </Panel>

          {/* 攻击面分析 */}
          <Panel header={<Space><AimOutlined /> 攻击面分析</Space>} key="attack">
            {profile.attack_surface?.exposed_information && profile.attack_surface.exposed_information.length > 0 && (
              <div className="exposed-info">
                <div className="section-label">暴露信息</div>
                <div className="exposed-list">
                  {profile.attack_surface.exposed_information.map((info, i) => (
                    <div key={i} className="exposed-item">
                      <Tag color="orange">{info.type}</Tag>
                      <span className="exposed-value">{info.value}</span>
                      <span className="exposed-source">来源: {info.source}</span>
                    </div>
                  ))}
                </div>
              </div>
            )}

            {profile.attack_surface?.attack_vectors && profile.attack_surface.attack_vectors.length > 0 && (
              <div className="attack-vectors">
                <div className="section-label">攻击向量</div>
                <div className="vector-list-new">
                  {profile.attack_surface.attack_vectors.map((v, i) => (
                    <div key={i} className="vector-item">
                      <div className="vector-header">
                        <Tag color={v.difficulty === '低' ? 'green' : v.difficulty === '中' ? 'orange' : 'red'}>
                          {v.difficulty}难度
                        </Tag>
                        <span className="vector-name">{v.vector}</span>
                      </div>
                      <div className="vector-method">{v.method}</div>
                    </div>
                  ))}
                </div>
              </div>
            )}

            {profile.recommended_actions && profile.recommended_actions.length > 0 && (
              <div className="recommended-actions">
                <div className="section-label"><EyeOutlined /> 建议操作</div>
                <div className="action-list-new">
                  {profile.recommended_actions.map((a, i) => (
                    <div key={i} className="action-item">
                      <div className="action-header">
                        <Tag color={a.priority === '高' ? 'red' : a.priority === '中' ? 'orange' : 'blue'}>
                          {a.priority}优先级
                        </Tag>
                        <span className="action-name">{a.action}</span>
                      </div>
                      <div className="action-desc">{a.description}</div>
                    </div>
                  ))}
                </div>
              </div>
            )}
          </Panel>

          {/* 画像摘要 */}
          {profile.profile_summary && (
            <Panel header="画像摘要" key="summary">
              <XMarkdown content={profile.profile_summary} />
            </Panel>
          )}
        </Collapse>
      </div>
    )
  }

  return (
    <Modal
      title="生成人物画像"
      open={open}
      onCancel={handleClose}
      centered
      width={800}
      className="profile-modal"
      footer={
        <div className="profile-modal-footer">
          {status === 'generating' && (
            <Button
              danger
              icon={<StopOutlined />}
              onClick={handleCancel}
              loading={cancelling}
              disabled={!taskId || cancelling}
            >
              取消生成
            </Button>
          )}
          <Button onClick={handleClose} disabled={status === 'generating'}>
            关闭
          </Button>
          <Button
            type="primary"
            onClick={handleGenerate}
            loading={status === 'generating'}
            disabled={status === 'generating'}
          >
            {status === 'generating' ? '生成中...' : '开始生成'}
          </Button>
        </div>
      }
    >
      <div className="profile-modal-content">
        {/* 输入表单 */}
        <Form form={form} layout="vertical" className="profile-form">
          <Form.Item
            name="user_url"
            label="小红书用户主页 URL"
            rules={[
              { required: true, message: '请输入用户主页 URL' },
              { pattern: /xiaohongshu\.com\/user\/profile\//, message: '请输入有效的小红书用户主页 URL' }
            ]}
          >
            <Input placeholder="https://www.xiaohongshu.com/user/profile/xxx" />
          </Form.Item>
          {!keyword && (
            <Form.Item name="keyword" label="关联关键词（可选）">
              <Input placeholder="用于关联度分析，如公司名" />
            </Form.Item>
          )}
        </Form>

        {/* 状态显示 */}
        {status !== 'idle' && (
          <div className="profile-status">
            {/* 进度历史 */}
            {statusHistory.length > 0 && (
              <div className="status-history">
                <div className="status-history-title">
                  {renderStatusIcon()}
                  <span>执行进度</span>
                </div>
                <div className="status-history-list">
                  {statusHistory.map((item) => (
                    <div key={item.timestamp} className="status-history-item">
                      <CheckCircleOutlined className="status-item-icon" />
                      <span className="status-item-text">{item.message}</span>
                    </div>
                  ))}
                  {status === 'generating' && (
                    <div className="status-history-item status-current">
                      <LoadingOutlined spin className="status-item-icon" />
                      <span className="status-item-text">处理中...</span>
                    </div>
                  )}
                </div>
              </div>
            )}

            {/* 头像 */}
            {avatarUrl && (
              <div className="profile-avatar">
                <Avatar size={64} src={avatarUrl} icon={<UserOutlined />} />
                {userId && (
                  <a
                    href={`https://www.xiaohongshu.com/user/profile/${userId}`}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="user-link"
                  >
                    查看主页
                  </a>
                )}
              </div>
            )}

            {/* 视觉分析内容（流式） */}
            {visionContent && (
              <>
                {visionExpanded && <div className="vision-expanded-overlay" onClick={() => setVisionExpanded(false)} />}
                <div className={`vision-content ${visionExpanded ? 'vision-expanded' : ''}`} ref={contentRef}>
                  <div className="section-title">
                    <span>🔍 视觉分析</span>
                    <Button
                      type="text"
                      size="small"
                      icon={visionExpanded ? <CompressOutlined /> : <ExpandOutlined />}
                      onClick={() => setVisionExpanded(!visionExpanded)}
                      className="expand-btn"
                    />
                  </div>
                  <div className="vision-markdown">
                    <XMarkdown content={visionContent} />
                  </div>
                </div>
              </>
            )}

            {/* 生成中的加载状态 */}
            {status === 'generating' && !profile && (
              <div className="generating-spinner">
                <Spin indicator={<LoadingOutlined style={{ fontSize: 24 }} spin />} />
              </div>
            )}

            {/* 最终画像结果 */}
            {profile && renderProfile()}
          </div>
        )}
      </div>
    </Modal>
  )
}
