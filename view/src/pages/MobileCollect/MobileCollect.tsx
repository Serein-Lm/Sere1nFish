import { useCallback, useEffect, useState } from 'react'
import Card from 'antd/es/card'
import Table from 'antd/es/table'
import Button from 'antd/es/button'
import Space from 'antd/es/space'
import Tag from 'antd/es/tag'
import Drawer from 'antd/es/drawer'
import Form from 'antd/es/form'
import Input from 'antd/es/input'
import InputNumber from 'antd/es/input-number'
import Select from 'antd/es/select'
import Switch from 'antd/es/switch'
import Image from 'antd/es/image'
import Popconfirm from 'antd/es/popconfirm'
import Empty from 'antd/es/empty'
import Spin from 'antd/es/spin'
import Dropdown from 'antd/es/dropdown'
import message from 'antd/es/message'
import type { ColumnsType } from 'antd/es/table'
import {
  PlayCircleOutlined,
  PauseCircleOutlined,
  EditOutlined,
  DeleteOutlined,
  DatabaseOutlined,
  ClockCircleOutlined,
  PlusOutlined,
  ReloadOutlined,
  ExperimentOutlined,
} from '@ant-design/icons'

import {
  listTaskDefs,
  createTaskDef,
  updateTaskDef,
  deleteTaskDef,
  runTaskDef,
  stopTaskDef,
  dryRunTaskDef,
  listRecords,
  listSchedules,
  createSchedule,
  updateSchedule,
  deleteSchedule,
  listPresets,
  listSourceLinkStrategies,
  fetchScreenshotObjectUrl,
  type CollectTaskDef,
  type CollectTaskInput,
  type CollectRecord,
  type ScheduleDef,
  type CollectPreset,
  type DryRunResult,
  type DryRunPreviewItem,
  type SourceLinkStrategyOption,
} from '../../services/mobileCollectService'
import { getDevices, type SimpleDevice } from '../../services/mobileService'
import CollectRecordsView from '../../components/CollectRecordsView/CollectRecordsView'
import './MobileCollect.css'

const NOTIFY_OPTIONS = [
  { value: 'new', label: '仅新增' },
  { value: 'changed', label: '仅变更' },
  { value: 'both', label: '新增+变更' },
  { value: 'none', label: '不通知' },
]

const FIELD_TYPE_OPTIONS = [
  { value: 'string', label: '文本' },
  { value: 'number', label: '数字' },
  { value: 'boolean', label: '布尔' },
  { value: 'list', label: '列表' },
]

/** 鉴权截图组件：拉取 blob 转 ObjectURL 后展示。 */
function AuthImage({ url, width = 64 }: { url: string; width?: number }) {
  const [src, setSrc] = useState<string>('')
  useEffect(() => {
    let objectUrl = ''
    let alive = true
    fetchScreenshotObjectUrl(url)
      .then((u) => {
        if (alive) {
          objectUrl = u
          setSrc(u)
        } else {
          URL.revokeObjectURL(u)
        }
      })
      .catch(() => undefined)
    return () => {
      alive = false
      if (objectUrl) URL.revokeObjectURL(objectUrl)
    }
  }, [url])
  if (!src) return <Spin size="small" />
  return <Image src={src} width={width} style={{ borderRadius: 4 }} />
}

function statusTag(status?: string) {
  if (status === 'running') return <Tag color="processing">运行中</Tag>
  return <Tag color="default">空闲</Tag>
}

