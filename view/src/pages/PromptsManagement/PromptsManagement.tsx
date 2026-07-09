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
  Slider,
  message,
  Collapse,
} from 'antd'
import type { ColumnsType } from 'antd/es/table'
import {
  FileTextOutlined,
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
  CodeOutlined,
  CopyOutlined,
  FileMarkdownOutlined,
  SaveOutlined,
} from '@ant-design/icons'
import XMarkdown from '@ant-design/x-markdown'
import {
  listPrompts,
  getPromptDetail,
  createPrompt,
  updatePrompt,
  deletePrompt,
  submitPromptReview,
  listPendingPrompts,
  reviewPrompt,
  archivePrompt,
  listPromptCategories,
  listPromptTags,
  createPromptCategory,
  deletePromptCategory,
  createPromptTag,
  deletePromptTag,
  promptStatusMeta,
  PRESET_CATEGORIES,
  type Prompt,
  type PromptStatus,
  type PromptCategory,
  type PromptTag,
  type PromptListParams,
} from '../../services/promptService'
import { getCurrentUser, type CurrentUser } from '../../services/authService'
import './PromptsManagement.css'

const { Title, Paragraph, Text } = Typography
const { TextArea } = Input

export default function PromptsManagement() {
  const [currentUser, setCurrentUser] = useState<CurrentUser | null>(null)
  const isAdmin = currentUser?.is_admin ?? false

  const [prompts, setPrompts] = useState<Prompt[]>([])
  const [total, setTotal] = useState(0)
  const [loading, setLoading] = useState(true)
  const [page, setPage] = useState(1)
  const [pageSize, setPageSize] = useState(20)

  const [search, setSearch] = useState('')
  const [categoryFilter, setCategoryFilter] = useState<string | undefined>()
  const [tagFilter, setTagFilter] = useState<string | undefined>()
  const [statusFilter, setStatusFilter] = useState<PromptStatus | undefined>()

  const [categories, setCategories] = useState<PromptCategory[]>([])
  const [tags, setTags] = useState<PromptTag[]>([])

  const [treePrompts, setTreePrompts] = useState<Prompt[]>([])
  const [treeLoading, setTreeLoading] = useState(false)
  const [treeSelectedKeys, setTreeSelectedKeys] = useState<Key[]>([])
  const [selectedTreePrompt, setSelectedTreePrompt] = useState<Prompt | null>(null)
  const [editorLoading, setEditorLoading] = useState(false)
  const [editorSaving, setEditorSaving] = useState(false)
  const [previewOpen, setPreviewOpen] = useState(false)
  const [treeForm] = Form.useForm()
  const promptEditorContent = Form.useWatch('content', treeForm) || ''

  const [detailOpen, setDetailOpen] = useState(false)
  const [detailLoading, setDetailLoading] = useState(false)
  const [detail, setDetail] = useState<Prompt | null>(null)

  const [formOpen, setFormOpen] = useState(false)
  const [editing, setEditing] = useState<Prompt | null>(null)
  const [submitting, setSubmitting] = useState(false)
  const [form] = Form.useForm()

  const [pendingPrompts, setPendingPrompts] = useState<Prompt[]>([])
  const [pendingLoading, setPendingLoading] = useState(false)
  const [reviewModalOpen, setReviewModalOpen] = useState(false)
  const [reviewTarget, setReviewTarget] = useState<Prompt | null>(null)
  const [reviewForm] = Form.useForm()

  const [categoryModalOpen, setCategoryModalOpen] = useState(false)
  const [tagModalOpen, setTagModalOpen] = useState(false)
  const [categoryForm] = Form.useForm()
  const [tagForm] = Form.useForm()

  const [activeTab, setActiveTab] = useState('tree')

  useEffect(() => {
    const cached = localStorage.getItem('userInfo')
    if (cached) setCurrentUser(JSON.parse(cached))
    getCurrentUser().then(setCurrentUser).catch(() => {})
  }, [])

  const loadPrompts = useCallback(
    async (notify = false) => {
      setLoading(true)
      try {
        const params: PromptListParams = {
          page,
          page_size: pageSize,
          search: search || undefined,
          category: categoryFilter,
          tag: tagFilter,
          status: statusFilter,
          sort_by: 'updated_at',
          sort_order: 'desc',
        }
        const res = await listPrompts(params)
        setPrompts(res.items)
        setTotal(res.total)
        if (notify) message.success('提示词列表已刷新')
      } catch {
        message.error('加载提示词失败')
      } finally {
        setLoading(false)
      }
    },
    [page, pageSize, search, categoryFilter, tagFilter, statusFilter]
  )

  const loadMeta = useCallback(async () => {
    try {
      const [cats, tgs] = await Promise.all([listPromptCategories(), listPromptTags()])
      setCategories(cats)
      setTags(tgs)
    } catch {
      // 静默
    }
  }, [])

  const loadTreePrompts = useCallback(async (notify = false) => {
    setTreeLoading(true)
    try {
      const items: Prompt[] = []
      let currentPage = 1
      let pages = 1
      do {
        const res = await listPrompts({
          page: currentPage,
          page_size: 100,
          sort_by: 'category',
          sort_order: 'asc',
        })
        items.push(...res.items)
        pages = res.pages || 1
        currentPage += 1
      } while (currentPage <= pages)
      setTreePrompts(items)
      if (notify) message.success('提示词树已刷新')
    } catch {
      message.error('加载提示词树失败')
    } finally {
      setTreeLoading(false)
    }
  }, [])

  const loadPending = useCallback(async () => {
    if (!isAdmin) return
    setPendingLoading(true)
    try {
      const res = await listPendingPrompts()
      setPendingPrompts(res)
    } catch {
      // 静默
    } finally {
      setPendingLoading(false)
    }
  }, [isAdmin])

  useEffect(() => {
    loadPrompts()
  }, [loadPrompts])

  useEffect(() => {
    loadMeta()
  }, [loadMeta])

  useEffect(() => {
    if (isAdmin && activeTab === 'review') loadPending()
  }, [isAdmin, activeTab, loadPending])

  useEffect(() => {
    if (activeTab === 'tree') {
      loadTreePrompts()
    }
  }, [activeTab, loadTreePrompts])

  const stats = useMemo(() => {
    return {
      total,
      approved: prompts.filter((p) => p.status === 'approved').length,
      pending: prompts.filter((p) => p.status === 'pending_review').length,
      categories: new Set(prompts.map((p) => p.category)).size,
    }
  }, [prompts, total])

  // ============ 操作 ============
  const openDetail = async (prompt: Prompt) => {
    setDetailOpen(true)
    setDetail(null)
    setDetailLoading(true)
    try {
      const d = await getPromptDetail(prompt.prompt_id)
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
      tags: [],
      variables: [],
      temperature: 0.7,
      max_tokens: 4096,
      model_hint: 'qwen3.7-plus',
    })
    setFormOpen(true)
  }

  const openEdit = (prompt: Prompt) => {
    setEditing(prompt)
    form.setFieldsValue({
      slug: prompt.slug,
      name: prompt.name,
      category: prompt.category,
      description: prompt.description,
      content: prompt.content || '',
      system_prompt: prompt.system_prompt || '',
      user_prompt_template: prompt.user_prompt_template || '',
      variables: prompt.variables || [],
      tags: prompt.tags,
      model_hint: prompt.model_hint || '',
      temperature: prompt.temperature ?? 0.7,
      max_tokens: prompt.max_tokens ?? 4096,
    })
    setFormOpen(true)
  }

  const buildPromptPayload = (values: Record<string, unknown>): Record<string, unknown> & { slug?: string } => {
    const content = String(values.content || '')
    const systemPrompt = String(values.system_prompt || content)
    return {
      ...values,
      content,
      system_prompt: systemPrompt,
      user_prompt_template: String(values.user_prompt_template || ''),
      variables: Array.isArray(values.variables) ? values.variables : [],
      tags: Array.isArray(values.tags) ? values.tags : [],
    }
  }

  const setTreeEditorValues = (prompt: Prompt | null) => {
    if (!prompt) {
      treeForm.resetFields()
      treeForm.setFieldsValue({
        slug: '',
        name: '',
        category: categories[0]?.slug || PRESET_CATEGORIES[0]?.value,
        description: '',
        content: '',
        system_prompt: '',
        user_prompt_template: '',
        variables: [],
        tags: [],
        model_hint: 'qwen3.7-plus',
        temperature: 0.7,
        max_tokens: 4096,
      })
      return
    }

    treeForm.setFieldsValue({
      slug: prompt.slug,
      name: prompt.name,
      category: prompt.category,
      description: prompt.description,
      content: prompt.content || prompt.system_prompt || '',
      system_prompt: prompt.system_prompt || '',
      user_prompt_template: prompt.user_prompt_template || '',
      variables: prompt.variables || [],
      tags: prompt.tags || [],
      model_hint: prompt.model_hint || 'qwen3.7-plus',
      temperature: prompt.temperature ?? 0.7,
      max_tokens: prompt.max_tokens ?? 4096,
    })
  }

  const startTreeCreate = () => {
    setSelectedTreePrompt(null)
    setTreeSelectedKeys([])
    setTreeEditorValues(null)
  }

  const selectTreePrompt = async (key: string) => {
    if (!key.startsWith('prompt:')) return
    const promptId = key.slice('prompt:'.length)
    setTreeSelectedKeys([key])
    setEditorLoading(true)
    try {
      const detailPrompt = await getPromptDetail(promptId)
      setSelectedTreePrompt(detailPrompt)
      setTreeEditorValues(detailPrompt)
    } catch {
      message.error('加载提示词详情失败')
    } finally {
      setEditorLoading(false)
    }
  }

  const handleTreeSave = async () => {
    try {
      const values = await treeForm.validateFields()
      setEditorSaving(true)
      const payload = buildPromptPayload(values)
      let saved: Prompt
      if (selectedTreePrompt) {
        const { slug: _slug, ...updatePayload } = payload
        saved = await updatePrompt(selectedTreePrompt.prompt_id, updatePayload as Parameters<typeof updatePrompt>[1])
        message.success('提示词已更新')
      } else {
        saved = await createPrompt(payload as unknown as Parameters<typeof createPrompt>[0])
        message.success(isAdmin ? '提示词已创建（自动通过）' : '提示词已创建（待审核）')
      }
      setSelectedTreePrompt(saved)
      setTreeSelectedKeys([`prompt:${saved.prompt_id}`])
      await Promise.all([loadPrompts(), loadTreePrompts(), loadMeta()])
    } catch (e: unknown) {
      if (e && typeof e === 'object' && 'errorFields' in e) return
      message.error('保存失败')
    } finally {
      setEditorSaving(false)
    }
  }

  const handleTreeDelete = async () => {
    if (!selectedTreePrompt) return
    try {
      await deletePrompt(selectedTreePrompt.prompt_id)
      message.success('已删除')
      setSelectedTreePrompt(null)
      setTreeSelectedKeys([])
      setTreeEditorValues(null)
      await Promise.all([loadPrompts(), loadTreePrompts()])
    } catch {
      message.error('删除失败')
    }
  }

  useEffect(() => {
    if (activeTab === 'tree' && !selectedTreePrompt && !treeForm.getFieldValue('slug')) {
      setTreeEditorValues(null)
    }
  }, [activeTab, categories, selectedTreePrompt, treeForm])

  const handleSubmit = async () => {
    try {
      const values = await form.validateFields()
      setSubmitting(true)
      const payload = buildPromptPayload(values)
      if (editing) {
        const { slug: _slug, ...updatePayload } = payload
        await updatePrompt(editing.prompt_id, updatePayload as Parameters<typeof updatePrompt>[1])
        message.success('提示词已更新')
      } else {
        await createPrompt(payload as unknown as Parameters<typeof createPrompt>[0])
        message.success(isAdmin ? '提示词已创建（自动通过）' : '提示词已创建（待审核）')
      }
      setFormOpen(false)
      await Promise.all([loadPrompts(), loadTreePrompts(), loadMeta()])
    } catch (e: unknown) {
      if (e && typeof e === 'object' && 'errorFields' in e) return
      message.error('操作失败')
    } finally {
      setSubmitting(false)
    }
  }

  const handleDelete = async (id: string) => {
    try {
      await deletePrompt(id)
      message.success('已删除')
      await Promise.all([loadPrompts(), loadTreePrompts()])
    } catch {
      message.error('删除失败')
    }
  }

  const handleSubmitReview = async (prompt: Prompt) => {
    try {
      await submitPromptReview(prompt.prompt_id)
      message.success('已提交审核')
      await Promise.all([loadPrompts(), loadTreePrompts()])
    } catch {
      message.error('提交审核失败')
    }
  }

  const handleArchive = async (prompt: Prompt) => {
    try {
      await archivePrompt(prompt.prompt_id)
      message.success('已归档')
      await Promise.all([loadPrompts(), loadTreePrompts()])
    } catch {
      message.error('归档失败')
    }
  }

  const openReviewModal = (prompt: Prompt) => {
    setReviewTarget(prompt)
    reviewForm.resetFields()
    setReviewModalOpen(true)
  }

  const handleReview = async (approved: boolean) => {
    if (!reviewTarget) return
    try {
      const { comment } = await reviewForm.validateFields()
      await reviewPrompt(reviewTarget.prompt_id, approved, comment)
      message.success(approved ? '已通过' : '已拒绝')
      setReviewModalOpen(false)
      await Promise.all([loadPending(), loadPrompts(), loadTreePrompts()])
    } catch {
      message.error('审核操作失败')
    }
  }

  const handleCreateCategory = async () => {
    try {
      const values = await categoryForm.validateFields()
      await createPromptCategory(values)
      message.success('分类已创建')
      categoryForm.resetFields()
      await Promise.all([loadMeta(), loadTreePrompts()])
    } catch {
      message.error('创建分类失败')
    }
  }

  const handleDeleteCategory = async (id: string) => {
    try {
      await deletePromptCategory(id)
      message.success('分类已删除')
      await Promise.all([loadMeta(), loadTreePrompts()])
    } catch {
      message.error('删除分类失败')
    }
  }

  const handleCreateTag = async () => {
    try {
      const values = await tagForm.validateFields()
      await createPromptTag(values)
      message.success('标签已创建')
      tagForm.resetFields()
      loadMeta()
    } catch {
      message.error('创建标签失败')
    }
  }

  const handleDeleteTag = async (id: string) => {
    try {
      await deletePromptTag(id)
      message.success('标签已删除')
      loadMeta()
    } catch {
      message.error('删除标签失败')
    }
  }

  const copyToClipboard = (text: string) => {
    navigator.clipboard.writeText(text).then(() => message.success('已复制'))
  }

  const resetFilters = () => {
    setSearch('')
    setCategoryFilter(undefined)
    setTagFilter(undefined)
    setStatusFilter(undefined)
    setPage(1)
  }

  const hasFilter = !!search || !!categoryFilter || !!tagFilter || !!statusFilter

  const categoryOptions = useMemo(() => {
    if (categories.length > 0) return categories.map((c) => ({ value: c.slug, label: c.name }))
    return PRESET_CATEGORIES
  }, [categories])

  const tagOptions = useMemo(() => tags.map((t) => ({ value: t.name, label: t.name })), [tags])

  type PromptTreeNode = {
    title: ReactNode
    key: string
    children?: PromptTreeNode[]
    selectable?: boolean
    isLeaf?: boolean
  }

  const promptTreeData = useMemo<PromptTreeNode[]>(() => {
    const nodesById = new Map<string, PromptTreeNode>()
    const roots: PromptTreeNode[] = []

    categories.forEach((category) => {
      nodesById.set(category.category_id, {
        key: `cat:${category.category_id}`,
        title: (
          <span className="library-tree-category">
            <FolderOutlined />
            <span>{category.name || category.slug}</span>
          </span>
        ),
        selectable: false,
        children: [],
      })
    })

    categories.forEach((category) => {
      const node = nodesById.get(category.category_id)
      if (!node) return
      const parent = category.parent_id ? nodesById.get(category.parent_id) : null
      if (parent) {
        parent.children = parent.children || []
        parent.children.push(node)
      } else {
        roots.push(node)
      }
    })

    const categoryBySlug = new Map(categories.map((category) => [category.slug, category]))
    const uncategorized: PromptTreeNode[] = []
    treePrompts.forEach((prompt) => {
      const promptNode: PromptTreeNode = {
        key: `prompt:${prompt.prompt_id}`,
        title: (
          <span className="library-tree-leaf">
            <FileMarkdownOutlined />
            <span>{prompt.name}</span>
            <Tag color={promptStatusMeta(prompt.status).color} bordered={false}>
              {promptStatusMeta(prompt.status).label}
            </Tag>
          </span>
        ),
        isLeaf: true,
      }
      const category = categoryBySlug.get(prompt.category)
      const target = category ? nodesById.get(category.category_id) : null
      if (target) {
        target.children = target.children || []
        target.children.push(promptNode)
      } else {
        uncategorized.push(promptNode)
      }
    })

    if (uncategorized.length > 0) {
      roots.push({
        key: 'cat:uncategorized',
        title: (
          <span className="library-tree-category">
            <FolderOutlined />
            <span>未分类</span>
          </span>
        ),
        selectable: false,
        children: uncategorized,
      })
    }

    return roots
  }, [categories, treePrompts])

  const canEditTreePrompt =
    !selectedTreePrompt || isAdmin || selectedTreePrompt.created_by === currentUser?.username

  // ============ 表格列 ============
  const columns: ColumnsType<Prompt> = [
    {
      title: '名称',
      dataIndex: 'name',
      key: 'name',
      width: 200,
      render: (name: string, record) => (
        <Space>
          <span className="prompt-table-name">{name}</span>
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
      render: (cat: string) => {
        const found = PRESET_CATEGORIES.find((c) => c.value === cat)
        return <Tag bordered={false}>{found ? found.label : cat}</Tag>
      },
    },
    {
      title: '状态',
      dataIndex: 'status',
      key: 'status',
      width: 100,
      render: (status: PromptStatus) => {
        const meta = promptStatusMeta(status)
        return <Tag color={meta.color}>{meta.label}</Tag>
      },
    },
    {
      title: '模型提示',
      dataIndex: 'model_hint',
      key: 'model_hint',
      width: 140,
      render: (v?: string) => v ? <Tag bordered={false} color="blue">{v}</Tag> : '-',
    },
    {
      title: '标签',
      dataIndex: 'tags',
      key: 'tags',
      width: 180,
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
      render: (_: unknown, record: Prompt) => (
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
            <Popconfirm title="确认归档？" onConfirm={() => handleArchive(record)}>
              <Tooltip title="归档">
                <Button type="text" size="small" icon={<InboxOutlined />} />
              </Tooltip>
            </Popconfirm>
          )}
          {isAdmin && (
            <Popconfirm title="确认删除？" description="删除后不可恢复" okButtonProps={{ danger: true }} onConfirm={() => handleDelete(record.prompt_id)}>
              <Tooltip title="删除">
                <Button type="text" size="small" danger icon={<DeleteOutlined />} />
              </Tooltip>
            </Popconfirm>
          )}
        </Space>
      ),
    },
  ]

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
            <AppstoreOutlined /> 提示词列表
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
              {pendingPrompts.length > 0 && <Badge count={pendingPrompts.length} size="small" style={{ marginLeft: 4 }} />}
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
  }, [isAdmin, pendingPrompts.length])

  return (
    <div className="prompts-mgmt page-container fade-in">
      <div className={`page-header prompts-header slide-up ${activeTab === 'tree' ? 'prompts-header-compact' : ''}`}>
        <div>
          <Title level={2} className="page-title">
            <FileTextOutlined /> Prompts 提示词库
          </Title>
          {activeTab !== 'tree' && (
            <Paragraph className="page-description">
              统一管理提示词模板，支持分类、标签与审核流，供 AI 助手和工作流调用
            </Paragraph>
          )}
        </div>
        <Space wrap>
          <Button
            icon={<ReloadOutlined />}
            onClick={() => {
              if (activeTab === 'tree') {
                loadMeta()
                loadTreePrompts(true)
              } else {
                loadPrompts(true)
              }
            }}
          >
            刷新
          </Button>
          <Button type="primary" icon={<PlusOutlined />} onClick={openCreate}>
            新建 Prompt
          </Button>
        </Space>
      </div>

      {/* 统计 */}
      {activeTab !== 'tree' && (
        <Row gutter={[16, 16]} className="prompts-stats">
          <Col xs={12} md={6} className="slide-up stagger-1">
            <Card className="glass-card hover-float stat-mini-card">
              <Statistic title="提示词总数" value={stats.total} prefix={<FileTextOutlined />} valueStyle={{ color: 'var(--color-info)', fontWeight: 700 }} />
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

      <Card className={`glass-card slide-up stagger-2 ${activeTab === 'tree' ? 'prompts-tree-card' : ''}`}>
        <Tabs activeKey={activeTab} onChange={setActiveTab} items={tabItems} />

        {/* ===== Tab: 树形编辑 ===== */}
        {activeTab === 'tree' && (
          <div className="library-workbench">
            <aside className="library-tree-pane">
              <div className="library-pane-header">
                <Text strong>提示词结构</Text>
                <Space size={4}>
                  <Tooltip title="刷新">
                    <Button size="small" type="text" icon={<ReloadOutlined />} onClick={() => loadTreePrompts(true)} />
                  </Tooltip>
                  <Tooltip title="新建 Prompt">
                    <Button size="small" type="text" icon={<PlusOutlined />} onClick={startTreeCreate} />
                  </Tooltip>
                </Space>
              </div>
              <Spin spinning={treeLoading}>
                {promptTreeData.length > 0 ? (
                  <Tree
                    key={`prompts-tree-${categories.length}-${treePrompts.length}`}
                    showLine
                    blockNode
                    defaultExpandAll
                    selectedKeys={treeSelectedKeys}
                    treeData={promptTreeData}
                    onSelect={(keys) => selectTreePrompt(String(keys[0] || ''))}
                  />
                ) : (
                  <Empty description="暂无提示词" image={Empty.PRESENTED_IMAGE_SIMPLE} />
                )}
              </Spin>
            </aside>

            <section className="library-editor-pane">
              <div className="library-editor-header">
                <div>
                  <Text strong>{selectedTreePrompt ? selectedTreePrompt.name : '新建 Prompt'}</Text>
                  <div className="library-editor-subtitle">
                    {selectedTreePrompt ? selectedTreePrompt.slug : '保存后进入数据库并刷新运行时提示词库'}
                  </div>
                </div>
                <Space wrap>
                  {selectedTreePrompt && isAdmin && (
                    <Popconfirm title="确认删除此 Prompt？" onConfirm={handleTreeDelete}>
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
                    disabled={!canEditTreePrompt}
                    onClick={handleTreeSave}
                  >
                    保存
                  </Button>
                </Space>
              </div>

              <Spin spinning={editorLoading}>
                <Form form={treeForm} layout="vertical" disabled={!canEditTreePrompt}>
                  <Form.Item
                    name="content"
                    label={
                      <div className="markdown-field-header">
                        <span>Markdown</span>
                        <Button
                          size="small"
                          icon={<EyeOutlined />}
                          disabled={!promptEditorContent}
                          onClick={() => setPreviewOpen(true)}
                        >
                          预览
                        </Button>
                      </div>
                    }
                    rules={[{ required: true, message: '请输入 Prompt 内容' }]}
                  >
                    <TextArea rows={28} className="markdown-source prompt-markdown-source" placeholder="Prompt 主体内容" />
                  </Form.Item>

                  <Collapse
                    className="prompt-editor-collapse"
                    defaultActiveKey={[]}
                    items={[
                      {
                        key: 'meta',
                        label: '基础信息 / 模型参数 / 模板 / 标签',
                        children: (
                          <>
                            <Row gutter={16}>
                              <Col xs={24} lg={8}>
                                <Form.Item name="name" label="名称" rules={[{ required: true, message: '请输入名称' }]}>
                                  <Input placeholder="Prompt 名称" />
                                </Form.Item>
                              </Col>
                              <Col xs={24} lg={8}>
                                <Form.Item name="slug" label="Slug" rules={[{ required: true, message: '请输入 slug' }]}>
                                  <Input disabled={!!selectedTreePrompt} placeholder="如 web_tagging/web_tagging" />
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
                                <Form.Item name="model_hint" label="推荐模型">
                                  <Input placeholder="qwen3.7-plus" />
                                </Form.Item>
                              </Col>
                              <Col xs={24} lg={8}>
                                <Form.Item name="max_tokens" label="Max Tokens">
                                  <InputNumber min={1} max={128000} style={{ width: '100%' }} />
                                </Form.Item>
                              </Col>
                              <Col xs={24} lg={8}>
                                <Form.Item name="temperature" label="Temperature">
                                  <Slider min={0} max={2} step={0.1} />
                                </Form.Item>
                              </Col>
                            </Row>

                            <Form.Item name="description" label="描述" rules={[{ required: true, message: '请输入描述' }]}>
                              <TextArea rows={2} placeholder="描述该 Prompt 的用途" />
                            </Form.Item>

                            <Row gutter={16}>
                              <Col xs={24} lg={12}>
                                <Form.Item name="variables" label="变量">
                                  <Select mode="tags" placeholder="变量名" tokenSeparators={[',']} />
                                </Form.Item>
                              </Col>
                              <Col xs={24} lg={12}>
                                <Form.Item name="tags" label="标签">
                                  <Select mode="tags" placeholder="输入标签后回车" tokenSeparators={[',']} options={tagOptions} />
                                </Form.Item>
                              </Col>
                            </Row>

                            <Row gutter={16}>
                              <Col xs={24} lg={12}>
                                <Form.Item name="system_prompt" label={<Space><CodeOutlined /> System Prompt</Space>}>
                                  <TextArea rows={6} className="markdown-source compact" placeholder="为空时使用主 Markdown" />
                                </Form.Item>
                              </Col>
                              <Col xs={24} lg={12}>
                                <Form.Item name="user_prompt_template" label={<Space><CodeOutlined /> User Template</Space>}>
                                  <TextArea rows={6} className="markdown-source compact" placeholder="可选的用户模板" />
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

        {/* ===== Tab: 提示词列表 ===== */}
        {activeTab === 'list' && (
          <>
            <div className="prompts-toolbar">
              <Input
                allowClear
                prefix={<SearchOutlined />}
                placeholder="搜索名称 / 描述 / 标签"
                value={search}
                onChange={(e) => {
                  setSearch(e.target.value)
                  setPage(1)
                }}
                className="prompts-search"
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
                className="prompts-filter"
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
                className="prompts-filter"
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
                className="prompts-filter"
              />
              {hasFilter && (
                <Button type="link" onClick={resetFilters}>
                  重置
                </Button>
              )}
            </div>

            <Table<Prompt>
              columns={columns}
              dataSource={prompts}
              rowKey="prompt_id"
              loading={loading}
              pagination={false}
              scroll={{ x: 1200 }}
              locale={{ emptyText: <Empty description={hasFilter ? '没有匹配的提示词' : '暂无提示词'} /> }}
            />

            <div className="prompts-pagination">
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

        {/* ===== Tab: 待审核 ===== */}
        {activeTab === 'review' && isAdmin && (
          <div className="review-tab">
            {pendingLoading ? (
              <Spin style={{ display: 'block', padding: 48 }} />
            ) : pendingPrompts.length === 0 ? (
              <Empty description="暂无待审核提示词" style={{ padding: 48 }} />
            ) : (
              <Table<Prompt>
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
                    render: (_: unknown, record: Prompt) => (
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
                dataSource={pendingPrompts}
                rowKey="prompt_id"
                pagination={false}
              />
            )}
          </div>
        )}

        {/* ===== Tab: 分类管理 ===== */}
        {activeTab === 'categories' && isAdmin && (
          <div className="meta-tab">
            <div className="meta-tab-header">
              <Button type="primary" size="small" icon={<PlusOutlined />} onClick={() => setCategoryModalOpen(true)}>
                新建分类
              </Button>
            </div>
            <Table<PromptCategory>
              columns={[
                { title: '名称', dataIndex: 'name', key: 'name', width: 160 },
                { title: 'Slug', dataIndex: 'slug', key: 'slug', width: 160 },
                { title: '描述', dataIndex: 'description', key: 'description' },
                { title: '排序', dataIndex: 'sort_order', key: 'sort_order', width: 80, align: 'center' },
                {
                  title: '操作',
                  key: 'actions',
                  width: 100,
                  render: (_: unknown, record: PromptCategory) => (
                    <Popconfirm title="确认删除此分类？" onConfirm={() => handleDeleteCategory(record.category_id)}>
                      <Button type="text" size="small" danger icon={<DeleteOutlined />} />
                    </Popconfirm>
                  ),
                },
              ]}
              dataSource={categories}
              rowKey="category_id"
              pagination={false}
              locale={{ emptyText: <Empty description="暂无分类" /> }}
            />
          </div>
        )}

        {/* ===== Tab: 标签管理 ===== */}
        {activeTab === 'tags' && isAdmin && (
          <div className="meta-tab">
            <div className="meta-tab-header">
              <Button type="primary" size="small" icon={<PlusOutlined />} onClick={() => setTagModalOpen(true)}>
                新建标签
              </Button>
            </div>
            <Table<PromptTag>
              columns={[
                {
                  title: '标签',
                  dataIndex: 'name',
                  key: 'name',
                  width: 160,
                  render: (name: string, record: PromptTag) => (
                    <Tag color={record.color || undefined}>{name}</Tag>
                  ),
                },
                { title: '颜色', dataIndex: 'color', key: 'color', width: 100 },
                { title: '描述', dataIndex: 'description', key: 'description' },
                {
                  title: '操作',
                  key: 'actions',
                  width: 100,
                  render: (_: unknown, record: PromptTag) => (
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
        title={`Markdown 预览 · ${selectedTreePrompt?.name || '新建 Prompt'}`}
        open={previewOpen}
        onCancel={() => setPreviewOpen(false)}
        footer={null}
        width={900}
        style={{ maxWidth: '95vw' }}
        destroyOnClose
      >
        <div className="prompt-preview-modal">
          {promptEditorContent ? <XMarkdown content={promptEditorContent} /> : <Empty description="暂无内容" />}
        </div>
      </Modal>

      {/* ===== 详情抽屉 ===== */}
      <Drawer
        title={detail?.name ?? '提示词详情'}
        open={detailOpen}
        onClose={() => setDetailOpen(false)}
        width={700}
        rootClassName="prompt-drawer"
        destroyOnClose
      >
        {detailLoading || !detail ? (
          <Spin>
            <div style={{ minHeight: 240 }} />
          </Spin>
        ) : (
          <div className="prompt-detail">
            <div className="prompt-detail-meta">
              <Tag bordered={false}>{detail.category}</Tag>
              <Tag color={promptStatusMeta(detail.status).color}>{promptStatusMeta(detail.status).label}</Tag>
              {detail.model_hint && <Tag color="blue">{detail.model_hint}</Tag>}
              <Text type="secondary" style={{ fontSize: 12 }}>
                v{detail.version}
              </Text>
              <Text type="secondary" className="prompt-detail-id">
                ID: {detail.prompt_id}
              </Text>
            </div>

            <div className="prompt-detail-section">
              <div className="prompt-detail-label">描述</div>
              <Paragraph>{detail.description}</Paragraph>
            </div>

            {detail.tags?.length > 0 && (
              <div className="prompt-detail-section">
                <div className="prompt-detail-label">标签</div>
                <Space wrap>
                  {detail.tags.map((t) => (
                    <Tag key={t} bordered={false}>
                      #{t}
                    </Tag>
                  ))}
                </Space>
              </div>
            )}

            <div className="prompt-detail-section">
              <div className="prompt-detail-label">参数</div>
              <Space size={24}>
                {detail.temperature !== undefined && <Text>Temperature: {detail.temperature}</Text>}
                {detail.max_tokens !== undefined && <Text>Max Tokens: {detail.max_tokens}</Text>}
              </Space>
            </div>

            {detail.variables && detail.variables.length > 0 && (
              <div className="prompt-detail-section">
                <div className="prompt-detail-label">变量</div>
                <Space wrap>
                  {detail.variables.map((v) => (
                    <Tag key={v} color="geekblue" bordered={false}>
                      {`{${v}}`}
                    </Tag>
                  ))}
                </Space>
              </div>
            )}

            {detail.system_prompt && (
              <div className="prompt-detail-section">
                <div className="prompt-detail-label">
                  <Space>
                    System Prompt
                    <Button type="text" size="small" icon={<CopyOutlined />} onClick={() => copyToClipboard(detail.system_prompt!)} />
                  </Space>
                </div>
                <div className="prompt-code-block">
                  <pre>{detail.system_prompt}</pre>
                </div>
              </div>
            )}

            {detail.user_prompt_template && (
              <div className="prompt-detail-section">
                <div className="prompt-detail-label">
                  <Space>
                    User Prompt Template
                    <Button type="text" size="small" icon={<CopyOutlined />} onClick={() => copyToClipboard(detail.user_prompt_template!)} />
                  </Space>
                </div>
                <div className="prompt-code-block">
                  <pre>{detail.user_prompt_template}</pre>
                </div>
              </div>
            )}

            {detail.content && (
              <div className="prompt-detail-section">
                <div className="prompt-detail-label">
                  <Space>
                    完整内容
                    <Button type="text" size="small" icon={<CopyOutlined />} onClick={() => copyToClipboard(detail.content!)} />
                  </Space>
                </div>
                <div className="prompt-code-block">
                  <pre>{detail.content}</pre>
                </div>
              </div>
            )}

            <div className="prompt-detail-section">
              <div className="prompt-detail-label">创建信息</div>
              <Space direction="vertical" size={4}>
                <Text>创建者：{detail.created_by}</Text>
                <Text>更新时间：{detail.updated_at ? new Date(detail.updated_at).toLocaleString('zh-CN') : '-'}</Text>
                {detail.reviewed_by && <Text>审核者：{detail.reviewed_by}</Text>}
                {detail.review_comment && <Text>审核意见：{detail.review_comment}</Text>}
              </Space>
            </div>

            <div className="prompt-detail-actions">
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
        title={editing ? `编辑提示词 · ${editing.name}` : '新建 Prompt'}
        open={formOpen}
        onCancel={() => setFormOpen(false)}
        onOk={handleSubmit}
        confirmLoading={submitting}
        okText={editing ? '保存' : '创建'}
        cancelText="取消"
        width={780}
        style={{ maxWidth: '95vw' }}
        destroyOnClose
      >
        <Form form={form} layout="vertical" className="prompt-form">
          <Row gutter={16}>
            <Col xs={24} sm={12}>
              <Form.Item name="name" label="名称" rules={[{ required: true, message: '请输入名称' }]}>
                <Input placeholder="例如：代码审查提示词" />
              </Form.Item>
            </Col>
            <Col xs={24} sm={12}>
              <Form.Item name="slug" label="Slug" rules={[{ required: true, message: '请输入 slug' }]} tooltip="唯一标识符">
                <Input placeholder="例如：code-review-prompt" />
              </Form.Item>
            </Col>
          </Row>

          <Row gutter={16}>
            <Col xs={24} sm={8}>
              <Form.Item name="category" label="分类" rules={[{ required: true, message: '请选择分类' }]}>
                <Select placeholder="选择分类" options={categoryOptions} showSearch />
              </Form.Item>
            </Col>
            <Col xs={24} sm={8}>
              <Form.Item name="model_hint" label="推荐模型">
                <Input placeholder="例如：qwen3.7-max" />
              </Form.Item>
            </Col>
            <Col xs={24} sm={8}>
              <Form.Item name="max_tokens" label="Max Tokens">
                <InputNumber min={1} max={128000} style={{ width: '100%' }} />
              </Form.Item>
            </Col>
          </Row>

          <Form.Item name="temperature" label="Temperature">
            <Slider min={0} max={2} step={0.1} marks={{ 0: '0', 0.7: '0.7', 1: '1', 2: '2' }} />
          </Form.Item>

          <Form.Item name="description" label="描述" rules={[{ required: true, message: '请输入描述' }]}>
            <TextArea rows={2} placeholder="描述该提示词的用途" />
          </Form.Item>

          <Form.Item name="system_prompt" label={<Space><CodeOutlined /> System Prompt</Space>}>
            <TextArea rows={5} placeholder="系统提示词" style={{ fontFamily: 'monospace' }} />
          </Form.Item>

          <Form.Item name="user_prompt_template" label={<Space><CodeOutlined /> User Prompt Template</Space>}>
            <TextArea rows={5} placeholder="用户提示词模板，使用 {variable} 作为占位符" style={{ fontFamily: 'monospace' }} />
          </Form.Item>

          <Form.Item name="content" label="完整内容" rules={[{ required: true, message: '请输入内容' }]}>
            <TextArea rows={6} placeholder="提示词完整文本" style={{ fontFamily: 'monospace' }} />
          </Form.Item>

          <Row gutter={16}>
            <Col xs={24} sm={12}>
              <Form.Item name="variables" label="变量列表">
                <Select mode="tags" placeholder="输入变量名后回车" tokenSeparators={[',']} />
              </Form.Item>
            </Col>
            <Col xs={24} sm={12}>
              <Form.Item name="tags" label="标签">
                <Select mode="tags" placeholder="输入标签后回车" tokenSeparators={[',']} options={tagOptions} />
              </Form.Item>
            </Col>
          </Row>
        </Form>
      </Modal>

      {/* ===== 审核弹窗 ===== */}
      <Modal
        title={`审核提示词 · ${reviewTarget?.name}`}
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
            <Input placeholder="例如：系统提示词" />
          </Form.Item>
          <Form.Item name="slug" label="Slug" rules={[{ required: true }]}>
            <Input placeholder="例如：system-prompts" />
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
            <Input placeholder="例如：analysis" />
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
