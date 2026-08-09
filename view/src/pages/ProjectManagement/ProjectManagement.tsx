import { useEffect, useMemo, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import {
  Badge,
  Button,
  Card,
  Collapse,
  Empty,
  Form,
  Input,
  Modal,
  Popconfirm,
  Select,
  Skeleton,
  Space,
  Tooltip,
  Typography,
  message,
} from 'antd'
import {
  ClockCircleOutlined,
  DeleteOutlined,
  EditOutlined,
  FolderAddOutlined,
  FolderOpenOutlined,
  FolderOutlined,
  PlusOutlined,
  RightOutlined,
  SearchOutlined,
  SettingOutlined,
} from '@ant-design/icons'
import {
  createProject,
  createProjectGroup,
  deleteProjectGroup,
  listProjectGroups,
  listProjects,
  updateProject,
  updateProjectGroup,
  type Project,
  type ProjectGroup,
} from '../../services/projectService'
import './ProjectManagement.css'

const { Title, Paragraph, Text } = Typography
const UNGROUPED_KEY = '__ungrouped__'

interface ProjectSection {
  key: string
  name: string
  description: string
  projects: Project[]
}

export default function ProjectManagement() {
  const navigate = useNavigate()
  const [projects, setProjects] = useState<Project[]>([])
  const [groups, setGroups] = useState<ProjectGroup[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [search, setSearch] = useState('')
  const [activeGroups, setActiveGroups] = useState<string[]>([])
  const [isProjectModalOpen, setIsProjectModalOpen] = useState(false)
  const [isGroupManagerOpen, setIsGroupManagerOpen] = useState(false)
  const [isGroupEditorOpen, setIsGroupEditorOpen] = useState(false)
  const [editingGroup, setEditingGroup] = useState<ProjectGroup | null>(null)
  const [assigningProject, setAssigningProject] = useState<Project | null>(null)
  const [submitting, setSubmitting] = useState(false)
  const [projectForm] = Form.useForm()
  const [groupForm] = Form.useForm()
  const [assignmentForm] = Form.useForm()
  const [messageApi, messageContextHolder] = message.useMessage()

  const reload = async () => {
    setLoading(true)
    setError(null)
    try {
      const [projectData, groupData] = await Promise.all([
        listProjects({ page: 1, page_size: 200 }),
        listProjectGroups(),
      ])
      setProjects(projectData.items)
      setGroups(groupData)
      const validKeys = [
        ...groupData.map((group) => group.group_id),
        ...(projectData.items.some((project) => !project.group_id) ? [UNGROUPED_KEY] : []),
      ]
      setActiveGroups((current) => {
        const retained = current.filter((key) => validKeys.includes(key))
        return retained.length > 0 ? retained : validKeys.slice(0, 1)
      })
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : '加载失败')
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    void reload()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  const visibleProjects = useMemo(() => {
    const keyword = search.trim().toLocaleLowerCase()
    if (!keyword) return projects
    return projects.filter((project) =>
      [project.name, project.description, project.group_name]
        .filter(Boolean)
        .some((value) => String(value).toLocaleLowerCase().includes(keyword)),
    )
  }, [projects, search])

  const sections = useMemo<ProjectSection[]>(() => {
    const grouped = groups.map((group) => ({
      key: group.group_id,
      name: group.name,
      description: group.description,
      projects: visibleProjects.filter((project) => project.group_id === group.group_id),
    }))
    const ungrouped = visibleProjects.filter((project) => !project.group_id)
    if (ungrouped.length > 0) {
      grouped.push({
        key: UNGROUPED_KEY,
        name: '未分组',
        description: '尚未归入项目分组',
        projects: ungrouped,
      })
    }
    return search.trim() ? grouped.filter((section) => section.projects.length > 0) : grouped
  }, [groups, search, visibleProjects])

  useEffect(() => {
    if (search.trim()) setActiveGroups(sections.map((section) => section.key))
  }, [search, sections])

  const groupOptions = groups.map((group) => ({
    label: group.name,
    value: group.group_id,
  }))

  const openProjectModal = () => {
    projectForm.resetFields()
    setIsProjectModalOpen(true)
  }

  const submitProject = async () => {
    const values = await projectForm.validateFields()
    setSubmitting(true)
    try {
      await createProject({
        name: String(values.name).trim(),
        description: String(values.description || '').trim() || undefined,
        group_id: values.group_id || undefined,
      })
      setIsProjectModalOpen(false)
      messageApi.success('项目已创建')
      await reload()
    } finally {
      setSubmitting(false)
    }
  }

  const openGroupEditor = (group?: ProjectGroup) => {
    setEditingGroup(group || null)
    groupForm.setFieldsValue({
      name: group?.name || '',
      description: group?.description || '',
    })
    setIsGroupEditorOpen(true)
  }

  const submitGroup = async () => {
    const values = await groupForm.validateFields()
    setSubmitting(true)
    try {
      const body = {
        name: String(values.name).trim(),
        description: String(values.description || '').trim(),
      }
      if (editingGroup) {
        await updateProjectGroup(editingGroup.group_id, body)
        messageApi.success('分组已更新')
      } else {
        await createProjectGroup(body)
        messageApi.success('分组已创建')
      }
      setIsGroupEditorOpen(false)
      await reload()
    } finally {
      setSubmitting(false)
    }
  }

  const removeGroup = async (group: ProjectGroup) => {
    const result = await deleteProjectGroup(group.group_id)
    messageApi.success(
      result.ungrouped_count > 0
        ? `分组已删除，${result.ungrouped_count} 个项目已移至未分组`
        : '分组已删除',
    )
    await reload()
  }

  const openAssignment = (project: Project) => {
    setAssigningProject(project)
    assignmentForm.setFieldsValue({ group_id: project.group_id || undefined })
  }

  const submitAssignment = async () => {
    if (!assigningProject) return
    const values = await assignmentForm.validateFields()
    setSubmitting(true)
    try {
      await updateProject(assigningProject.id, { group_id: values.group_id || null })
      setAssigningProject(null)
      messageApi.success('项目分组已更新')
      await reload()
    } finally {
      setSubmitting(false)
    }
  }

  const renderProjectCard = (project: Project) => (
    <Card
      key={project.id}
      className="project-card"
      onClick={() => navigate(`/projects/${project.id}`)}
      styles={{ body: { padding: 18 } }}
    >
      <div className="project-card-heading">
        <div className="project-card-icon" aria-hidden="true">
          <FolderOutlined />
        </div>
        <Title level={4} className="project-card-title" ellipsis={{ rows: 2 }}>
          {project.name}
        </Title>
        <Tooltip title="调整分组">
          <Button
            type="text"
            size="small"
            icon={<FolderAddOutlined />}
            aria-label={`调整 ${project.name} 的分组`}
            onClick={(event) => {
              event.stopPropagation()
              openAssignment(project)
            }}
          />
        </Tooltip>
      </div>
      <Paragraph className="project-card-desc" ellipsis={{ rows: 2 }}>
        {project.description || '暂无项目描述'}
      </Paragraph>
      <div className="project-card-footer">
        <Space size={6} className="project-card-time">
          <ClockCircleOutlined />
          <Text type="secondary">{new Date(project.updated_at).toLocaleDateString()}</Text>
        </Space>
        <Button type="link" size="small" icon={<RightOutlined />} className="view-link">
          查看
        </Button>
      </div>
    </Card>
  )

  const collapseItems = sections.map((section) => ({
    key: section.key,
    label: (
      <div className="project-group-label">
        <FolderOpenOutlined />
        <span>{section.name}</span>
        <Badge count={section.projects.length} showZero color="#1677ff" />
        {section.description ? <Text type="secondary">{section.description}</Text> : null}
      </div>
    ),
    children:
      section.projects.length > 0 ? (
        <div className="project-card-grid">{section.projects.map(renderProjectCard)}</div>
      ) : (
        <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="该分组暂无项目" />
      ),
  }))

  return (
    <div className="project-management page-container fade-in">
      {messageContextHolder}
      <div className="page-header">
        <div>
          <Title level={2} className="page-title">
            <FolderOutlined /> 项目管理
          </Title>
          <Paragraph className="page-description">按工作批次组织项目与目标</Paragraph>
        </div>
        <Space wrap>
          <Button icon={<SettingOutlined />} onClick={() => setIsGroupManagerOpen(true)}>
            管理分组
          </Button>
          <Button type="primary" icon={<PlusOutlined />} onClick={openProjectModal}>
            新建项目
          </Button>
        </Space>
      </div>

      <div className="project-toolbar">
        <Input
          id="project-search"
          name="project-search"
          autoComplete="off"
          allowClear
          prefix={<SearchOutlined />}
          placeholder="搜索项目名称、描述或分组"
          value={search}
          onChange={(event) => setSearch(event.target.value)}
        />
        <Text type="secondary">共 {visibleProjects.length} 个项目</Text>
      </div>

      {loading ? (
        <Skeleton active paragraph={{ rows: 8 }} />
      ) : error ? (
        <div className="project-error">
          <Text type="danger">{error}</Text>
          <Button onClick={() => void reload()}>重新加载</Button>
        </div>
      ) : collapseItems.length > 0 ? (
        <Collapse
          className="project-groups"
          items={collapseItems}
          activeKey={activeGroups}
          onChange={(keys) => setActiveGroups(Array.isArray(keys) ? keys.map(String) : [String(keys)])}
        />
      ) : (
        <Empty description={search ? '没有匹配的项目' : '暂无项目'} />
      )}

      <Modal
        title="新建项目"
        open={isProjectModalOpen}
        onOk={() => void submitProject()}
        onCancel={() => setIsProjectModalOpen(false)}
        confirmLoading={submitting}
        width={560}
        forceRender
      >
        <Form form={projectForm} layout="vertical">
          <Form.Item name="name" label="项目名称" rules={[{ required: true, whitespace: true, message: '请输入项目名称' }]}>
            <Input autoComplete="off" maxLength={200} placeholder="请输入项目名称" />
          </Form.Item>
          <Form.Item name="group_id" label="所属分组">
            <Select allowClear options={groupOptions} placeholder="可选" />
          </Form.Item>
          <Form.Item name="description" label="项目描述">
            <Input.TextArea maxLength={500} placeholder="可选" rows={3} />
          </Form.Item>
        </Form>
      </Modal>

      <Modal
        title="项目分组"
        open={isGroupManagerOpen}
        onCancel={() => setIsGroupManagerOpen(false)}
        footer={null}
        width={640}
      >
        <div className="group-manager-toolbar">
          <Text type="secondary">删除分组不会删除项目，项目会移至未分组。</Text>
          <Button type="primary" icon={<PlusOutlined />} onClick={() => openGroupEditor()}>
            新建分组
          </Button>
        </div>
        <div className="group-list">
          {groups.length > 0 ? groups.map((group) => (
            <div className="group-list-item" key={group.group_id}>
              <FolderOpenOutlined className="group-list-icon" />
              <div className="group-list-content">
                <Space>
                  <Text strong>{group.name}</Text>
                  <Badge count={group.project_count} showZero color="#1677ff" />
                </Space>
                <Text type="secondary">{group.description || '暂无描述'}</Text>
              </div>
              <Space size={4}>
                <Tooltip title="编辑分组">
                  <Button
                    type="text"
                    icon={<EditOutlined />}
                    aria-label={`编辑 ${group.name}`}
                    onClick={() => openGroupEditor(group)}
                  />
                </Tooltip>
                <Popconfirm
                  title="删除该分组？"
                  description="组内项目会移至未分组，项目数据不会删除。"
                  okText="删除"
                  cancelText="取消"
                  onConfirm={() => void removeGroup(group)}
                >
                  <Tooltip title="删除分组">
                    <Button danger type="text" icon={<DeleteOutlined />} aria-label={`删除 ${group.name}`} />
                  </Tooltip>
                </Popconfirm>
              </Space>
            </div>
          )) : <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="暂无分组" />}
        </div>
      </Modal>

      <Modal
        title={editingGroup ? '编辑分组' : '新建分组'}
        open={isGroupEditorOpen}
        onOk={() => void submitGroup()}
        onCancel={() => setIsGroupEditorOpen(false)}
        confirmLoading={submitting}
        width={500}
        forceRender
      >
        <Form form={groupForm} layout="vertical">
          <Form.Item name="name" label="分组名称" rules={[{ required: true, whitespace: true, message: '请输入分组名称' }]}>
            <Input autoComplete="off" maxLength={100} />
          </Form.Item>
          <Form.Item name="description" label="分组描述">
            <Input.TextArea maxLength={500} rows={3} />
          </Form.Item>
        </Form>
      </Modal>

      <Modal
        title={`调整分组 · ${assigningProject?.name || ''}`}
        open={Boolean(assigningProject)}
        onOk={() => void submitAssignment()}
        onCancel={() => setAssigningProject(null)}
        confirmLoading={submitting}
        width={480}
        forceRender
      >
        <Form form={assignmentForm} layout="vertical">
          <Form.Item name="group_id" label="所属分组">
            <Select allowClear options={groupOptions} placeholder="不选择则移至未分组" />
          </Form.Item>
        </Form>
      </Modal>
    </div>
  )
}