export default function MobileCollect() {
  const [tasks, setTasks] = useState<CollectTaskDef[]>([])
  const [loading, setLoading] = useState(false)
  const [devices, setDevices] = useState<SimpleDevice[]>([])
  const [presets, setPresets] = useState<CollectPreset[]>([])
  const [sourceLinkStrategies, setSourceLinkStrategies] = useState<SourceLinkStrategyOption[]>([
    { strategy: 'none', label: '不提取', description: '仅使用视觉模型可见的链接' },
  ])

  // 编辑抽屉
  const [editorOpen, setEditorOpen] = useState(false)
  const [editing, setEditing] = useState<CollectTaskDef | null>(null)
  const [form] = Form.useForm<CollectTaskInput>()
  const [saving, setSaving] = useState(false)

  // 记录抽屉
  const [recordsOpen, setRecordsOpen] = useState(false)
  const [recordsTask, setRecordsTask] = useState<CollectTaskDef | null>(null)
  const [records, setRecords] = useState<CollectRecord[]>([])
  const [recordsLoading, setRecordsLoading] = useState(false)
  const [onlyIncremental, setOnlyIncremental] = useState(false)

  // 调度抽屉
  const [scheduleOpen, setScheduleOpen] = useState(false)
  const [scheduleTask, setScheduleTask] = useState<CollectTaskDef | null>(null)
  const [schedules, setSchedules] = useState<ScheduleDef[]>([])
  const [scheduleForm] = Form.useForm()

  // 试跑预览
  const [dryRunOpen, setDryRunOpen] = useState(false)
  const [dryRunTask, setDryRunTask] = useState<CollectTaskDef | null>(null)
  const [dryRunning, setDryRunning] = useState(false)
  const [dryRunResult, setDryRunResult] = useState<DryRunResult | null>(null)

  const loadTasks = useCallback(async () => {
    setLoading(true)
    try {
      const res = await listTaskDefs()
      setTasks(res.items)
    } catch (e) {
      message.error(`加载采集任务失败: ${(e as Error).message}`)
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    loadTasks()
    getDevices().then((r) => setDevices(r.devices)).catch(() => undefined)
    listPresets().then((r) => setPresets(r.items)).catch(() => undefined)
    listSourceLinkStrategies().then((r) => setSourceLinkStrategies(r.items)).catch(() => undefined)
  }, [loadTasks])

  // ── 编辑 ──
  const openCreate = (preset?: CollectPreset) => {
    setEditing(null)
    const base: CollectTaskInput = {
      name: '',
      device_id: '',
      app_name: '',
      keywords: [],
      swipe_times: 3,
      swipe_interval: 1.2,
      extract_fields: [],
      dedup_key_fields: [],
      notify_on: 'new',
      search_hint: '',
      deep_collect: false,
      source_link_strategy: 'none',
      detail_max_items: 5,
      detail_max_swipes: 12,
      min_score_to_detail: 60,
      min_subject_match: 70,
      min_score_to_persist: 0,
    }
    form.setFieldsValue(preset ? { ...base, ...preset.task } : base)
    setEditorOpen(true)
  }

  const openEdit = (task: CollectTaskDef) => {
    setEditing(task)
    form.setFieldsValue({
      name: task.name,
      device_id: task.device_id,
      app_name: task.app_name,
      keywords: task.keywords || [],
      swipe_times: task.swipe_times,
      swipe_interval: task.swipe_interval,
      extract_fields: task.extract_fields || [],
      dedup_key_fields: task.dedup_key_fields || [],
      notify_on: task.notify_on,
      search_hint: task.search_hint || '',
      deep_collect: task.deep_collect ?? false,
      source_link_strategy: task.source_link_strategy ?? 'none',
      detail_max_items: task.detail_max_items ?? 5,
      detail_max_swipes: task.detail_max_swipes ?? 12,
      min_score_to_detail: task.min_score_to_detail ?? 60,
      min_subject_match: task.min_subject_match ?? 70,
      min_score_to_persist: task.min_score_to_persist ?? 0,
      project_id: task.project_id ?? undefined,
    })
    setEditorOpen(true)
  }

  const submit = async () => {
    try {
      const values = await form.validateFields()
      setSaving(true)
      if (editing) {
        await updateTaskDef(editing.task_def_id, values)
        message.success('已更新')
      } else {
        await createTaskDef(values)
        message.success('已创建')
      }
      setEditorOpen(false)
      loadTasks()
    } catch (e) {
      if ((e as { errorFields?: unknown }).errorFields) return
      message.error(`保存失败: ${(e as Error).message}`)
    } finally {
      setSaving(false)
    }
  }

  const doRun = async (task: CollectTaskDef) => {
    try {
      await runTaskDef(task.task_def_id)
      message.success('已启动采集')
      loadTasks()
    } catch (e) {
      message.error(`启动失败: ${(e as Error).message}`)
    }
  }

  const doStop = async (task: CollectTaskDef) => {
    try {
      const r = await stopTaskDef(task.task_def_id)
      message.success(r.ok ? '已发送停止' : '无运行实例')
      loadTasks()
    } catch (e) {
      message.error(`停止失败: ${(e as Error).message}`)
    }
  }

  const doDryRun = async (task: CollectTaskDef) => {
    setDryRunTask(task)
    setDryRunResult(null)
    setDryRunOpen(true)
    setDryRunning(true)
    try {
      const res = await dryRunTaskDef(task.task_def_id, 50)
      setDryRunResult(res)
      message.success(`试跑完成: 结构化 ${res.total} 条(未入库/未通知)`)
    } catch (e) {
      message.error(`试跑失败: ${(e as Error).message}`)
    } finally {
      setDryRunning(false)
    }
  }

  const doDelete = async (task: CollectTaskDef) => {
    try {
      await deleteTaskDef(task.task_def_id)
      message.success('已删除')
      loadTasks()
    } catch (e) {
      message.error(`删除失败: ${(e as Error).message}`)
    }
  }

  // ── 记录 ──
  const openRecords = async (task: CollectTaskDef, incremental = false) => {
    setRecordsTask(task)
    setOnlyIncremental(incremental)
    setRecordsOpen(true)
    setRecordsLoading(true)
    try {
      const res = await listRecords({ task_def_id: task.task_def_id, only_incremental: incremental, limit: 100 })
      setRecords(res.items)
    } catch (e) {
      message.error(`加载记录失败: ${(e as Error).message}`)
    } finally {
      setRecordsLoading(false)
    }
  }

  const reloadRecords = async (incremental: boolean) => {
    if (!recordsTask) return
    setOnlyIncremental(incremental)
    setRecordsLoading(true)
    try {
      const res = await listRecords({ task_def_id: recordsTask.task_def_id, only_incremental: incremental, limit: 100 })
      setRecords(res.items)
    } finally {
      setRecordsLoading(false)
    }
  }

  // ── 调度 ──
  const openSchedules = async (task: CollectTaskDef) => {
    setScheduleTask(task)
    setScheduleOpen(true)
    scheduleForm.setFieldsValue({ name: `${task.name} 定时`, triggerType: 'interval', interval_seconds: 3600, cron: '0 8 * * *', enabled: true })
    try {
      const res = await listSchedules(task.task_def_id)
      setSchedules(res.items)
    } catch (e) {
      message.error(`加载调度失败: ${(e as Error).message}`)
    }
  }

  const reloadSchedules = async () => {
    if (!scheduleTask) return
    const res = await listSchedules(scheduleTask.task_def_id)
    setSchedules(res.items)
  }

  const addSchedule = async () => {
    if (!scheduleTask) return
    try {
      const v = await scheduleForm.validateFields()
      const trigger =
        v.triggerType === 'interval'
          ? { type: 'interval' as const, interval_seconds: v.interval_seconds }
          : { type: 'cron' as const, cron: v.cron }
      await createSchedule({ name: v.name, target_id: scheduleTask.task_def_id, trigger, enabled: v.enabled })
      message.success('已创建调度')
      reloadSchedules()
    } catch (e) {
      if ((e as { errorFields?: unknown }).errorFields) return
      message.error(`创建调度失败: ${(e as Error).message}`)
    }
  }

  const toggleSchedule = async (s: ScheduleDef, enabled: boolean) => {
    try {
      await updateSchedule(s.schedule_id, { enabled })
      reloadSchedules()
    } catch (e) {
      message.error(`更新失败: ${(e as Error).message}`)
    }
  }

  const removeSchedule = async (s: ScheduleDef) => {
    try {
      await deleteSchedule(s.schedule_id)
      reloadSchedules()
    } catch (e) {
      message.error(`删除失败: ${(e as Error).message}`)
    }
  }

  const columns: ColumnsType<CollectTaskDef> = [
    { title: '名称', dataIndex: 'name', key: 'name', width: 160, ellipsis: true },
    { title: '应用', dataIndex: 'app_name', key: 'app_name', width: 90 },
    {
      title: '关键词',
      dataIndex: 'keywords',
      key: 'keywords',
      width: 220,
      render: (kws: string[]) =>
        kws?.length ? kws.map((k) => <Tag key={k}>{k}</Tag>) : <span style={{ color: '#999' }}>无(浏览)</span>,
    },
    { title: '设备', dataIndex: 'device_id', key: 'device_id', width: 150, ellipsis: true },
    { title: '状态', dataIndex: 'status', key: 'status', width: 90, render: statusTag },
    {
      title: '操作',
      key: 'actions',
      width: 400,
      render: (_, task) => (
        <Space size="small" wrap>
          {task.status === 'running' ? (
            <Button size="small" icon={<PauseCircleOutlined />} onClick={() => doStop(task)}>
              停止
            </Button>
          ) : (
            <Button size="small" type="primary" icon={<PlayCircleOutlined />} onClick={() => doRun(task)}>
              运行
            </Button>
          )}
          <Button
            size="small"
            icon={<ExperimentOutlined />}
            disabled={task.status === 'running'}
            onClick={() => doDryRun(task)}
          >
            试跑
          </Button>
          <Button size="small" icon={<DatabaseOutlined />} onClick={() => openRecords(task)}>
            记录
          </Button>
          <Button size="small" icon={<ClockCircleOutlined />} onClick={() => openSchedules(task)}>
            定时
          </Button>
          <Button size="small" icon={<EditOutlined />} onClick={() => openEdit(task)}>
            编辑
          </Button>
          <Popconfirm title="确认删除该采集任务?" onConfirm={() => doDelete(task)}>
            <Button size="small" danger icon={<DeleteOutlined />} />
          </Popconfirm>
        </Space>
      ),
    },
  ]

  const renderScore = (score?: number | null) => {
    if (score == null) return <span style={{ color: '#999' }}>-</span>
    const color = score >= 80 ? 'green' : score >= 60 ? 'blue' : score >= 40 ? 'orange' : 'default'
    return <Tag color={color}>{score}</Tag>
  }

  const previewColumns: ColumnsType<DryRunPreviewItem> = [
    {
      title: '截图',
      key: 'shot',
      width: 90,
      render: (_, r) =>
        r.screenshot_url ? <AuthImage url={r.screenshot_url} /> : <span style={{ color: '#999' }}>-</span>,
    },
    {
      title: '相关性',
      key: 'score',
      width: 110,
      sorter: (a, b) => (a.score ?? -1) - (b.score ?? -1),
      defaultSortOrder: 'descend',
      render: (_, r) => (
        <Space direction="vertical" size={2}>
          {renderScore(r.score)}
          {r.subject_match != null ? (
            <span style={{ fontSize: 11, color: '#888' }}>主体 {r.subject_match}</span>
          ) : null}
          {r.detail ? <Tag color="purple">深采</Tag> : null}
        </Space>
      ),
    },
    { title: '关键词', dataIndex: 'keyword', key: 'keyword', width: 110, ellipsis: true },
    {
      title: '结构化字段',
      key: 'fields',
      render: (_, r) => (
        <div style={{ maxWidth: 480, whiteSpace: 'pre-wrap', wordBreak: 'break-word' }}>
          {Object.entries(r.fields || {})
            .map(([k, v]) => `${k}: ${Array.isArray(v) ? v.join('/') : String(v)}`)
            .join('\n')}
          {r.score_reason ? <div style={{ color: '#999', marginTop: 4 }}>打分理由: {r.score_reason}</div> : null}
          <div style={{ marginTop: 4 }}>
            {r.contacts_count ? <Tag color="magenta">联系方式 {r.contacts_count}</Tag> : null}
            {r.source_url ? (
              <a href={r.source_url} target="_blank" rel="noreferrer">
                原文链接
              </a>
            ) : null}
          </div>
        </div>
      ),
    },
  ]

  const presetMenu = {
    items: presets.map((p) => ({ key: p.preset_id, label: p.title })),
    onClick: ({ key }: { key: string }) => {
      const p = presets.find((x) => x.preset_id === key)
      if (p) openCreate(p)
    },
  }

  return (
    <div style={{ padding: 16 }}>
      <Card
        title="手机采集任务"
        extra={
          <Space>
            <Button icon={<ReloadOutlined />} onClick={loadTasks}>
              刷新
            </Button>
            <Dropdown menu={presetMenu} disabled={!presets.length}>
              <Button>从预设创建</Button>
            </Dropdown>
            <Button type="primary" icon={<PlusOutlined />} onClick={() => openCreate()}>
              新建任务
            </Button>
          </Space>
        }
      >
        <Table
          rowKey="task_def_id"
          loading={loading}
          columns={columns}
          dataSource={tasks}
          locale={{ emptyText: <Empty description="暂无采集任务，点击右上角新建" /> }}
          pagination={{ pageSize: 10, hideOnSinglePage: true }}
          scroll={{ x: 1110 }}
        />
      </Card>

      {/* 编辑抽屉 */}
      <Drawer
        title={editing ? '编辑采集任务' : '新建采集任务'}
        size={560}
        open={editorOpen}
        onClose={() => setEditorOpen(false)}
        forceRender
        extra={
          <Button type="primary" loading={saving} onClick={submit}>
            保存
          </Button>
        }
      >
        <Form form={form} layout="vertical">
          <Form.Item name="name" label="任务名称" rules={[{ required: true, message: '请输入任务名称' }]}>
            <Input placeholder="如: 公众号搜索入库分析" />
          </Form.Item>
          <Form.Item name="device_id" label="执行设备" rules={[{ required: true, message: '请选择设备' }]}>
            <Select
              placeholder="选择设备"
              showSearch
              options={devices.map((d) => ({
                value: d.device_id,
                label: `${d.model || d.device_id} (${d.status})`,
              }))}
            />
          </Form.Item>
          <Form.Item name="app_name" label="目标应用" rules={[{ required: true, message: '请输入应用名' }]}>
            <Input placeholder="如: 微信 / 小红书" />
          </Form.Item>
          <Form.Item name="keywords" label="搜索关键词" tooltip="留空则仅打开应用浏览(如养号)">
            <Select mode="tags" placeholder="输入关键词后回车，可多个" tokenSeparators={[',']} />
          </Form.Item>
          <Space size="large">
            <Form.Item name="swipe_times" label="滑动次数" rules={[{ required: true }]}>
              <InputNumber min={0} max={50} />
            </Form.Item>
            <Form.Item name="swipe_interval" label="滑动间隔(秒)" rules={[{ required: true }]}>
              <InputNumber min={0.2} max={10} step={0.1} />
            </Form.Item>
            <Form.Item name="notify_on" label="增量通知" rules={[{ required: true }]}>
              <Select style={{ width: 130 }} options={NOTIFY_OPTIONS} />
            </Form.Item>
          </Space>
          <Form.Item label="结构化提取字段" tooltip="留空则仅记录整屏摘要">
            <Form.List name="extract_fields">
              {(fields, { add, remove }) => (
                <div>
                  {fields.map((field) => (
                    <div key={field.key} className="mobile-collect-field-row">
                      <div className="mobile-collect-field-name">
                        <Form.Item name={[field.name, 'name']} rules={[{ required: true, message: '字段名' }]} noStyle>
                          <Input placeholder="字段名" />
                        </Form.Item>
                      </div>
                      <div className="mobile-collect-field-description">
                        <Form.Item name={[field.name, 'description']} noStyle>
                          <Input placeholder="含义描述" />
                        </Form.Item>
                      </div>
                      <div className="mobile-collect-field-type">
                        <Form.Item name={[field.name, 'type']} noStyle initialValue="string">
                          <Select options={FIELD_TYPE_OPTIONS} />
                        </Form.Item>
                      </div>
                      <Button
                        className="mobile-collect-field-remove"
                        size="small"
                        danger
                        icon={<DeleteOutlined />}
                        title="删除字段"
                        aria-label="删除字段"
                        onClick={() => remove(field.name)}
                      />
                    </div>
                  ))}
                  <Button type="dashed" onClick={() => add({ type: 'string' })} block icon={<PlusOutlined />}>
                    添加字段
                  </Button>
                </div>
              )}
            </Form.List>
          </Form.Item>
          <Form.Item
            name="dedup_key_fields"
            label="去重键字段"
            tooltip="用于生成稳定 record_id，实现增量去重；留空则按整条内容去重"
          >
            <Select mode="tags" placeholder="选择/输入作为去重键的字段名" tokenSeparators={[',']} />
          </Form.Item>
          <Form.Item name="search_hint" label="搜索步骤补充说明(可选)">
            <Input.TextArea rows={2} placeholder="注入规划层目标的补充说明，如: 在顶部搜索框输入后选择公众号结果" />
          </Form.Item>
          <Space wrap size="large" style={{ marginBottom: 8 }}>
            <Form.Item
              name="deep_collect"
              label="深入采集"
              valuePropName="checked"
              tooltip="开启后：列表页全部条目结构化入库(广度)，AI 再对高分条目点进详情页深采(深度)"
            >
              <Switch />
            </Form.Item>
            <Form.Item
              name="source_link_strategy"
              label="原文链接提取"
              tooltip="进入详情页后通过运行时适配器获取真实原文 URL；微信公众号预设使用复制链接策略"
            >
              <Select
                style={{ width: 190 }}
                options={sourceLinkStrategies.map((item) => ({
                  value: item.strategy,
                  label: item.label,
                  title: item.description,
                }))}
              />
            </Form.Item>
            <Form.Item name="detail_max_items" label="每屏深采上限" tooltip="每个列表页最多点进几条深采">
              <InputNumber min={0} max={20} />
            </Form.Item>
            <Form.Item name="detail_max_swipes" label="详情滑到底上限" tooltip="详情页最多滑动几屏以滑到底(视觉到底自动提前停)">
              <InputNumber min={0} max={20} />
            </Form.Item>
            <Form.Item name="min_score_to_detail" label="深采分数阈值" tooltip="triage 相关性分达到该值才点进详情(0-100)">
              <InputNumber min={0} max={100} />
            </Form.Item>
            <Form.Item name="min_subject_match" label="主体对应阈值" tooltip="主体对应程度达到该值才点进详情, 避免什么都点(0-100)">
              <InputNumber min={0} max={100} />
            </Form.Item>
            <Form.Item name="min_score_to_persist" label="入库最低分" tooltip="低于该分不入库/不通知(0=全收)">
              <InputNumber min={0} max={100} />
            </Form.Item>
          </Space>
          <Form.Item name="project_id" label="归属项目(可选)">
            <Input placeholder="project_id" />
          </Form.Item>
        </Form>
      </Drawer>

      {/* 记录抽屉 */}
      <Drawer
        title={`采集记录 - ${recordsTask?.name ?? ''}`}
        size={820}
        open={recordsOpen}
        onClose={() => setRecordsOpen(false)}
        extra={
          <Space>
            <span>仅增量</span>
            <Switch checked={onlyIncremental} onChange={reloadRecords} />
            <Button icon={<ReloadOutlined />} onClick={() => reloadRecords(onlyIncremental)}>
              刷新
            </Button>
          </Space>
        }
      >
        <CollectRecordsView
          records={records}
          loading={recordsLoading}
          emptyText="暂无采集记录"
        />
      </Drawer>

      {/* 调度抽屉 */}
      <Drawer
        title={`定时调度 - ${scheduleTask?.name ?? ''}`}
        size={560}
        open={scheduleOpen}
        onClose={() => setScheduleOpen(false)}
      >
        <Card size="small" title="新增调度" style={{ marginBottom: 16 }}>
          <Form form={scheduleForm} layout="vertical">
            <Form.Item name="name" label="调度名称" rules={[{ required: true }]}>
              <Input />
            </Form.Item>
            <Form.Item name="triggerType" label="触发方式" rules={[{ required: true }]}>
              <Select
                options={[
                  { value: 'interval', label: '固定间隔' },
                  { value: 'cron', label: 'Cron 表达式' },
                ]}
              />
            </Form.Item>
            <Form.Item noStyle shouldUpdate={(p, c) => p.triggerType !== c.triggerType}>
              {({ getFieldValue }) =>
                getFieldValue('triggerType') === 'cron' ? (
                  <Form.Item name="cron" label="Cron (分 时 日 月 周)" rules={[{ required: true }]}>
                    <Input placeholder="如: 0 8 * * * (每天8点)" />
                  </Form.Item>
                ) : (
                  <Form.Item name="interval_seconds" label="间隔(秒)" rules={[{ required: true }]}>
                    <InputNumber min={30} style={{ width: 200 }} />
                  </Form.Item>
                )
              }
            </Form.Item>
            <Form.Item name="enabled" label="启用" valuePropName="checked">
              <Switch defaultChecked />
            </Form.Item>
            <Button type="primary" onClick={addSchedule}>
              添加调度
            </Button>
          </Form>
        </Card>
        <Table
          rowKey="schedule_id"
          size="small"
          dataSource={schedules}
          pagination={false}
          locale={{ emptyText: <Empty description="暂无调度" /> }}
          columns={[
            { title: '名称', dataIndex: 'name', ellipsis: true },
            {
              title: '触发',
              key: 'trigger',
              render: (_, s: ScheduleDef) =>
                s.trigger.type === 'cron' ? `cron: ${s.trigger.cron}` : `每 ${s.trigger.interval_seconds}s`,
            },
            { title: '下次', dataIndex: 'next_run', ellipsis: true, width: 160 },
            {
              title: '启用',
              key: 'enabled',
              width: 70,
              render: (_, s: ScheduleDef) => (
                <Switch size="small" checked={s.enabled} onChange={(v) => toggleSchedule(s, v)} />
              ),
            },
            {
              title: '',
              key: 'op',
              width: 50,
              render: (_, s: ScheduleDef) => (
                <Popconfirm title="删除该调度?" onConfirm={() => removeSchedule(s)}>
                  <Button size="small" danger icon={<DeleteOutlined />} />
                </Popconfirm>
              ),
            },
          ]}
        />
      </Drawer>
      {/* 试跑预览抽屉 */}
      <Drawer
        title={`试跑预览 - ${dryRunTask?.name ?? ''}`}
        size={820}
        open={dryRunOpen}
        onClose={() => setDryRunOpen(false)}
        extra={
          dryRunTask && (
            <Button
              icon={<ExperimentOutlined />}
              loading={dryRunning}
              onClick={() => doDryRun(dryRunTask)}
            >
              重新试跑
            </Button>
          )
        }
      >
        {dryRunning ? (
          <div style={{ textAlign: 'center', padding: '48px 0' }}>
            <Spin tip="试跑中: 导航 → 截屏 → 结构化(不入库/不通知)..." />
          </div>
        ) : dryRunResult ? (
          <>
            <Space style={{ marginBottom: 12 }} wrap>
              <Tag color="blue">结构化 {dryRunResult.total} 条</Tag>
              <Tag color="green">预览 {dryRunResult.preview.length} 条</Tag>
              {dryRunResult.stopped && <Tag color="red">已中断</Tag>}
              <span style={{ color: '#999' }}>试跑结果不入库、不发送通知</span>
            </Space>
            <Table
              rowKey={(_, idx) => String(idx)}
              columns={previewColumns}
              dataSource={dryRunResult.preview}
              locale={{ emptyText: <Empty description="无结构化结果(可能未匹配到内容或字段配置过严)" /> }}
              pagination={{ pageSize: 10, hideOnSinglePage: true }}
            />
          </>
        ) : (
          <Empty description="暂无试跑结果" />
        )}
      </Drawer>
    </div>
  )
}
