import { useCallback, useEffect, useState } from 'react'
import Button from 'antd/es/button'
import Drawer from 'antd/es/drawer'
import Empty from 'antd/es/empty'
import Form from 'antd/es/form'
import Input from 'antd/es/input'
import InputNumber from 'antd/es/input-number'
import Popconfirm from 'antd/es/popconfirm'
import Select from 'antd/es/select'
import Space from 'antd/es/space'
import Switch from 'antd/es/switch'
import Table from 'antd/es/table'
import Tag from 'antd/es/tag'
import message from 'antd/es/message'
import type { ColumnsType } from 'antd/es/table'
import {
  ClockCircleOutlined,
  DeleteOutlined,
  EditOutlined,
  PlayCircleOutlined,
  PlusOutlined,
  ReloadOutlined,
} from '@ant-design/icons'

import {
  createMobileMonitor,
  deleteMobileMonitor,
  listMobileMonitors,
  runMobileMonitor,
  updateMobileMonitor,
  type AppInstance,
  type MobileMonitor,
  type MobileMonitorInput,
  type MobileMonitorScope,
} from '../../../services/mobileCollectService'
import { listProjects, type Project } from '../../../services/projectService'
import {
  listProjectTargetOptions,
  type ProjectTargetOption,
} from '../../../services/sourceDocumentService'
import type { SimpleDevice } from '../../../services/mobileService'

interface MonitorFormValues {
  name?: string
  project_id: string
  target_id: string
  device_id: string
  scope: MobileMonitorScope
  official_accounts?: string[]
  app_instance: AppInstance
  trigger_type: 'interval' | 'cron'
  interval_seconds?: number
  cron?: string
  enabled: boolean
}

function formatTime(value?: string | null) {
  if (!value) return '-'
  const date = new Date(value)
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString()
}

function triggerLabel(monitor: MobileMonitor) {
  if (monitor.trigger.type === 'cron') return `Cron ${monitor.trigger.cron || '-'}`
  const seconds = Number(monitor.trigger.interval_seconds || 0)
  if (seconds > 0 && seconds % 86_400 === 0) return `每 ${seconds / 86_400} 天`
  if (seconds > 0 && seconds % 3_600 === 0) return `每 ${seconds / 3_600} 小时`
  return `每 ${seconds || '-'} 秒`
}

