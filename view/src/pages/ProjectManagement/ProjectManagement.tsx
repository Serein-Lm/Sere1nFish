import { useEffect, useMemo, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { Card, Button, Modal, Form, Input, Skeleton, Tag, Typography, Space, Divider } from 'antd'
import { PlusOutlined, FolderOutlined, ClockCircleOutlined, RightOutlined } from '@ant-design/icons'
import { createProject, listProjects, type Project } from '../../services/projectService'
import { stringToColor } from '../../utils/colorUtils'
import './ProjectManagement.css'

const { Title, Paragraph, Text } = Typography

export default function ProjectManagement() {
  const navigate = useNavigate()

  const [projects, setProjects] = useState<Project[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  const [isModalOpen, setIsModalOpen] = useState(false)
  const [form] = Form.useForm()

  const reload = async () => {
    setLoading(true)
    setError(null)
    try {
      const data = await listProjects({ page: 1, page_size: 50 })
      setProjects(data.items)
    } catch (e) {
      const msg = e instanceof Error ? e.message : '加载失败'
      setError(msg)
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    reload()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  const handleAdd = () => {
    form.resetFields()
    setIsModalOpen(true)
  }

  const handleModalOk = () => {
    form.validateFields().then(async (values) => {
      const name = values.name as string
      const description = (values.description as string | undefined) || undefined
      await createProject({ name, description })
      setIsModalOpen(false)
      await reload()
    })
  }

  const projectCards = useMemo(() => {
    return projects.map((p, idx) => {
      const delayClass = `stagger-${Math.min(idx + 1, 6)}`
      const tags: string[] = []
      if (p.description) tags.push('有描述')

      return (
        <Card
          key={p.id}
          className={`glass-card project-card slide-up ${delayClass} hover-float`}
          onClick={() => navigate(`/projects/${p.id}`)}
          styles={{ body: { padding: '20px' } }}
        >
          <div className="project-card-header-icon">
            <FolderOutlined />
          </div>
          <div className="project-card-main">
            <div className="project-card-title-row">
              <Title level={4} className="project-card-title">
                {p.name}
              </Title>
            </div>
            <Paragraph className="project-card-desc" ellipsis={{ rows: 2 }}>
              {p.description || '暂无项目描述信息'}
            </Paragraph>
            <div className="project-card-tags">
              {tags.map((t) => (
                <Tag key={t} color={stringToColor(t)} variant="filled">
                  {t}
                </Tag>
              ))}
            </div>
            <Divider style={{ margin: '12px 0', opacity: 0.5 }} />
            <div className="project-card-footer">
              <Space className="project-card-time">
                <ClockCircleOutlined style={{ fontSize: '12px' }} />
                <Text type="secondary" style={{ fontSize: '12px' }}>
                  {new Date(p.updated_at).toLocaleDateString()}
                </Text>
              </Space>
              <Button type="link" size="small" icon={<RightOutlined />} className="view-link">
                查看详情
              </Button>
            </div>
          </div>
        </Card>
      )
    })
  }, [navigate, projects])

  return (
    <div className="project-management page-container fade-in">
      <div className="page-header slide-up">
        <div>
          <Title level={2} className="page-title">
            <FolderOutlined /> 项目管理
          </Title>
          <Paragraph className="page-description">管理所有项目</Paragraph>
        </div>
        <Button type="primary" icon={<PlusOutlined />} onClick={handleAdd} className="add-btn hover-float">
          新建项目
        </Button>
      </div>

      <Card className="glass-card slide-up stagger-1">
        {loading ? (
          <Skeleton active />
        ) : error ? (
          <Text type="danger">{error}</Text>
        ) : (
          <div className="project-card-grid">{projectCards}</div>
        )}
      </Card>

      <Modal
        title={'新建项目'}
        open={isModalOpen}
        onOk={handleModalOk}
        onCancel={() => setIsModalOpen(false)}
        width={600}
        className="project-modal"
      >
        <Form form={form} layout="vertical">
          <Form.Item
            name="name"
            label="项目名称"
            rules={[{ required: true, message: '请输入项目名称' }]}
          >
            <Input placeholder="请输入项目名称" />
          </Form.Item>

          <Form.Item name="description" label="项目描述">
            <Input.TextArea placeholder="可选" rows={4} />
          </Form.Item>
        </Form>
      </Modal>
    </div>
  )
}
