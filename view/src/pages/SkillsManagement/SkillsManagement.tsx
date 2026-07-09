import { useState, useEffect, useMemo, useCallback, type Key, type ReactNode } from 'react'
import {
  Row,
  Col,
  Card,
  Statistic,
  Button,
  Input,
  Select,
  Tag,
  Table,
  Drawer,
  Modal,
  Form,
  InputNumber,
  Tooltip,
  Popconfirm,
  Empty,
  Spin,
  Tabs,
  Tree,
  Badge,
  Typography,
  Space,
  Pagination,
  message,
  Collapse,
} from 'antd'
import type { ColumnsType } from 'antd/es/table'
import {
  ExperimentOutlined,
  PlusOutlined,
  ReloadOutlined,
  SearchOutlined,
  EyeOutlined,
  EditOutlined,
  DeleteOutlined,
  CheckCircleOutlined,
  CloseCircleOutlined,
  ClockCircleOutlined,
  InboxOutlined,
  SendOutlined,
  AuditOutlined,
  AppstoreOutlined,
  TagsOutlined,
  FolderOutlined,
  FileMarkdownOutlined,
  SaveOutlined,
} from '@ant-design/icons'
import XMarkdown from '@ant-design/x-markdown'
import {
  listSkills,
  getSkillDetail,
  createSkill,
  updateSkill,
  deleteSkill,
  submitSkillReview,
  listPendingSkills,
  reviewSkill,
  archiveSkill,
  listSkillCategories,
  listSkillTags,
  createSkillCategory,
  deleteSkillCategory,
  createSkillTag,
  deleteSkillTag,
  statusMeta,
  type Skill,
  type SkillStatus,
  type SkillCategory,
  type SkillTag,
  type SkillListParams,
} from '../../services/skillService'
import { getCurrentUser, type CurrentUser } from '../../services/authService'
import './SkillsManagement.css'

const { Title, Paragraph, Text } = Typography
const { TextArea } = Input