export default function MobileMonitorPanel({ devices }: { devices: SimpleDevice[] }) {
  const [monitors, setMonitors] = useState<MobileMonitor[]>([])
  const [loading, setLoading] = useState(false)
  const [drawerOpen, setDrawerOpen] = useState(false)
  const [editing, setEditing] = useState<MobileMonitor | null>(null)
  const [saving, setSaving] = useState(false)
  const [actionId, setActionId] = useState('')
  const [projects, setProjects] = useState<Project[]>([])
  const [projectsLoading, setProjectsLoading] = useState(false)
  const [targets, setTargets] = useState<ProjectTargetOption[]>([])
  const [targetsLoading, setTargetsLoading] = useState(false)
  const [form] = Form.useForm<MonitorFormValues>()
  const projectId = Form.useWatch('project_id', form)
  const scope = Form.useWatch('scope', form)
  const triggerType = Form.useWatch('trigger_type', form)

  const loadMonitors = useCallback(async () => {
    setLoading(true)
    try {
      const result = await listMobileMonitors()
      setMonitors(result.items)
    } catch (error) {
      message.error(`加载增量监控失败: ${(error as Error).message}`)
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    void loadMonitors()
  }, [loadMonitors])

  const ensureProjects = async () => {
    if (projects.length || projectsLoading) return
    setProjectsLoading(true)
    try {
      const result = await listProjects({ page: 1, page_size: 100 })
      setProjects(result.items)
    } catch (error) {
      message.error(`加载项目失败: ${(error as Error).message}`)
    } finally {
      setProjectsLoading(false)
    }
  }

  const loadTargets = async (projectId: string) => {
    setTargets([])
    if (!projectId) return
    setTargetsLoading(true)
    try {
      const result = await listProjectTargetOptions(projectId)
      setTargets(result.items)
    } catch (error) {
      message.error(`加载 Target 失败: ${(error as Error).message}`)
    } finally {
      setTargetsLoading(false)
    }
  }

  const openCreate = () => {
    setEditing(null)
    setTargets([])
    form.resetFields()
    form.setFieldsValue({
      scope: 'target',
      app_instance: 'primary',
      trigger_type: 'interval',
      interval_seconds: 21_600,
      enabled: true,
    })
    setDrawerOpen(true)
    void ensureProjects()
  }

  const openEdit = (monitor: MobileMonitor) => {
    setEditing(monitor)
    form.setFieldsValue({
      name: monitor.name,
      project_id: monitor.project_id,
      target_id: monitor.target_id,
      device_id: monitor.device_id,
      scope: monitor.scope,
      official_accounts: monitor.official_accounts,
      app_instance: monitor.app_instance || 'primary',
      trigger_type: monitor.trigger.type,
      interval_seconds: monitor.trigger.interval_seconds || 21_600,
      cron: monitor.trigger.cron || '0 8 * * *',
      enabled: monitor.enabled,
    })
    setDrawerOpen(true)
    void ensureProjects()
    void loadTargets(monitor.project_id)
  }

  const save = async () => {
    try {
      const values = await form.validateFields()
      const trigger = values.trigger_type === 'cron'
        ? { type: 'cron' as const, cron: values.cron }
        : { type: 'interval' as const, interval_seconds: values.interval_seconds }
      setSaving(true)
      if (editing) {
        await updateMobileMonitor(editing.monitor_id, {
          name: values.name,
          device_id: values.device_id,
          official_accounts: values.official_accounts || [],
          app_instance: values.app_instance,
          trigger,
          enabled: values.enabled,
        })
      } else {
        const payload: MobileMonitorInput = {
          name: values.name,
          project_id: values.project_id,
          target_id: values.target_id,
          device_id: values.device_id,
          scope: values.scope,
          official_accounts: values.official_accounts || [],
          app_instance: values.app_instance,
          trigger,
          enabled: values.enabled,
        }
        await createMobileMonitor(payload)
      }
      message.success(editing ? '监控已更新' : '监控已创建')
      setDrawerOpen(false)
      await loadMonitors()
    } catch (error) {
      if ((error as { errorFields?: unknown }).errorFields) return
      message.error(`保存监控失败: ${(error as Error).message}`)
    } finally {
      setSaving(false)
    }
  }

  const toggle = async (monitor: MobileMonitor, enabled: boolean) => {
    setActionId(monitor.monitor_id)
    try {
      await updateMobileMonitor(monitor.monitor_id, { enabled })
      await loadMonitors()
    } catch (error) {
      message.error(`更新监控失败: ${(error as Error).message}`)
    } finally {
      setActionId('')
    }
  }

  const runNow = async (monitor: MobileMonitor) => {
    setActionId(monitor.monitor_id)
    try {
      await runMobileMonitor(monitor.monitor_id)
      message.success('已进入手机采集队列')
      await loadMonitors()
    } catch (error) {
      message.error(`启动监控失败: ${(error as Error).message}`)
    } finally {
      setActionId('')
    }
  }

  const remove = async (monitor: MobileMonitor) => {
    setActionId(monitor.monitor_id)
    try {
      await deleteMobileMonitor(monitor.monitor_id)
      message.success('监控已删除')
      await loadMonitors()
    } catch (error) {
      message.error(`删除监控失败: ${(error as Error).message}`)
    } finally {
      setActionId('')
    }
  }

  const columns: ColumnsType<MobileMonitor> = [
    {
      title: '监控范围',
      key: 'scope',
      width: 260,
      render: (_, monitor) => (
        <Space direction="vertical" size={2}>
          <Space size={6} wrap>
            <strong>{monitor.target_name}</strong>
            <Tag color={monitor.scope === 'official_account' ? 'green' : 'blue'}>
              {monitor.scope === 'official_account' ? '指定公众号' : 'Target'}
            </Tag>
          </Space>
          {monitor.official_accounts.length ? (
            <span className="mobile-monitor-secondary">
              {monitor.official_accounts.join('、')}
            </span>
          ) : null}
        </Space>
      ),
    },
    {
      title: '设备',
      key: 'device',
      width: 190,
      render: (_, monitor) => (
        <Space direction="vertical" size={2}>
          <span>{monitor.device_id}</span>
          <span className="mobile-monitor-secondary">
            {monitor.app_instance === 'clone' ? '微信分身' : '主微信'}
          </span>
        </Space>
      ),
    },
    {
      title: '周期',
      key: 'trigger',
      width: 170,
      render: (_, monitor) => (
        <Space direction="vertical" size={2}>
          <span>{triggerLabel(monitor)}</span>
          <span className="mobile-monitor-secondary">下次 {formatTime(monitor.next_run)}</span>
        </Space>
      ),
    },
    {
      title: '最近执行',
      key: 'last_run',
      width: 180,
      render: (_, monitor) => (
        <Space direction="vertical" size={2}>
          <span>{formatTime(monitor.last_run_at)}</span>
          {monitor.last_status ? <Tag>{monitor.last_status}</Tag> : null}
          {monitor.last_error ? <span className="mobile-monitor-error">{monitor.last_error}</span> : null}
        </Space>
      ),
    },
    {
      title: '启用',
      key: 'enabled',
      width: 76,
      render: (_, monitor) => (
        <Switch
          size="small"
          checked={monitor.enabled}
          loading={actionId === monitor.monitor_id}
          onChange={(enabled) => void toggle(monitor, enabled)}
        />
      ),
    },
    {
      title: '操作',
      key: 'actions',
      width: 168,
      fixed: 'right',
      render: (_, monitor) => (
        <Space size="small">
          <Button
            size="small"
            title="立即执行"
            aria-label="立即执行"
            icon={<PlayCircleOutlined />}
            loading={actionId === monitor.monitor_id}
            disabled={monitor.task_status === 'running'}
            onClick={() => void runNow(monitor)}
          />
          <Button
            size="small"
            title="编辑"
            aria-label="编辑"
            icon={<EditOutlined />}
            onClick={() => openEdit(monitor)}
          />
          <Popconfirm title="删除该增量监控?" onConfirm={() => void remove(monitor)}>
            <Button
              size="small"
              danger
              title="删除"
              aria-label="删除"
              icon={<DeleteOutlined />}
            />
          </Popconfirm>
        </Space>
      ),
    },
  ]

  return (
    <div className="mobile-monitor-panel">
      <div className="mobile-monitor-toolbar">
        <Space>
          <Button icon={<ReloadOutlined />} onClick={() => void loadMonitors()}>
            刷新
          </Button>
          <Button type="primary" icon={<PlusOutlined />} onClick={openCreate}>
            新增监控
          </Button>
        </Space>
      </div>
      <Table
        rowKey="monitor_id"
        loading={loading}
        columns={columns}
        dataSource={monitors}
        locale={{ emptyText: <Empty description="暂无增量监控" /> }}
        pagination={{ pageSize: 10, hideOnSinglePage: true }}
        scroll={{ x: 1_050 }}
      />

      <Drawer
        title={editing ? '编辑增量监控' : '新增增量监控'}
        size={560}
        open={drawerOpen}
        onClose={() => setDrawerOpen(false)}
        extra={<Button type="primary" loading={saving} onClick={() => void save()}>保存</Button>}
      >
        <Form form={form} layout="vertical">
          <Form.Item name="name" label="监控名称">
            <Input placeholder="留空时按监控范围自动命名" />
          </Form.Item>
          <Form.Item name="project_id" label="项目" rules={[{ required: true, message: '请选择项目' }]}>
            <Select
              showSearch
              optionFilterProp="label"
              loading={projectsLoading}
              disabled={Boolean(editing)}
              options={projects.map((project) => ({ value: project.id, label: project.name }))}
              onChange={(projectId) => {
                form.setFieldValue('target_id', undefined)
                void loadTargets(projectId)
              }}
            />
          </Form.Item>
          <Form.Item name="target_id" label="Target" rules={[{ required: true, message: '请选择 Target' }]}>
            <Select
              showSearch
              optionFilterProp="label"
              loading={targetsLoading}
              disabled={Boolean(editing) || !projectId}
              options={targets.map((target) => ({
                value: target.target_id,
                label: target.batch_tags?.length
                  ? `${target.target_name} · ${target.batch_tags.join(' / ')}`
                  : target.target_name,
              }))}
            />
          </Form.Item>
          <Form.Item name="scope" label="监控范围" rules={[{ required: true }]}>
            <Select
              disabled={Boolean(editing)}
              options={[
                { value: 'target', label: 'Target 公众号文章' },
                { value: 'official_account', label: '指定公众号' },
              ]}
            />
          </Form.Item>
          {scope === 'official_account' ? (
            <Form.Item
              name="official_accounts"
              label="公众号名称"
              rules={[{ required: true, message: '至少填写一个公众号名称' }]}
            >
              <Select mode="tags" tokenSeparators={[',']} placeholder="输入后回车，可填写多个" />
            </Form.Item>
          ) : null}
          <Form.Item name="device_id" label="执行设备" rules={[{ required: true, message: '请选择设备' }]}>
            <Select
              showSearch
              optionFilterProp="label"
              options={devices.map((device) => ({
                value: device.device_id,
                label: `${device.model || device.device_id} · ${device.status}`,
              }))}
            />
          </Form.Item>
          <Form.Item name="app_instance" label="微信实例" rules={[{ required: true }]}>
            <Select
              options={[
                { value: 'primary', label: '主微信' },
                { value: 'clone', label: '微信分身' },
              ]}
            />
          </Form.Item>
          <Form.Item name="trigger_type" label="执行周期" rules={[{ required: true }]}>
            <Select
              suffixIcon={<ClockCircleOutlined />}
              options={[
                { value: 'interval', label: '固定间隔' },
                { value: 'cron', label: 'Cron' },
              ]}
            />
          </Form.Item>
          {triggerType === 'cron' ? (
            <Form.Item name="cron" label="Cron（分 时 日 月 周）" rules={[{ required: true }]}>
              <Input placeholder="0 8 * * *" />
            </Form.Item>
          ) : (
            <Form.Item
              name="interval_seconds"
              label="间隔秒数"
              rules={[{ required: true, message: '请填写间隔' }]}
            >
              <InputNumber min={1_800} max={2_592_000} style={{ width: '100%' }} />
            </Form.Item>
          )}
          <Form.Item name="enabled" label="启用" valuePropName="checked">
            <Switch />
          </Form.Item>
        </Form>
      </Drawer>
    </div>
  )
}
