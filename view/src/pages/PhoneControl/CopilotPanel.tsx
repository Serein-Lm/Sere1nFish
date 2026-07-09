import { useEffect, useMemo, useRef, useState, type ReactNode } from 'react'
import { Input, Button, Tooltip, Segmented, message, Empty, Drawer, Tag, Space, Typography } from 'antd'
import {
  RobotOutlined,
  MessageOutlined,
  ThunderboltOutlined,
  SendOutlined,
  StopOutlined,
  ClearOutlined,
  BulbOutlined,
  EyeOutlined,
  CheckCircleFilled,
  CloseCircleFilled,
  LoadingOutlined,
  ProfileOutlined,
  CommentOutlined,
  HistoryOutlined,
  ClockCircleOutlined,
  CodeOutlined,
  InfoCircleOutlined,
} from '@ant-design/icons'
import {
  planGoal,
  runPlanned,
  agentTask,
  cancelAgent,
  suggestChat,
  sendChat,
  getSuggestion,
  subscribeEvents,
  getRecentEvents,
  relativeTime,
  MobileError,
  type StageFrame,
  type TypedFrame,
  type MobileEvent,
} from '../../services/mobileService'

type Tab = 'agent' | 'assist' | 'events'
type SubtaskStatus = 'pending' | 'active' | 'done' | 'failed'

interface Subtask {
  task: string
  status: SubtaskStatus
}
interface ExecEntry {
  id: number
  kind: 'thinking' | 'step' | 'info' | 'error'
  text: string
  shot?: string
}
type OperationStatus = 'info' | 'active' | 'ok' | 'failed'

interface OperationDetail {
  id: number
  time: number
  kind: 'plan' | 'subtask' | 'step' | 'done' | 'error' | 'system'
  title: string
  status: OperationStatus
  message?: string
  actionText?: string
  stage?: string
  eventType?: string
  subtaskIndex?: number
  timings?: {
    llm_ms?: number | null
    action_ms?: number | null
  }
  batchResults?: Record<string, any>[]
  screenshot?: string
  raw?: unknown
}

const BG_KEY = 'mobile_my_background'
const { Text } = Typography

const formatAction = (action: unknown): string => {
  if (!action) return ''
  if (typeof action === 'string') return action
  if (typeof action !== 'object') return String(action)

  const obj = action as Record<string, any>
  const name = String(obj.action ?? obj._metadata ?? '')
  if (name === 'launch_app' || name === 'Launch') return `打开 ${obj.app_name ?? obj.app ?? '应用'}`
  if (name === 'swipe' || name === 'Swipe') return '滑动浏览'
  if (name === 'wait' || name === 'Wait') {
    const seconds = obj.seconds ?? obj.duration
    return seconds ? `等待 ${seconds}${typeof seconds === 'number' ? ' 秒' : ''}` : '等待'
  }
  if (name === 'home' || name === 'Home') return '回到主屏幕'
  if (name === 'back' || name === 'Back') return '返回'
  if (name === 'Press Key') return `按键 ${obj.key ?? ''}`.trim()
  if (name === 'wake') return '唤醒屏幕'
  if (name === 'wake_unlock') return '唤醒并解锁屏幕'
  if (name === 'Tap') return '点击'
  if (name === 'Double Tap') return '双击'
  if (name === 'Long Press') return '长按'
  if (name === 'Type' || name === 'Type_Name') return `输入 ${obj.text ?? ''}`.trim()
  if (obj.message) return String(obj.message)
  try {
    return JSON.stringify(obj)
  } catch {
    return String(action)
  }
}

const formatStepText = (step: unknown, action: unknown, messageText: unknown): string => {
  const actionText = formatAction(action)
  const messagePart = typeof messageText === 'string' ? messageText : ''
  const detail = [actionText, messagePart].filter(Boolean).join(' · ')
  return `第 ${step ?? '?'} 步${detail ? ` · ${detail}` : ''}`
}

const formatMs = (value: unknown): string => {
  if (typeof value !== 'number' || !Number.isFinite(value)) return '-'
  return value >= 1000 ? `${(value / 1000).toFixed(1)}s` : `${Math.round(value)}ms`
}

const screenshotSrc = (shot: string): string =>
  shot.startsWith('data:') ? shot : `data:image/png;base64,${shot}`