export default function SkillsManagement() {
  const [currentUser, setCurrentUser] = useState<CurrentUser | null>(null)
  const isAdmin = currentUser?.is_admin ?? false

  // 列表数据
  const [skills, setSkills] = useState<Skill[]>([])
  const [total, setTotal] = useState(0)
  const [loading, setLoading] = useState(true)
  const [page, setPage] = useState(1)
  const [pageSize, setPageSize] = useState(20)

  // 筛选
  const [search, setSearch] = useState('')
  const [categoryFilter, setCategoryFilter] = useState<string | undefined>()
  const [tagFilter, setTagFilter] = useState<string | undefined>()
  const [statusFilter, setStatusFilter] = useState<SkillStatus | undefined>()

  // 元数据
  const [categories, setCategories] = useState<SkillCategory[]>([])
  const [tags, setTags] = useState<SkillTag[]>([])

  // 树形编辑工作台
  const [treeSkills, setTreeSkills] = useState<Skill[]>([])
  const [treeLoading, setTreeLoading] = useState(false)
  const [treeSelectedKeys, setTreeSelectedKeys] = useState<Key[]>([])
  const [selectedTreeSkill, setSelectedTreeSkill] = useState<Skill | null>(null)
  const [editorLoading, setEditorLoading] = useState(false)
  const [editorSaving, setEditorSaving] = useState(false)
  const [previewOpen, setPreviewOpen] = useState(false)
  const [treeForm] = Form.useForm()
  const skillEditorContent = Form.useWatch('content_raw', treeForm) || ''

  // 详情抽屉
  const [detailOpen, setDetailOpen] = useState(false)
  const [detailLoading, setDetailLoading] = useState(false)
  const [detail, setDetail] = useState<Skill | null>(null)

  // 新建/编辑弹窗
  const [formOpen, setFormOpen] = useState(false)
  const [editing, setEditing] = useState<Skill | null>(null)
  const [submitting, setSubmitting] = useState(false)
  const [form] = Form.useForm()

  // 审核（admin）
  const [pendingSkills, setPendingSkills] = useState<Skill[]>([])
  const [pendingLoading, setPendingLoading] = useState(false)
  const [reviewModalOpen, setReviewModalOpen] = useState(false)
  const [reviewTarget, setReviewTarget] = useState<Skill | null>(null)
  const [reviewForm] = Form.useForm()

  // 分类/标签管理弹窗（admin）
  const [categoryModalOpen, setCategoryModalOpen] = useState(false)
  const [tagModalOpen, setTagModalOpen] = useState(false)
  const [categoryForm] = Form.useForm()
  const [tagForm] = Form.useForm()

  // 当前 Tab
  const [activeTab, setActiveTab] = useState('tree')

  useEffect(() => {
    const cached = localStorage.getItem('userInfo')
    if (cached) setCurrentUser(JSON.parse(cached))
    getCurrentUser().then(setCurrentUser).catch(() => {})
  }, [])

  const loadSkills = useCallback(
    async (notify = false) => {
      setLoading(true)
      try {
        const params: SkillListParams = {
          page,
          page_size: pageSize,
          search: search || undefined,
          category: categoryFilter,
          tag: tagFilter,
          status: statusFilter,
          sort_by: 'updated_at',
          sort_order: 'desc',
        }
        const res = await listSkills(params)
        setSkills(res.items)
        setTotal(res.total)
        if (notify) message.success('技能列表已刷新')
      } catch {
        message.error('加载技能失败')
      } finally {
        setLoading(false)
      }
    },
    [page, pageSize, search, categoryFilter, tagFilter, statusFilter]
  )

  const loadMeta = useCallback(async () => {
    try {
      const [cats, tgs] = await Promise.all([listSkillCategories(), listSkillTags()])
      setCategories(cats)
      setTags(tgs)
    } catch {
      // 静默
    }
  }, [])

  const loadTreeSkills = useCallback(async (notify = false) => {
    setTreeLoading(true)
    try {
      const items: Skill[] = []
      let currentPage = 1
      let pages = 1
      do {
        const res = await listSkills({
          page: currentPage,
          page_size: 100,
          sort_by: 'category',
          sort_order: 'asc',
        })
        items.push(...res.items)
        pages = res.pages || 1
        currentPage += 1
      } while (currentPage <= pages)
      setTreeSkills(items)
      if (notify) message.success('技能树已刷新')
    } catch {
      message.error('加载技能树失败')
    } finally {
      setTreeLoading(false)
    }
  }, [])

  const loadPending = useCallback(async () => {
    if (!isAdmin) return
    setPendingLoading(true)
    try {
      const res = await listPendingSkills()
      setPendingSkills(res)
    } catch {
      // 静默
    } finally {
      setPendingLoading(false)
    }
  }, [isAdmin])

  useEffect(() => {
    loadSkills()
  }, [loadSkills])

  useEffect(() => {
    loadMeta()
  }, [loadMeta])

  useEffect(() => {
    if (isAdmin && activeTab === 'review') {
      loadPending()
    }
  }, [isAdmin, activeTab, loadPending])

  useEffect(() => {
    if (activeTab === 'tree') {
      loadTreeSkills()
    }
  }, [activeTab, loadTreeSkills])

  // ============ 统计 ============
  const stats = useMemo(() => {
    return {
      total,
      approved: skills.filter((s) => s.status === 'approved').length,
      pending: skills.filter((s) => s.status === 'pending_review').length,
      categories: new Set(skills.map((s) => s.category)).size,
    }
  }, [skills, total])

  // ============ 操作 ============
  const openDetail = async (skill: Skill) => {
    setDetailOpen(true)
    setDetail(null)
    setDetailLoading(true)
    try {
      const d = await getSkillDetail(skill.skill_id)
      setDetail(d)
    } catch {
      message.error('加载详情失败')
      setDetailOpen(false)
    } finally {
      setDetailLoading(false)
    }
  }

  const openCreate = () => {
    setEditing(null)
    form.resetFields()
    form.setFieldsValue({
      priority: 0,
      tags: [],
      triggers: [],
      anti_triggers: [],
      phases: ['scenario', 'script', 'objection', 'finalize'],
    })
    setFormOpen(true)
  }

  const openEdit = (skill: Skill) => {
    const phases = Array.isArray(skill.meta?.phases) ? skill.meta.phases : []
    setEditing(skill)
    form.setFieldsValue({
      slug: skill.slug,
      name: skill.name,
      category: skill.category,
      description: skill.description,
      content_raw: skill.content_raw || '',
      tags: skill.tags,
      triggers: skill.triggers || [],
      anti_triggers: skill.anti_triggers || [],
      priority: skill.priority,
      phases,
    })
    setFormOpen(true)
  }

  const buildSkillPayload = (
    values: Record<string, unknown>,
    source?: Skill | null
  ): Record<string, unknown> & { slug?: string } => {
    const phases = Array.isArray(values.phases) ? values.phases : []
    const meta = {
      ...(source?.meta || {}),
      phases,
    }
    const { phases: _phases, ...payload } = values
    return {
      ...payload,
      meta,
      tags: Array.isArray(values.tags) ? values.tags : [],
      triggers: Array.isArray(values.triggers) ? values.triggers : [],
      anti_triggers: Array.isArray(values.anti_triggers) ? values.anti_triggers : [],
    }
  }

  const setTreeEditorValues = (skill: Skill | null) => {
    if (!skill) {
      treeForm.resetFields()
      treeForm.setFieldsValue({
        slug: '',
        name: '',
        category: categories[0]?.slug,
        description: '',
        content_raw: '',
        tags: [],
        triggers: [],
        anti_triggers: [],
        priority: 0,
        phases: ['scenario', 'script', 'objection', 'finalize'],
      })
      return
    }

    treeForm.setFieldsValue({
      slug: skill.slug,
      name: skill.name,
      category: skill.category,
      description: skill.description,
      content_raw: skill.content_raw || '',
      tags: skill.tags || [],
      triggers: skill.triggers || [],
      anti_triggers: skill.anti_triggers || [],
      priority: skill.priority,
      phases: Array.isArray(skill.meta?.phases) ? skill.meta.phases : [],
    })
  }

  const startTreeCreate = () => {
    setSelectedTreeSkill(null)
    setTreeSelectedKeys([])
    setTreeEditorValues(null)
  }

  const selectTreeSkill = async (key: string) => {
    const parts = key.split(':')
    const skillId = parts[0] === 'skill' || parts[0] === 'folder' ? parts[1] : ''
    if (!skillId) return
    setTreeSelectedKeys([key])
    setEditorLoading(true)
    try {
      const detailSkill = await getSkillDetail(skillId)
      setSelectedTreeSkill(detailSkill)
      setTreeEditorValues(detailSkill)
    } catch {
      message.error('加载技能详情失败')
    } finally {
      setEditorLoading(false)
    }
  }

  const handleTreeSave = async () => {
    try {
      const values = await treeForm.validateFields()
      setEditorSaving(true)
      const payload = buildSkillPayload(values, selectedTreeSkill)
      let saved: Skill
      if (selectedTreeSkill) {
        const { slug: _slug, ...updatePayload } = payload
        saved = await updateSkill(selectedTreeSkill.skill_id, updatePayload as Parameters<typeof updateSkill>[1])
        message.success('技能已更新')
      } else {
        saved = await createSkill(payload as unknown as Parameters<typeof createSkill>[0])
        message.success(isAdmin ? '技能已创建（自动通过）' : '技能已创建（待审核）')
      }
      setSelectedTreeSkill(saved)
      setTreeSelectedKeys([`skill:${saved.skill_id}`])
      await Promise.all([loadSkills(), loadTreeSkills(), loadMeta()])
    } catch (e: unknown) {
      if (e && typeof e === 'object' && 'errorFields' in e) return
      message.error('保存失败')
    } finally {
      setEditorSaving(false)
    }
  }

  const handleTreeDelete = async () => {
    if (!selectedTreeSkill) return
    try {
      await deleteSkill(selectedTreeSkill.skill_id)
      message.success('已删除')
      setSelectedTreeSkill(null)
      setTreeSelectedKeys([])
      setTreeEditorValues(null)
      await Promise.all([loadSkills(), loadTreeSkills()])
    } catch {
      message.error('删除失败')
    }
  }

  useEffect(() => {
    if (activeTab === 'tree' && !selectedTreeSkill && !treeForm.getFieldValue('slug')) {
      setTreeEditorValues(null)
    }
  }, [activeTab, categories, selectedTreeSkill, treeForm])

  const handleSubmit = async () => {
    try {
      const values = await form.validateFields()
      setSubmitting(true)
      const payload = buildSkillPayload(values, editing)
      if (editing) {
        const { slug: _slug, ...updatePayload } = payload
        await updateSkill(editing.skill_id, updatePayload as Parameters<typeof updateSkill>[1])
        message.success('技能已更新')
      } else {
        await createSkill(payload as unknown as Parameters<typeof createSkill>[0])
        message.success(isAdmin ? '技能已创建（自动通过）' : '技能已创建（待审核）')
      }
      setFormOpen(false)
      await Promise.all([loadSkills(), loadTreeSkills(), loadMeta()])
    } catch (e: unknown) {
      if (e && typeof e === 'object' && 'errorFields' in e) return
      message.error('操作失败')
    } finally {
      setSubmitting(false)
    }
  }

  const handleDelete = async (id: string) => {
    try {
      await deleteSkill(id)
      message.success('已删除')
      await Promise.all([loadSkills(), loadTreeSkills()])
    } catch {
      message.error('删除失败')
    }
  }

  const handleSubmitReview = async (skill: Skill) => {
    try {
      await submitSkillReview(skill.skill_id)
      message.success('已提交审核')
      await Promise.all([loadSkills(), loadTreeSkills()])
    } catch {
      message.error('提交审核失败')
    }
  }

  const handleArchive = async (skill: Skill) => {
    try {
      await archiveSkill(skill.skill_id)
      message.success('已归档')
      await Promise.all([loadSkills(), loadTreeSkills()])
    } catch {
      message.error('归档失败')
    }
  }

  const openReviewModal = (skill: Skill) => {
    setReviewTarget(skill)
    reviewForm.resetFields()
    setReviewModalOpen(true)
  }

  const handleReview = async (approved: boolean) => {
    if (!reviewTarget) return
    try {
      const { comment } = await reviewForm.validateFields()
      await reviewSkill(reviewTarget.skill_id, approved, comment)
      message.success(approved ? '已通过' : '已拒绝')
      setReviewModalOpen(false)
      await Promise.all([loadPending(), loadSkills(), loadTreeSkills()])
    } catch {
      message.error('审核操作失败')
    }
  }

  // 分类/标签管理
  const handleCreateCategory = async () => {
    try {
      const values = await categoryForm.validateFields()
      await createSkillCategory(values)
      message.success('分类已创建')
      categoryForm.resetFields()
      await Promise.all([loadMeta(), loadTreeSkills()])
    } catch {
      message.error('创建分类失败')
    }
  }

  const handleDeleteCategory = async (id: string) => {
    try {
      await deleteSkillCategory(id)
      message.success('分类已删除')
      await Promise.all([loadMeta(), loadTreeSkills()])
    } catch {
      message.error('删除分类失败')
    }
  }

  const handleCreateTag = async () => {
    try {
      const values = await tagForm.validateFields()
      await createSkillTag(values)
      message.success('标签已创建')
      tagForm.resetFields()
      loadMeta()
    } catch {
      message.error('创建标签失败')
    }
  }

  const handleDeleteTag = async (id: string) => {
    try {
      await deleteSkillTag(id)
      message.success('标签已删除')
      loadMeta()
    } catch {
      message.error('删除标签失败')
    }
  }

  const resetFilters = () => {
    setSearch('')
    setCategoryFilter(undefined)
    setTagFilter(undefined)
    setStatusFilter(undefined)
    setPage(1)
  }

  const hasFilter = !!search || !!categoryFilter || !!tagFilter || !!statusFilter

  const categoryOptions = useMemo(
    () => categories.map((c) => ({ value: c.slug, label: c.name })),
    [categories]
  )

  const tagOptions = useMemo(
    () => tags.map((t) => ({ value: t.name, label: t.name })),
    [tags]
  )

  type SkillTreeNode = {
    title: ReactNode
    key: string
    children?: SkillTreeNode[]
    selectable?: boolean
    isLeaf?: boolean
  }

  const getCategoryId = (category: SkillCategory) => category.category_id || category.id || category.slug

  const phaseOptions = [
    { value: 'scenario', label: 'scenario 场景构建' },
    { value: 'script', label: 'script 话术生成' },
    { value: 'objection', label: 'objection 质疑应对' },
    { value: 'finalize', label: 'finalize 整合输出' },
  ]

  const getSkillPhases = (skill: Skill) => {
    const phases = skill.meta?.phases
    return Array.isArray(phases) ? phases.map(String).filter(Boolean) : []
  }

  const getSkillReferences = (skill: Skill) => {
    const refs = skill.meta?.reference_contents
    if (refs && typeof refs === 'object' && !Array.isArray(refs)) {
      return Object.keys(refs as Record<string, unknown>).sort()
    }
    return []
  }

  const renderSkillFolderTitle = (skill: Skill) => (
    <span className="library-tree-folder">
      <FolderOutlined />
      <span>{skill.slug}</span>
      <Text type="secondary">{skill.name}</Text>
      {getSkillPhases(skill).map((phase) => (
        <Tag key={phase} bordered={false}>
          {phase}
        </Tag>
      ))}
      <Tag color={statusMeta(skill.status).color} bordered={false}>
        {statusMeta(skill.status).label}
      </Tag>
    </span>
  )

  const renderSkillLeafTitle = (skill: Skill, label = 'SKILL.md') => (
    <span className="library-tree-leaf">
      <FileMarkdownOutlined />
      <span>{label}</span>
      <Text type="secondary">{skill.name}</Text>
    </span>
  )

  const skillTreeData = useMemo<SkillTreeNode[]>(() => {
    const sortedSkills = [...treeSkills].sort((a, b) => a.slug.localeCompare(b.slug))
    const phaseChildren = phaseOptions.map((phase) => ({
      key: `phase:${phase.value}`,
      title: (
        <span className="library-tree-category">
          <FolderOutlined />
          <span>{phase.label}</span>
        </span>
      ),
      selectable: false,
      children: sortedSkills
        .filter((skill) => getSkillPhases(skill).includes(phase.value))
        .map((skill) => ({
          key: `skill:${skill.skill_id}:phase:${phase.value}`,
          title: renderSkillLeafTitle(skill, skill.slug),
          isLeaf: true,
        })),
    }))

    const libraryChildren = sortedSkills.map((skill) => {
      const refs = getSkillReferences(skill)
      const children: SkillTreeNode[] = [
        {
          key: `skill:${skill.skill_id}:skill-md`,
          title: (
            <span className="library-tree-leaf">
              <FileMarkdownOutlined />
              <span>SKILL.md</span>
              <Tag bordered={false}>Layer 2</Tag>
            </span>
          ),
          isLeaf: true,
        },
      ]

      if (refs.length > 0) {
        children.push({
          key: `refs:${skill.skill_id}`,
          title: (
            <span className="library-tree-category">
              <FolderOutlined />
              <span>references</span>
              <Tag bordered={false}>Layer 3</Tag>
            </span>
          ),
          selectable: false,
          children: refs.map((ref) => ({
            key: `skill:${skill.skill_id}:ref:${ref}`,
            title: (
              <span className="library-tree-leaf">
                <FileMarkdownOutlined />
                <span>{ref}</span>
              </span>
            ),
            isLeaf: true,
          })),
        })
      }

      return {
        key: `folder:${skill.skill_id}`,
        title: renderSkillFolderTitle(skill),
        children,
      }
    })

    return [
      {
        key: 'progressive-disclosure',
        title: (
          <span className="library-tree-category">
            <FolderOutlined />
            <span>progressive-disclosure</span>
          </span>
        ),
        selectable: false,
        children: [
          {
            key: 'layer:1',
            title: (
              <span className="library-tree-category">
                <FolderOutlined />
                <span>Layer 1 · SkillIndex 常驻索引</span>
              </span>
            ),
            selectable: false,
            children: phaseChildren,
          },
          {
            key: 'layer:2',
            title: (
              <span className="library-tree-category">
                <FileMarkdownOutlined />
                <span>Layer 2 · SKILL.md 触发后加载</span>
              </span>
            ),
            selectable: false,
          },
          {
            key: 'layer:3',
            title: (
              <span className="library-tree-category">
                <FolderOutlined />
                <span>Layer 3 · references 二次按需加载</span>
              </span>
            ),
            selectable: false,
          },
        ],
      },
      {
        key: 'skills-library-root',
        title: (
          <span className="library-tree-category">
            <FolderOutlined />
            <span>skills/library</span>
          </span>
        ),
        selectable: false,
        children: libraryChildren,
      },
    ]
  }, [treeSkills])

  const canEditTreeSkill =
    !selectedTreeSkill || isAdmin || selectedTreeSkill.created_by === currentUser?.username

  // ============ 表格列 ============
  const columns: ColumnsType<Skill> = [
    {
      title: '名称',
      dataIndex: 'name',
      key: 'name',
      width: 200,
      render: (name: string, record) => (
        <Space>
          <span className="skill-table-name">{name}</span>
          <Text type="secondary" style={{ fontSize: 12 }}>
            {record.slug}
          </Text>
        </Space>
      ),
    },
    {
      title: '分类',
      dataIndex: 'category',
      key: 'category',
      width: 120,
      render: (cat: string) => <Tag bordered={false}>{cat}</Tag>,
    },
    {
      title: '状态',
      dataIndex: 'status',
      key: 'status',
      width: 100,
      render: (status: SkillStatus) => {
        const meta = statusMeta(status)
        return <Tag color={meta.color}>{meta.label}</Tag>
      },
    },
    {
      title: '标签',
      dataIndex: 'tags',
      key: 'tags',
      width: 200,
      render: (tagList: string[]) =>
        tagList?.slice(0, 3).map((t) => (
          <Tag key={t} bordered={false} style={{ marginBottom: 2 }}>
            {t}
          </Tag>
        )),
    },
    {
      title: '版本',
      dataIndex: 'version',
      key: 'version',
      width: 60,
      align: 'center',
      render: (v: number) => `v${v}`,
    },
    {
      title: '创建者',
      dataIndex: 'created_by',
      key: 'created_by',
      width: 100,
    },
    {
      title: '更新时间',
      dataIndex: 'updated_at',
      key: 'updated_at',
      width: 170,
      render: (t: string) => (t ? new Date(t).toLocaleString('zh-CN') : '-'),
    },
    {
      title: '操作',
      key: 'actions',
      width: 220,
      fixed: 'right',
      render: (_: unknown, record: Skill) => (
        <Space size={4}>
          <Tooltip title="查看详情">
            <Button type="text" size="small" icon={<EyeOutlined />} onClick={() => openDetail(record)} />
          </Tooltip>
          {(isAdmin || record.created_by === currentUser?.username) && (
            <Tooltip title="编辑">
              <Button type="text" size="small" icon={<EditOutlined />} onClick={() => openEdit(record)} />
            </Tooltip>
          )}
          {(record.status === 'draft' || record.status === 'rejected') && (
            <Tooltip title="提交审核">
              <Button type="text" size="small" icon={<SendOutlined />} onClick={() => handleSubmitReview(record)} />
            </Tooltip>
          )}
          {isAdmin && record.status === 'pending_review' && (
            <Tooltip title="审核">
              <Button type="text" size="small" icon={<AuditOutlined />} onClick={() => openReviewModal(record)} />
            </Tooltip>
          )}
          {isAdmin && record.status === 'approved' && (
            <Popconfirm title="确认归档此技能？" onConfirm={() => handleArchive(record)}>
              <Tooltip title="归档">
                <Button type="text" size="small" icon={<InboxOutlined />} />
              </Tooltip>
            </Popconfirm>
          )}
          {isAdmin && (
            <Popconfirm title="确认删除？" description="删除后不可恢复" okButtonProps={{ danger: true }} onConfirm={() => handleDelete(record.skill_id)}>
              <Tooltip title="删除">
                <Button type="text" size="small" danger icon={<DeleteOutlined />} />
              </Tooltip>
            </Popconfirm>
          )}
        </Space>
      ),
    },
  ]

  // ============ Tab 项 ============
  const tabItems = useMemo(() => {
    const items = [
      {
        key: 'tree',
        label: (
          <span>
            <FolderOutlined /> 树形编辑
          </span>
        ),
      },
      {
        key: 'list',
        label: (
          <span>
            <AppstoreOutlined /> 技能列表
          </span>
        ),
      },
    ]
    if (isAdmin) {
      items.push(
        {
          key: 'review',
          label: (
            <span>
              <AuditOutlined /> 待审核{' '}
              {pendingSkills.length > 0 && <Badge count={pendingSkills.length} size="small" style={{ marginLeft: 4 }} />}
            </span>
          ),
        },
        {
          key: 'categories',
          label: (
            <span>
              <FolderOutlined /> 分类管理
            </span>
          ),
        },
        {
          key: 'tags',
          label: (
            <span>
              <TagsOutlined /> 标签管理
            </span>
          ),
        }
      )
    }
    return items
  }, [isAdmin, pendingSkills.length])

  // ============ 渲染 ============
  return (
    <div className="skills-mgmt page-container fade-in">
      <div className={`page-header skills-header slide-up ${activeTab === 'tree' ? 'skills-header-compact' : ''}`}>
        <div>
          <Title level={2} className="page-title">
            <ExperimentOutlined /> Skills 技能库
          </Title>
          {activeTab !== 'tree' && (
            <Paragraph className="page-description">
              统一管理技能库，支持分类、标签与审核流，按需供 AI 助手调用
            </Paragraph>
          )}
        </div>
        <Space wrap>
          <Button
            icon={<ReloadOutlined />}
            onClick={() => {
              if (activeTab === 'tree') {
                loadMeta()
                loadTreeSkills(true)
              } else {
                loadSkills(true)
              }
            }}
          >
            刷新
          </Button>
          <Button type="primary" icon={<PlusOutlined />} onClick={openCreate}>
            新建 Skill
          </Button>
        </Space>
      </div>

      {/* 统计卡片 */}
      {activeTab !== 'tree' && (
        <Row gutter={[16, 16]} className="skills-stats">
          <Col xs={12} md={6} className="slide-up stagger-1">
            <Card className="glass-card hover-float stat-mini-card">
              <Statistic title="技能总数" value={stats.total} prefix={<ExperimentOutlined />} valueStyle={{ color: 'var(--color-info)', fontWeight: 700 }} />
            </Card>
          </Col>
          <Col xs={12} md={6} className="slide-up stagger-1">
            <Card className="glass-card hover-float stat-mini-card">
              <Statistic title="已通过" value={stats.approved} prefix={<CheckCircleOutlined />} valueStyle={{ color: 'var(--color-success)', fontWeight: 700 }} />
            </Card>
          </Col>
          <Col xs={12} md={6} className="slide-up stagger-2">
            <Card className="glass-card hover-float stat-mini-card">
              <Statistic title="审核中" value={stats.pending} prefix={<ClockCircleOutlined />} valueStyle={{ color: 'var(--color-warning)', fontWeight: 700 }} />
            </Card>
          </Col>
          <Col xs={12} md={6} className="slide-up stagger-2">
            <Card className="glass-card hover-float stat-mini-card">
              <Statistic title="分类数" value={stats.categories} prefix={<AppstoreOutlined />} valueStyle={{ fontWeight: 700 }} />
            </Card>
          </Col>
        </Row>
      )}

      {/* Tab */}
      <Card className={`glass-card slide-up stagger-2 ${activeTab === 'tree' ? 'skills-tree-card' : ''}`}>
        <Tabs activeKey={activeTab} onChange={setActiveTab} items={tabItems} />

        {/* ===== Tab: 树形编辑 ===== */}
        {activeTab === 'tree' && (
          <div className="library-workbench">
            <aside className="library-tree-pane">
              <div className="library-pane-header">
                <Text strong>技能结构</Text>
                <Space size={4}>
                  <Tooltip title="刷新">
                    <Button size="small" type="text" icon={<ReloadOutlined />} onClick={() => loadTreeSkills(true)} />
                  </Tooltip>
                  <Tooltip title="新建 Skill">
                    <Button size="small" type="text" icon={<PlusOutlined />} onClick={startTreeCreate} />
                  </Tooltip>
                </Space>
              </div>
              <Spin spinning={treeLoading}>
                {skillTreeData.length > 0 ? (
                  <Tree
                    key={`skills-tree-${categories.length}-${treeSkills.length}`}
                    showLine
                    blockNode
                    defaultExpandAll
                    selectedKeys={treeSelectedKeys}
                    treeData={skillTreeData}
                    onSelect={(keys) => selectTreeSkill(String(keys[0] || ''))}
                  />
                ) : (
                  <Empty description="暂无技能" image={Empty.PRESENTED_IMAGE_SIMPLE} />
                )}
              </Spin>
            </aside>

            <section className="library-editor-pane">
              <div className="library-editor-header">
                <div>
                  <Text strong>{selectedTreeSkill ? selectedTreeSkill.name : '新建 Skill'}</Text>
                  <div className="library-editor-subtitle">
                    {selectedTreeSkill ? selectedTreeSkill.slug : '保存后进入数据库并刷新运行时技能库'}
                  </div>
                </div>
                <Space wrap>
                  {selectedTreeSkill && isAdmin && (
                    <Popconfirm title="确认删除此 Skill？" onConfirm={handleTreeDelete}>
                      <Button danger icon={<DeleteOutlined />}>
                        删除
                      </Button>
                    </Popconfirm>
                  )}
                  <Button icon={<PlusOutlined />} onClick={startTreeCreate}>
                    新建
                  </Button>
                  <Button
                    type="primary"
                    icon={<SaveOutlined />}
                    loading={editorSaving}
                    disabled={!canEditTreeSkill}
                    onClick={handleTreeSave}
                  >
                    保存
                  </Button>
                </Space>
              </div>

              <Spin spinning={editorLoading}>
                <Form form={treeForm} layout="vertical" disabled={!canEditTreeSkill}>
                  <Form.Item
                    name="content_raw"
                    label={
                      <div className="markdown-field-header">
                        <span>Markdown</span>
                        <Button
                          size="small"
                          icon={<EyeOutlined />}
                          disabled={!skillEditorContent}
                          onClick={() => setPreviewOpen(true)}
                        >
                          预览
                        </Button>
                      </div>
                    }
                    rules={[{ required: true, message: '请输入 Skill 内容' }]}
                  >
                    <TextArea rows={28} className="markdown-source skill-markdown-source" placeholder="Skill 完整指令内容" />
                  </Form.Item>

                  <Collapse
                    className="skill-editor-collapse"
                    defaultActiveKey={[]}
                    items={[
                      {
                        key: 'meta',
                        label: '基础信息 / 渐进阶段 / 标签',
                        children: (
                          <>
                            <Row gutter={16}>
                              <Col xs={24} lg={8}>
                                <Form.Item name="name" label="名称" rules={[{ required: true, message: '请输入名称' }]}>
                                  <Input placeholder="Skill 名称" />
                                </Form.Item>
                              </Col>
                              <Col xs={24} lg={8}>
                                <Form.Item name="slug" label="Slug" rules={[{ required: true, message: '请输入 slug' }]}>
                                  <Input disabled={!!selectedTreeSkill} placeholder="唯一标识" />
                                </Form.Item>
                              </Col>
                              <Col xs={24} lg={8}>
                                <Form.Item name="category" label="分类" rules={[{ required: true, message: '请选择分类' }]}>
                                  <Select placeholder="选择分类" options={categoryOptions} showSearch />
                                </Form.Item>
                              </Col>
                            </Row>

                            <Row gutter={16}>
                              <Col xs={24} lg={8}>
                                <Form.Item name="priority" label="优先级">
                                  <InputNumber min={0} max={99} style={{ width: '100%' }} />
                                </Form.Item>
                              </Col>
                              <Col xs={24} lg={16}>
                                <Form.Item name="phases" label="渐进阶段">
                                  <Select
                                    mode="multiple"
                                    options={[
                                      { value: 'scenario', label: 'scenario' },
                                      { value: 'script', label: 'script' },
                                      { value: 'objection', label: 'objection' },
                                      { value: 'finalize', label: 'finalize' },
                                    ]}
                                  />
                                </Form.Item>
                              </Col>
                            </Row>

                            <Form.Item name="description" label="描述" rules={[{ required: true, message: '请输入描述' }]}>
                              <TextArea rows={2} placeholder="描述该 Skill 的触发场景" />
                            </Form.Item>

                            <Row gutter={16}>
                              <Col xs={24} lg={12}>
                                <Form.Item name="tags" label="标签">
                                  <Select mode="tags" placeholder="输入标签后回车" tokenSeparators={[',']} options={tagOptions} />
                                </Form.Item>
                              </Col>
                              <Col xs={24} lg={6}>
                                <Form.Item name="triggers" label="触发词">
                                  <Select mode="tags" placeholder="触发词" tokenSeparators={[',']} />
                                </Form.Item>
                              </Col>
                              <Col xs={24} lg={6}>
                                <Form.Item name="anti_triggers" label="排除词">
                                  <Select mode="tags" placeholder="排除词" tokenSeparators={[',']} />
                                </Form.Item>
                              </Col>
                            </Row>
                          </>
                        ),
                      },
                    ]}
                  />
                </Form>
              </Spin>
            </section>
          </div>
        )}

        {/* ===== Tab: 技能列表 ===== */}
        {activeTab === 'list' && (
          <>
            <div className="skills-toolbar">
              <Input
                allowClear
                prefix={<SearchOutlined />}
                placeholder="搜索名称 / 描述 / 标签"
                value={search}
                onChange={(e) => {
                  setSearch(e.target.value)
                  setPage(1)
                }}
                className="skills-search"
              />
              <Select
                allowClear
                placeholder="分类"
                value={categoryFilter}
                onChange={(v) => {
                  setCategoryFilter(v)
                  setPage(1)
                }}
                options={categoryOptions}
                className="skills-filter"
              />
              <Select
                allowClear
                placeholder="标签"
                value={tagFilter}
                onChange={(v) => {
                  setTagFilter(v)
                  setPage(1)
                }}
                options={tagOptions}
                className="skills-filter"
              />
              <Select
                allowClear
                placeholder="状态"
                value={statusFilter}
                onChange={(v) => {
                  setStatusFilter(v)
                  setPage(1)
                }}
                options={[
                  { value: 'draft', label: '草稿' },
                  { value: 'pending_review', label: '审核中' },
                  { value: 'approved', label: '已通过' },
                  { value: 'rejected', label: '已拒绝' },
                  { value: 'archived', label: '已归档' },
                ]}
                className="skills-filter"
              />
              {hasFilter && (
                <Button type="link" onClick={resetFilters}>
                  重置
                </Button>
              )}
            </div>

            <Table<Skill>
              columns={columns}
              dataSource={skills}
              rowKey="skill_id"
              loading={loading}
              pagination={false}
              scroll={{ x: 1100 }}
              locale={{ emptyText: <Empty description={hasFilter ? '没有匹配的技能' : '暂无技能'} /> }}
            />

            <div className="skills-pagination">
              <Pagination
                current={page}
                pageSize={pageSize}
                total={total}
                showSizeChanger
                showQuickJumper
                showTotal={(t) => `共 ${t} 条`}
                onChange={(p, ps) => {
                  setPage(p)
                  setPageSize(ps)
                }}
              />
            </div>
          </>
        )}

        {/* ===== Tab: 待审核（admin） ===== */}
        {activeTab === 'review' && isAdmin && (
          <div className="review-tab">
            {pendingLoading ? (
              <Spin style={{ display: 'block', padding: 48 }} />
            ) : pendingSkills.length === 0 ? (
              <Empty description="暂无待审核技能" style={{ padding: 48 }} />
            ) : (
              <Table<Skill>
                columns={[
                  { title: '名称', dataIndex: 'name', key: 'name', width: 200 },
                  { title: 'Slug', dataIndex: 'slug', key: 'slug', width: 160 },
                  { title: '分类', dataIndex: 'category', key: 'category', width: 120 },
                  { title: '提交者', dataIndex: 'created_by', key: 'created_by', width: 120 },
                  {
                    title: '提交时间',
                    dataIndex: 'updated_at',
                    key: 'updated_at',
                    width: 170,
                    render: (t: string) => new Date(t).toLocaleString('zh-CN'),
                  },
                  {
                    title: '操作',
                    key: 'actions',
                    width: 180,
                    render: (_: unknown, record: Skill) => (
                      <Space>
                        <Button size="small" icon={<EyeOutlined />} onClick={() => openDetail(record)}>
                          查看
                        </Button>
                        <Button size="small" type="primary" icon={<AuditOutlined />} onClick={() => openReviewModal(record)}>
                          审核
                        </Button>
                      </Space>
                    ),
                  },
                ]}
                dataSource={pendingSkills}
                rowKey="skill_id"
                pagination={false}
              />
            )}
          </div>
        )}

        {/* ===== Tab: 分类管理（admin） ===== */}
        {activeTab === 'categories' && isAdmin && (
          <div className="meta-tab">
            <div className="meta-tab-header">
              <Button type="primary" size="small" icon={<PlusOutlined />} onClick={() => setCategoryModalOpen(true)}>
                新建分类
              </Button>
            </div>
            <Table<SkillCategory>
              columns={[
                { title: '名称', dataIndex: 'name', key: 'name', width: 160 },
                { title: 'Slug', dataIndex: 'slug', key: 'slug', width: 160 },
                { title: '描述', dataIndex: 'description', key: 'description' },
                { title: '排序', dataIndex: 'sort_order', key: 'sort_order', width: 80, align: 'center' },
                {
                  title: '操作',
                  key: 'actions',
                  width: 100,
                  render: (_: unknown, record: SkillCategory) => (
                    <Popconfirm title="确认删除此分类？" onConfirm={() => handleDeleteCategory(getCategoryId(record))}>
                      <Button type="text" size="small" danger icon={<DeleteOutlined />} />
                    </Popconfirm>
                  ),
                },
              ]}
              dataSource={categories}
              rowKey={(record) => getCategoryId(record)}
              pagination={false}
              locale={{ emptyText: <Empty description="暂无分类" /> }}
            />
          </div>
        )}

        {/* ===== Tab: 标签管理（admin） ===== */}
        {activeTab === 'tags' && isAdmin && (
          <div className="meta-tab">
            <div className="meta-tab-header">
              <Button type="primary" size="small" icon={<PlusOutlined />} onClick={() => setTagModalOpen(true)}>
                新建标签
              </Button>
            </div>
            <Table<SkillTag>
              columns={[
                {
                  title: '标签',
                  dataIndex: 'name',
                  key: 'name',
                  width: 160,
                  render: (name: string, record: SkillTag) => (
                    <Tag color={record.color || undefined}>{name}</Tag>
                  ),
                },
                { title: '颜色', dataIndex: 'color', key: 'color', width: 100 },
                { title: '描述', dataIndex: 'description', key: 'description' },
                {
                  title: '操作',
                  key: 'actions',
                  width: 100,
                  render: (_: unknown, record: SkillTag) => (
                    <Popconfirm title="删除标签会同步清理引用，确认？" onConfirm={() => handleDeleteTag(record.tag_id)}>
                      <Button type="text" size="small" danger icon={<DeleteOutlined />} />
                    </Popconfirm>
                  ),
                },
              ]}
              dataSource={tags}
              rowKey="tag_id"
              pagination={false}
              locale={{ emptyText: <Empty description="暂无标签" /> }}
            />
          </div>
        )}
      </Card>

      <Modal
        title={`Markdown 预览 · ${selectedTreeSkill?.name || '新建 Skill'}`}
        open={previewOpen}
        onCancel={() => setPreviewOpen(false)}
        footer={null}
        width="min(1100px, 92vw)"
        destroyOnHidden
      >
        <div className="skill-preview-modal">
          {skillEditorContent ? <XMarkdown content={skillEditorContent} /> : <Empty description="暂无内容" />}
        </div>
      </Modal>

      {/* ===== 详情抽屉 ===== */}
      <Drawer
        title={detail?.name ?? '技能详情'}
        open={detailOpen}
        onClose={() => setDetailOpen(false)}
        width={640}
        rootClassName="skill-drawer"
        destroyOnClose
      >
        {detailLoading || !detail ? (
          <Spin>
            <div style={{ minHeight: 240 }} />
          </Spin>
        ) : (
          <div className="skill-detail">
            <div className="skill-detail-meta">
              <Tag bordered={false}>{detail.category}</Tag>
              <Tag color={statusMeta(detail.status).color}>{statusMeta(detail.status).label}</Tag>
              <Text type="secondary" style={{ fontSize: 12 }}>
                v{detail.version}
              </Text>
              <Text type="secondary" className="skill-detail-id">
                ID: {detail.skill_id}
              </Text>
            </div>

            <div className="skill-detail-section">
              <div className="skill-detail-label">描述</div>
              <Paragraph>{detail.description}</Paragraph>
            </div>

            {detail.tags?.length > 0 && (
              <div className="skill-detail-section">
                <div className="skill-detail-label">标签</div>
                <div className="skill-tags">
                  {detail.tags.map((t) => (
                    <span key={t} className="skill-tag-chip">#{t}</span>
                  ))}
                </div>
              </div>
            )}

            <div className="skill-detail-section">
              <div className="skill-detail-label">创建信息</div>
              <Space direction="vertical" size={4}>
                <Text>创建者：{detail.created_by}</Text>
                <Text>更新时间：{detail.updated_at ? new Date(detail.updated_at).toLocaleString('zh-CN') : '-'}</Text>
                {detail.reviewed_by && <Text>审核者：{detail.reviewed_by}</Text>}
                {detail.review_comment && <Text>审核意见：{detail.review_comment}</Text>}
              </Space>
            </div>

            {detail.content_raw && (
              <div className="skill-detail-section">
                <div className="skill-detail-label">技能内容</div>
                <div className="skill-detail-content">
                  <XMarkdown content={detail.content_raw} />
                </div>
              </div>
            )}

            <div className="skill-detail-actions">
              <Space>
                {(isAdmin || detail.created_by === currentUser?.username) && (
                  <Button
                    type="primary"
                    icon={<EditOutlined />}
                    onClick={() => {
                      setDetailOpen(false)
                      openEdit(detail)
                    }}
                  >
                    编辑
                  </Button>
                )}
                {(detail.status === 'draft' || detail.status === 'rejected') && (
                  <Button icon={<SendOutlined />} onClick={() => handleSubmitReview(detail)}>
                    提交审核
                  </Button>
                )}
              </Space>
            </div>
          </div>
        )}
      </Drawer>

      {/* ===== 新建/编辑弹窗 ===== */}
      <Modal
        title={editing ? `编辑技能 · ${editing.name}` : '新建 Skill'}
        open={formOpen}
        onCancel={() => setFormOpen(false)}
        onOk={handleSubmit}
        confirmLoading={submitting}
        okText={editing ? '保存' : '创建'}
        cancelText="取消"
        width={720}
        style={{ maxWidth: '95vw' }}
        destroyOnClose
      >
        <Form form={form} layout="vertical" className="skill-form">
          <Row gutter={16}>
            <Col xs={24} sm={12}>
              <Form.Item name="name" label="技能名称" rules={[{ required: true, message: '请输入名称' }]}>
                <Input placeholder="例如：ai-engineering" />
              </Form.Item>
            </Col>
            <Col xs={24} sm={12}>
              <Form.Item name="slug" label="Slug" rules={[{ required: true, message: '请输入 slug' }]} tooltip="唯一标识符，用于 API 引用">
                <Input placeholder="例如：ai-engineering" />
              </Form.Item>
            </Col>
          </Row>

          <Row gutter={16}>
            <Col xs={24} sm={12}>
              <Form.Item name="category" label="分类" rules={[{ required: true, message: '请选择分类' }]}>
                <Select placeholder="选择分类" options={categoryOptions} showSearch />
              </Form.Item>
            </Col>
            <Col xs={24} sm={12}>
              <Form.Item name="priority" label="优先级" tooltip="数值越小优先级越高">
                <InputNumber min={0} max={99} style={{ width: '100%' }} />
              </Form.Item>
            </Col>
          </Row>

          <Form.Item name="description" label="描述" rules={[{ required: true, message: '请输入描述' }]}>
            <TextArea rows={3} placeholder="描述该技能的适用场景与能力范围" />
          </Form.Item>

          <Form.Item name="content_raw" label="技能内容（Markdown）" rules={[{ required: true, message: '请输入内容' }]}>
            <TextArea rows={10} placeholder="技能完整内容（Markdown 格式）" style={{ fontFamily: 'monospace' }} />
          </Form.Item>

          <Form.Item name="tags" label="标签">
            <Select mode="tags" placeholder="输入标签后回车" tokenSeparators={[',']} options={tagOptions} />
          </Form.Item>

          <Form.Item name="phases" label="渐进阶段">
            <Select
              mode="multiple"
              options={[
                { value: 'scenario', label: 'scenario' },
                { value: 'script', label: 'script' },
                { value: 'objection', label: 'objection' },
                { value: 'finalize', label: 'finalize' },
              ]}
            />
          </Form.Item>

          <Row gutter={16}>
            <Col xs={24} sm={12}>
              <Form.Item name="triggers" label="触发词">
                <Select mode="tags" placeholder="触发词" tokenSeparators={[',']} />
              </Form.Item>
            </Col>
            <Col xs={24} sm={12}>
              <Form.Item name="anti_triggers" label="排除词">
                <Select mode="tags" placeholder="排除词" tokenSeparators={[',']} />
              </Form.Item>
            </Col>
          </Row>
        </Form>
      </Modal>

      {/* ===== 审核弹窗 ===== */}
      <Modal
        title={`审核技能 · ${reviewTarget?.name}`}
        open={reviewModalOpen}
        onCancel={() => setReviewModalOpen(false)}
        footer={
          <Space>
            <Button onClick={() => setReviewModalOpen(false)}>取消</Button>
            <Button danger icon={<CloseCircleOutlined />} onClick={() => handleReview(false)}>
              拒绝
            </Button>
            <Button type="primary" icon={<CheckCircleOutlined />} onClick={() => handleReview(true)}>
              通过
            </Button>
          </Space>
        }
        destroyOnClose
      >
        <Form form={reviewForm} layout="vertical">
          <Form.Item name="comment" label="审核意见">
            <TextArea rows={4} placeholder="可选：填写审核意见" />
          </Form.Item>
        </Form>
        {reviewTarget && (
          <div style={{ marginTop: 16, padding: 12, background: 'var(--accent-color)', borderRadius: 8 }}>
            <Text strong>{reviewTarget.name}</Text>
            <br />
            <Text type="secondary">{reviewTarget.description}</Text>
            <br />
            <Text type="secondary" style={{ fontSize: 12 }}>
              提交者: {reviewTarget.created_by} · 分类: {reviewTarget.category}
            </Text>
          </div>
        )}
      </Modal>

      {/* ===== 新建分类弹窗 ===== */}
      <Modal
        title="新建分类"
        open={categoryModalOpen}
        onCancel={() => setCategoryModalOpen(false)}
        onOk={handleCreateCategory}
        destroyOnClose
      >
        <Form form={categoryForm} layout="vertical">
          <Form.Item name="name" label="分类名称" rules={[{ required: true }]}>
            <Input placeholder="例如：后端 API" />
          </Form.Item>
          <Form.Item name="slug" label="Slug" rules={[{ required: true }]}>
            <Input placeholder="例如：backend-api" />
          </Form.Item>
          <Form.Item name="description" label="描述">
            <TextArea rows={2} placeholder="分类描述" />
          </Form.Item>
          <Form.Item name="sort_order" label="排序" initialValue={0}>
            <InputNumber min={0} style={{ width: '100%' }} />
          </Form.Item>
        </Form>
      </Modal>

      {/* ===== 新建标签弹窗 ===== */}
      <Modal
        title="新建标签"
        open={tagModalOpen}
        onCancel={() => setTagModalOpen(false)}
        onOk={handleCreateTag}
        destroyOnClose
      >
        <Form form={tagForm} layout="vertical">
          <Form.Item name="name" label="标签名" rules={[{ required: true }]}>
            <Input placeholder="例如：安全" />
          </Form.Item>
          <Form.Item name="color" label="颜色" initialValue="#1677ff">
            <Input placeholder="#ff0000" />
          </Form.Item>
          <Form.Item name="description" label="描述">
            <TextArea rows={2} placeholder="标签描述" />
          </Form.Item>
        </Form>
      </Modal>
    </div>
  )
}