const statusLabel: Record<OperationStatus, string> = {
  info: '信息',
  active: '进行中',
  ok: '成功',
  failed: '失败',
}

const statusColor: Record<OperationStatus, string> = {
  info: 'blue',
  active: 'processing',
  ok: 'success',
  failed: 'error',
}

const safeJson = (value: unknown): string => {
  try {
    return JSON.stringify(value, null, 2)
  } catch {
    return String(value)
  }
}

export default function CopilotPanel({ deviceId }: { deviceId: string }) {
  const [tab, setTab] = useState<Tab>('agent')

  // ---------------- AI 操作 ----------------
  const [goal, setGoal] = useState('')
  const [subtasks, setSubtasks] = useState<Subtask[]>([])
  const [execLog, setExecLog] = useState<ExecEntry[]>([])
  const [running, setRunning] = useState(false)
  const [planning, setPlanning] = useState(false)
  const [agentMode, setAgentMode] = useState<'planned' | 'single'>('planned')
  const [detailOpen, setDetailOpen] = useState(false)
  const [operationEvents, setOperationEvents] = useState<OperationDetail[]>([])
  const planIdRef = useRef<string | null>(null)
  const agentAbort = useRef<AbortController | null>(null)
  const logIdRef = useRef(0)
  const detailIdRef = useRef(0)
  const logEndRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    logEndRef.current?.scrollIntoView({ behavior: 'smooth', block: 'end' })
  }, [execLog])

  const pushLog = (kind: ExecEntry['kind'], text: string, shot?: string) =>
    setExecLog((prev) => [...prev.slice(-59), { id: ++logIdRef.current, kind, text, shot }])

  const appendThinking = (chunk: string) =>
    setExecLog((prev) => {
      const last = prev[prev.length - 1]
      if (last && last.kind === 'thinking') {
        const copy = prev.slice()
        copy[copy.length - 1] = { ...last, text: last.text + chunk }
        return copy
      }
      return [...prev.slice(-59), { id: ++logIdRef.current, kind: 'thinking', text: chunk }]
    })

  const setSubtaskStatus = (index: number, status: SubtaskStatus) =>
    setSubtasks((prev) => prev.map((s, i) => (i === index ? { ...s, status } : s)))

  const pushOperation = (detail: Omit<OperationDetail, 'id' | 'time'>) =>
    setOperationEvents((prev) => [
      ...prev.slice(-119),
      { ...detail, id: ++detailIdRef.current, time: Date.now() },
    ])

  const operationStats = useMemo(() => {
    const steps = operationEvents.filter((e) => e.kind === 'step')
    const failures = operationEvents.filter((e) => e.status === 'failed')
    const batchSteps = steps.filter((e) => (e.batchResults?.length ?? 0) > 0)
    const llmTotal = steps.reduce((sum, e) => sum + (e.timings?.llm_ms ?? 0), 0)
    const actionTotal = steps.reduce((sum, e) => sum + (e.timings?.action_ms ?? 0), 0)
    return {
      total: operationEvents.length,
      steps: steps.length,
      failures: failures.length,
      batchSteps: batchSteps.length,
      llmTotal,
      actionTotal,
    }
  }, [operationEvents])

  const handlePreview = async () => {
    if (!goal.trim()) return
    setPlanning(true)
    setSubtasks([])
    try {
      const res = await planGoal(goal.trim())
      setSubtasks(res.subtasks.map((task) => ({ task, status: 'pending' as SubtaskStatus })))
      message.success(`已拆解 ${res.subtasks.length} 个子任务`)
    } catch (e) {
      message.error(e instanceof Error ? e.message : '规划失败')
    } finally {
      setPlanning(false)
    }
  }

  const handleRun = async () => {
    if (!goal.trim() || running) return
    setRunning(true)
    setExecLog([])
    setOperationEvents([])
    if (agentMode === 'single') setSubtasks([])
    planIdRef.current = null
    const ctrl = new AbortController()
    agentAbort.current = ctrl

    const onFrame = (f: StageFrame) => {
      const d = (f.data ?? {}) as Record<string, any>
      switch (f.stage) {
        case 'planning':
          planIdRef.current = d.plan_id
          pushLog('info', d.mode === 'compiled_tools' ? '任务开始执行（快速工具）…' : '开始规划任务…')
          pushOperation({
            kind: 'system',
            title: d.mode === 'compiled_tools' ? '快速工具路径启动' : '规划任务启动',
            status: 'active',
            stage: f.stage,
            message: d.goal,
            raw: f,
          })
          break
        case 'device_ready':
          pushLog('info', d.wake_ok ? '设备已自动唤醒' : '已尝试自动唤醒设备')
          pushOperation({
            kind: 'system',
            title: '设备准备',
            status: d.wake_ok ? 'ok' : 'info',
            stage: f.stage,
            message: d.wake_ok ? '设备已自动唤醒' : '已尝试自动唤醒设备',
            raw: f,
          })
          break
        case 'screen':
          if (d.analysis) pushLog('info', `读屏：${d.analysis}`)
          pushOperation({
            kind: 'system',
            title: '规划前读屏',
            status: 'ok',
            stage: f.stage,
            message: d.analysis,
            raw: f,
          })
          break
        case 'screen_error':
          pushLog('info', `读屏降级：${d.message ?? ''}`)
          pushOperation({
            kind: 'system',
            title: '读屏降级',
            status: 'failed',
            stage: f.stage,
            message: d.message,
            raw: f,
          })
          break
        case 'plan':
          setSubtasks((d.subtasks ?? []).map((task: string) => ({ task, status: 'pending' as SubtaskStatus })))
          if (d.replanned) message.info('AI 重新规划了剩余步骤')
          if (d.mode === 'compiled_tools') pushLog('info', '已命中快速工具执行路径')
          pushOperation({
            kind: 'plan',
            title: d.replanned ? '重规划步骤' : '生成执行计划',
            status: 'ok',
            stage: f.stage,
            message: Array.isArray(d.subtasks) ? d.subtasks.join(' → ') : '',
            raw: f,
          })
          break
        case 'subtask_start':
          setSubtaskStatus(d.index, 'active')
          pushOperation({
            kind: 'subtask',
            title: `开始子任务 ${Number(d.index ?? 0) + 1}`,
            status: 'active',
            stage: f.stage,
            subtaskIndex: d.index,
            message: d.task,
            raw: f,
          })
          break
        case 'exec': {
          const ev = d.event as Record<string, any>
          if (!ev) break
          const ed = (ev.data ?? {}) as Record<string, any>
          if (ev.type === 'thinking' && ed.chunk) appendThinking(String(ed.chunk))
          else if (ev.type === 'step') {
            const actionText = formatAction(ed.action)
            pushLog('step', formatStepText(ed.step, ed.action, ed.message), ed.screenshot)
            pushOperation({
              kind: 'step',
              title: `执行步骤 ${ed.step ?? '?'}`,
              status: ed.success === false ? 'failed' : 'ok',
              stage: f.stage,
              eventType: ev.type,
              subtaskIndex: d.index,
              message: ed.message,
              actionText,
              timings: ed.timings,
              batchResults: ed.batch_results,
              screenshot: ed.screenshot,
              raw: ev,
            })
          } else if (ev.type === 'done') {
            pushLog('info', ed.message ?? '子任务完成')
            pushOperation({
              kind: 'done',
              title: '子任务结束',
              status: ed.success === false ? 'failed' : 'ok',
              stage: f.stage,
              eventType: ev.type,
              subtaskIndex: d.index,
              message: ed.message,
              raw: ev,
            })
          }
          break
        }
        case 'subtask_done':
          setSubtaskStatus(d.index, d.success ? 'done' : 'failed')
          pushOperation({
            kind: 'subtask',
            title: `子任务 ${Number(d.index ?? 0) + 1} ${d.success ? '完成' : '失败'}`,
            status: d.success ? 'ok' : 'failed',
            stage: f.stage,
            subtaskIndex: d.index,
            message: d.result?.message,
            raw: f,
          })
          break
        case 'replanning':
          pushLog('info', 'AI 正在调整方案…')
          pushOperation({
            kind: 'plan',
            title: '触发重规划',
            status: 'active',
            stage: f.stage,
            message: `失败步骤：${Number(d.failed_index ?? 0) + 1}，第 ${d.attempt ?? 1} 次重规划`,
            raw: f,
          })
          break
        case 'aborted':
          pushLog('error', `已中止：${d.reason ?? ''}`)
          pushOperation({
            kind: 'error',
            title: '任务中止',
            status: 'failed',
            stage: f.stage,
            message: d.reason,
            raw: f,
          })
          break
        case 'cancelled':
          pushLog('info', '任务已取消')
          pushOperation({
            kind: 'system',
            title: '任务取消',
            status: 'info',
            stage: f.stage,
            raw: f,
          })
          break
        case 'done':
          pushLog('info', `全部完成 ${d.completed ?? ''}/${(d.subtasks ?? []).length || ''}`)
          pushOperation({
            kind: 'done',
            title: '全部完成',
            status: 'ok',
            stage: f.stage,
            message: `${d.completed ?? ''}/${(d.subtasks ?? []).length || ''}`,
            raw: f,
          })
          message.success('AI 任务完成')
          break
        case 'error':
          pushLog('error', d.message ?? '执行失败')
          pushOperation({
            kind: 'error',
            title: '执行错误',
            status: 'failed',
            stage: f.stage,
            message: d.message,
            raw: f,
          })
          break
      }
    }

    const onTaskFrame = (f: TypedFrame) => {
      const d = (f.data ?? {}) as Record<string, any>
      switch (f.type) {
        case 'device_ready':
          pushLog('info', d.wake_ok ? '设备已自动唤醒' : '已尝试自动唤醒设备')
          pushOperation({
            kind: 'system',
            title: '设备准备',
            status: d.wake_ok ? 'ok' : 'info',
            eventType: f.type,
            message: d.wake_ok ? '设备已自动唤醒' : '已尝试自动唤醒设备',
            raw: f,
          })
          break
        case 'task_start':
          planIdRef.current = d.task_id
          pushLog('info', d.mode === 'compiled_tools' ? '任务开始执行（快速工具）…' : '任务开始执行…')
          pushOperation({
            kind: 'system',
            title: d.mode === 'compiled_tools' ? '快速工具任务启动' : '单步任务启动',
            status: 'active',
            eventType: f.type,
            message: d.task,
            raw: f,
          })
          break
        case 'thinking':
          if (d.chunk) appendThinking(String(d.chunk))
          break
        case 'step':
          pushLog('step', formatStepText(d.step, d.action, d.message), d.screenshot)
          pushOperation({
            kind: 'step',
            title: `执行步骤 ${d.step ?? '?'}`,
            status: d.success === false ? 'failed' : 'ok',
            eventType: f.type,
            message: d.message,
            actionText: formatAction(d.action),
            timings: d.timings,
            batchResults: d.batch_results,
            screenshot: d.screenshot,
            raw: f,
          })
          break
        case 'done':
          pushLog('info', d.message ?? '任务完成')
          pushOperation({
            kind: 'done',
            title: '任务完成',
            status: d.success === false ? 'failed' : 'ok',
            eventType: f.type,
            message: d.message,
            raw: f,
          })
          message.success('AI 任务完成')
          break
        case 'cancelled':
          pushLog('info', '任务已取消')
          pushOperation({
            kind: 'system',
            title: '任务取消',
            status: 'info',
            eventType: f.type,
            raw: f,
          })
          break
        case 'error':
          pushLog('error', d.message ?? '执行失败')
          pushOperation({
            kind: 'error',
            title: '执行错误',
            status: 'failed',
            eventType: f.type,
            message: d.message,
            raw: f,
          })
          break
      }
    }

    try {
      if (agentMode === 'single') {
        await agentTask({ device_id: deviceId, task: goal.trim() }, onTaskFrame, ctrl.signal)
      } else {
        await runPlanned({ device_id: deviceId, goal: goal.trim(), screen_aware: true }, onFrame, ctrl.signal)
      }
    } catch (e) {
      if (!ctrl.signal.aborted) pushLog('error', e instanceof Error ? e.message : '任务流中断')
    } finally {
      setRunning(false)
      agentAbort.current = null
    }
  }

  const handleCancel = async () => {
    if (planIdRef.current) {
      try {
        await cancelAgent(planIdRef.current)
      } catch {
        /* ignore */
      }
    }
    agentAbort.current?.abort()
    setRunning(false)
    message.info('已发送取消指令')
  }

  // ---------------- 话术辅助 ----------------
  const [myBg, setMyBg] = useState(() => localStorage.getItem(BG_KEY) ?? '')
  const [contactId, setContactId] = useState('')
  const [analysis, setAnalysis] = useState('')
  const [suggestion, setSuggestion] = useState('')
  const [suggestStatus, setSuggestStatus] = useState('')
  const [suggesting, setSuggesting] = useState(false)
  const [sending, setSending] = useState(false)
  const suggestAbort = useRef<AbortController | null>(null)

  useEffect(() => {
    localStorage.setItem(BG_KEY, myBg)
  }, [myBg])

  const handleSuggest = async () => {
    if (suggesting) return
    setSuggesting(true)
    setSuggestion('')
    setAnalysis('')
    setSuggestStatus('读屏中…')
    const ctrl = new AbortController()
    suggestAbort.current = ctrl

    const onFrame = (f: StageFrame) => {
      const d = f.data as any
      switch (f.stage) {
        case 'reading':
          setSuggestStatus('读屏中…')
          break
        case 'screen':
          setAnalysis(d?.analysis ?? '')
          setSuggestStatus('生成话术中…')
          break
        case 'generating':
          setSuggestStatus('生成话术中…')
          break
        case 'skill':
          setSuggestStatus(`命中技能：${d?.tool ?? ''}`)
          break
        case 'suggestion_chunk':
          setSuggestion((prev) => prev + (typeof d === 'string' ? d : ''))
          break
        case 'done':
          if (d?.suggestions) setSuggestion(d.suggestions)
          setSuggestStatus('已生成')
          break
        case 'error':
          setSuggestStatus(`失败：${d?.message ?? ''}`)
          break
      }
    }

    try {
      await suggestChat(
        { device_id: deviceId, my_background: myBg, contact_id: contactId || undefined },
        onFrame,
        ctrl.signal,
      )
    } catch (e) {
      if (!ctrl.signal.aborted) setSuggestStatus(e instanceof Error ? e.message : '生成失败')
    } finally {
      setSuggesting(false)
      suggestAbort.current = null
    }
  }

  const handleSend = async () => {
    if (!suggestion.trim()) return
    setSending(true)
    try {
      await sendChat({ device_id: deviceId, text: suggestion.trim() })
      message.success('已输入到设备，请在画面点击发送')
    } catch (e) {
      message.error(e instanceof Error ? e.message : '发送失败')
    } finally {
      setSending(false)
    }
  }

  const handleFetchLatest = async () => {
    const key = contactId || `device:${deviceId}`
    try {
      const doc = await getSuggestion(key)
      setSuggestion(doc.suggestions || '')
      setAnalysis(doc.screen_analysis || '')
      setSuggestStatus('已载入最新建议快照')
    } catch (e) {
      if (e instanceof MobileError && e.status === 404) setSuggestStatus('暂无历史建议')
      else message.error(e instanceof Error ? e.message : '获取失败')
    }
  }

  // ---------------- 实时事件 ----------------
  const [events, setEvents] = useState<MobileEvent[]>([])
  const [subscribed, setSubscribed] = useState(true)

  useEffect(() => {
    if (!subscribed) return
    let stop = false
    const ctrl = new AbortController()
    ;(async () => {
      try {
        const recent = await getRecentEvents({ device_id: deviceId, limit: 30 })
        if (!stop) setEvents(recent.events.slice(-60))
      } catch {
        /* ignore */
      }
      let wait = 1000
      while (!stop && !ctrl.signal.aborted) {
        try {
          await subscribeEvents(
            { device_id: deviceId },
            (ev) => setEvents((prev) => [...prev.slice(-90), ev]),
            ctrl.signal,
          )
          wait = 1000
        } catch {
          if (stop || ctrl.signal.aborted) break
          await new Promise((r) => setTimeout(r, wait))
          wait = Math.min(wait * 2, 15000)
        }
      }
    })()
    return () => {
      stop = true
      ctrl.abort()
    }
  }, [deviceId, subscribed])

  useEffect(() => {
    return () => {
      agentAbort.current?.abort()
      suggestAbort.current?.abort()
    }
  }, [])

  const eventMeta = (ev: MobileEvent): { icon: ReactNode; title: string; desc: string; tone: string } => {
    const d = ev.data as any
    switch (ev.type) {
      case 'profile_updated':
        return { icon: <ProfileOutlined />, title: `画像更新 · ${d?.name ?? ev.contact_id ?? ''}`, desc: d?.summary ?? '', tone: 'info' }
      case 'suggestion':
        return { icon: <BulbOutlined />, title: '新建议', desc: (d?.suggestions ?? '').slice(0, 120), tone: 'warn' }
      case 'auto_chat':
        return {
          icon: <CommentOutlined />,
          title: `自动聊天 · ${d?.event ?? ''}`,
          desc: d?.reply || d?.suggestion || d?.message || `回合 ${d?.rounds ?? 0} · 已发 ${d?.replies_sent ?? 0}`,
          tone: 'success',
        }
      case 'auto_chat_watch':
        return { icon: <EyeOutlined />, title: `新好友监控 · ${d?.event ?? ''}`, desc: d?.contact_name ? `联系人：${d.contact_name}` : d?.message ?? '', tone: 'info' }
      default:
        return { icon: <ThunderboltOutlined />, title: ev.type, desc: '', tone: 'info' }
    }
  }

  const tabs: { key: Tab; label: string; icon: ReactNode }[] = [
    { key: 'agent', label: 'AI 操作', icon: <RobotOutlined /> },
    { key: 'assist', label: '话术辅助', icon: <MessageOutlined /> },
    { key: 'events', label: '实时事件', icon: <ThunderboltOutlined /> },
  ]

  return (
    <div className="copilot-panel glass-card">
      <div className="copilot-tabs">
        {tabs.map((t) => (
          <button
            key={t.key}
            className={`copilot-tab ${tab === t.key ? 'active' : ''}`}
            onClick={() => setTab(t.key)}
          >
            {t.icon}
            <span>{t.label}</span>
            {t.key === 'events' && events.length > 0 && <span className="tab-badge">{events.length}</span>}
          </button>
        ))}
      </div>

      <div className="copilot-body">
        {tab === 'agent' && (
          <div className="copilot-scroll">
            <Segmented
              block
              size="small"
              value={agentMode}
              onChange={(v) => setAgentMode(v as 'planned' | 'single')}
              options={[
                { label: '多步规划', value: 'planned' },
                { label: '单步任务', value: 'single' },
              ]}
            />
            <Input.TextArea
              value={goal}
              onChange={(e) => setGoal(e.target.value)}
              placeholder={
                agentMode === 'single'
                  ? '描述单步任务，优先调用本地工具'
                  : '描述一个目标，例如：打开微信给张三发消息说我晚点到'
              }
              autoSize={{ minRows: 2, maxRows: 4 }}
              disabled={running}
            />
            <div className="copilot-actions">
              <Button
                icon={<BulbOutlined />}
                loading={planning}
                onClick={handlePreview}
                disabled={running || agentMode === 'single'}
              >
                预览步骤
              </Button>
              {running ? (
                <Button danger icon={<StopOutlined />} onClick={handleCancel}>
                  停止
                </Button>
              ) : (
                <Button type="primary" icon={<ThunderboltOutlined />} onClick={handleRun} disabled={!goal.trim()}>
                  开始执行
                </Button>
              )}
            </div>

            {(operationEvents.length > 0 || running) && (
              <div className="operation-inspector">
                <div className="operation-inspector-head">
                  <div>
                    <div className="operation-inspector-title">
                      <InfoCircleOutlined /> AI 操作信息
                    </div>
                    <div className="operation-inspector-sub">
                      {running ? '正在执行' : '最近一次执行'} · {operationStats.steps} 个动作步骤
                    </div>
                  </div>
                  <Button size="small" icon={<CodeOutlined />} onClick={() => setDetailOpen(true)}>
                    详情
                  </Button>
                </div>
                <div className="operation-metrics">
                  <span>
                    <b>{operationStats.total}</b>
                    <em>事件</em>
                  </span>
                  <span>
                    <b>{operationStats.batchSteps}</b>
                    <em>批量</em>
                  </span>
                  <span className={operationStats.failures ? 'is-warn' : ''}>
                    <b>{operationStats.failures}</b>
                    <em>异常</em>
                  </span>
                  <span>
                    <b>{formatMs(operationStats.llmTotal)}</b>
                    <em>模型</em>
                  </span>
                  <span>
                    <b>{formatMs(operationStats.actionTotal)}</b>
                    <em>动作</em>
                  </span>
                </div>
              </div>
            )}

            {subtasks.length > 0 && (
              <div className="subtask-list">
                {subtasks.map((s, i) => (
                  <div key={i} className={`subtask-item is-${s.status}`}>
                    <span className="subtask-dot">
                      {s.status === 'done' ? (
                        <CheckCircleFilled />
                      ) : s.status === 'failed' ? (
                        <CloseCircleFilled />
                      ) : s.status === 'active' ? (
                        <LoadingOutlined spin />
                      ) : (
                        <span className="subtask-index">{i + 1}</span>
                      )}
                    </span>
                    <span className="subtask-text">{s.task}</span>
                  </div>
                ))}
              </div>
            )}

            {execLog.length > 0 && (
              <div className="exec-log">
                {execLog.map((e) => (
                  <div key={e.id} className={`exec-entry is-${e.kind}`}>
                    {e.kind === 'thinking' ? (
                      <details className="thinking-collapse">
                        <summary>思考信息</summary>
                        <div className="exec-entry-text">{e.text}</div>
                      </details>
                    ) : (
                      <div className="exec-entry-text">{e.text}</div>
                    )}
                    {e.shot && (
                      <img
                        className="exec-shot"
                        src={e.shot.startsWith('data:') ? e.shot : `data:image/png;base64,${e.shot}`}
                        alt="step"
                      />
                    )}
                  </div>
                ))}
                <div ref={logEndRef} />
              </div>
            )}

            {subtasks.length === 0 && execLog.length === 0 && (
              <div className="copilot-hint">
                输入目标后会优先调用本地工具，复杂界面再看屏规划执行。
              </div>
            )}
          </div>
        )}

        {tab === 'assist' && (
          <div className="copilot-scroll">
            <Input.TextArea
              value={myBg}
              onChange={(e) => setMyBg(e.target.value)}
              placeholder="我的背景（例如：我是 XX 公司商务，主打企业获客）"
              autoSize={{ minRows: 2, maxRows: 3 }}
            />
            <Input
              value={contactId}
              onChange={(e) => setContactId(e.target.value)}
              placeholder="联系人 contact_id（可选，如 wechat:张三，自动注入画像）"
              prefix={<ProfileOutlined />}
            />
            <div className="copilot-actions">
              <Button
                type="primary"
                icon={<EyeOutlined />}
                loading={suggesting}
                onClick={handleSuggest}
                style={{ flex: 1 }}
              >
                读屏生成话术
              </Button>
              <Tooltip title="读取最新建议快照（无需重新生成）">
                <Button icon={<HistoryOutlined />} onClick={handleFetchLatest}>
                  最新
                </Button>
              </Tooltip>
            </div>

            {suggestStatus && <div className="suggest-status">{suggestStatus}</div>}
            {analysis && (
              <div className="screen-analysis">
                <div className="analysis-label">
                  <EyeOutlined /> 屏幕分析
                </div>
                <div className="analysis-text">{analysis}</div>
              </div>
            )}

            <Input.TextArea
              value={suggestion}
              onChange={(e) => setSuggestion(e.target.value)}
              placeholder="生成的话术将出现在这里，可编辑后发送"
              autoSize={{ minRows: 3, maxRows: 8 }}
              className="suggestion-area"
            />
            <div className="copilot-actions">
              <Tooltip title="清空内容">
                <Button
                  icon={<ClearOutlined />}
                  onClick={() => {
                    setSuggestion('')
                    setAnalysis('')
                    setSuggestStatus('')
                  }}
                  disabled={!suggestion}
                >
                  清空
                </Button>
              </Tooltip>
              <Button
                type="primary"
                icon={<SendOutlined />}
                loading={sending}
                onClick={handleSend}
                disabled={!suggestion.trim()}
              >
                输入到设备
              </Button>
            </div>
          </div>
        )}

        {tab === 'events' && (
          <div className="copilot-scroll">
            <div className="events-toolbar">
              <span className={`events-live ${subscribed ? 'on' : ''}`}>
                <span className="events-dot" />
                {subscribed ? '实时订阅中' : '已暂停'}
              </span>
              <div className="events-toolbar-right">
                <Button size="small" type="text" icon={<ClearOutlined />} onClick={() => setEvents([])}>
                  清空
                </Button>
                <Button size="small" onClick={() => setSubscribed((v) => !v)}>
                  {subscribed ? '暂停' : '继续'}
                </Button>
              </div>
            </div>
            {events.length === 0 ? (
              <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="暂无事件，挂载自动聊天/画像后将实时推送" />
            ) : (
              <div className="events-timeline">
                {[...events].reverse().map((ev, i) => {
                  const m = eventMeta(ev)
                  return (
                    <div key={`${ev.ts}-${i}`} className={`event-item tone-${m.tone}`}>
                      <span className="event-icon">{m.icon}</span>
                      <div className="event-main">
                        <div className="event-title">
                          {m.title}
                          <span className="event-time">{relativeTime(ev.ts)}</span>
                        </div>
                        {m.desc && <div className="event-desc">{m.desc}</div>}
                      </div>
                    </div>
                  )
                })}
              </div>
            )}
          </div>
        )}
      </div>

      <Drawer
        title={
          <Space>
            <RobotOutlined />
            AI 操作详情
          </Space>
        }
        open={detailOpen}
        onClose={() => setDetailOpen(false)}
        size="large"
        className="operation-drawer"
      >
        <div className="operation-drawer-summary">
          <span>
            <b>{operationStats.total}</b>
            <em>总事件</em>
          </span>
          <span>
            <b>{operationStats.steps}</b>
            <em>动作步骤</em>
          </span>
          <span>
            <b>{operationStats.batchSteps}</b>
            <em>batch 步骤</em>
          </span>
          <span className={operationStats.failures ? 'is-warn' : ''}>
            <b>{operationStats.failures}</b>
            <em>异常</em>
          </span>
        </div>

        {operationEvents.length === 0 ? (
          <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="暂无 AI 操作记录" />
        ) : (
          <div className="operation-detail-list">
            {[...operationEvents].reverse().map((item) => (
              <div key={item.id} className={`operation-detail-item is-${item.status}`}>
                <div className="operation-detail-head">
                  <Space size={6} wrap>
                    <Tag color={statusColor[item.status]}>{statusLabel[item.status]}</Tag>
                    {item.stage && <Tag>{item.stage}</Tag>}
                    {item.eventType && <Tag>{item.eventType}</Tag>}
                    {typeof item.subtaskIndex === 'number' && <Tag>子任务 {item.subtaskIndex + 1}</Tag>}
                  </Space>
                  <span className="operation-detail-time">{relativeTime(item.time / 1000)}</span>
                </div>
                <div className="operation-detail-title">{item.title}</div>
                {item.actionText && (
                  <div className="operation-detail-line">
                    <ThunderboltOutlined /> {item.actionText}
                  </div>
                )}
                {item.message && <div className="operation-detail-message">{item.message}</div>}
                {(item.timings?.llm_ms != null || item.timings?.action_ms != null) && (
                  <div className="operation-timing-row">
                    <span>
                      <ClockCircleOutlined /> 模型 {formatMs(item.timings?.llm_ms)}
                    </span>
                    <span>动作 {formatMs(item.timings?.action_ms)}</span>
                  </div>
                )}
                {item.batchResults && item.batchResults.length > 0 && (
                  <div className="batch-result-list">
                    <div className="batch-result-title">批量动作反馈</div>
                    {item.batchResults.map((r, idx) => (
                      <div key={`${item.id}-${idx}`} className={`batch-result-item ${r.success ? 'ok' : 'bad'}`}>
                        <span>{idx + 1}</span>
                        <Text ellipsis>{formatAction(r.action)}</Text>
                        <em>{r.message || (r.success ? 'OK' : '失败')}</em>
                        {typeof r.duration_ms === 'number' && <small>{formatMs(r.duration_ms)}</small>}
                      </div>
                    ))}
                  </div>
                )}
                {item.screenshot && (
                  <img className="operation-detail-shot" src={screenshotSrc(item.screenshot)} alt="AI step" />
                )}
                <details className="operation-raw">
                  <summary>原始事件</summary>
                  <pre>{safeJson(item.raw)}</pre>
                </details>
              </div>
            ))}
          </div>
        )}
      </Drawer>
    </div>
  )
}
