import { useEffect, useState } from 'react'
import { Button, Modal, Input, Tooltip, Switch, message } from 'antd'
import {
  ArrowLeftOutlined,
  EditOutlined,
  AppstoreAddOutlined,
  BulbOutlined,
  HeartOutlined,
  ThunderboltOutlined,
  StopOutlined,
  DisconnectOutlined,
  MobileOutlined,
  PaperClipOutlined,
} from '@ant-design/icons'
import DeviceScreen from './DeviceScreen'
import CopilotPanel from './CopilotPanel'
import DeviceTransferDrawer from './DeviceTransferDrawer'
import {
  getHealth,
  getCurrentApp,
  inputText,
  launchApp,
  wakeUnlockDevice,
  stayAwake,
  videoReset,
  type PoolDevice,
  type DeviceHealth,
} from '../../services/mobileService'

interface Props {
  device: PoolDevice
  vibeMode?: boolean
  onVibeChange?: (on: boolean) => void
  onBackToPool?: () => void
  onRelease?: () => void
  releasing?: boolean
}

const HEALTH_PILLS: { key: keyof DeviceHealth; label: string }[] = [
  { key: 'online', label: '在线' },
  { key: 'screenshot_ready', label: '画面' },
  { key: 'input_ready', label: '输入' },
  { key: 'current_app_ready', label: '应用' },
]

/** 常用应用快捷启动（执行层按名称智能匹配并打开，§4 launch） */
const QUICK_APPS = ['微信', 'QQ', '微博', '抖音', '支付宝', '设置']

const VIBE_KEY = 'mobile_console_vibe_mode'

export default function DeviceConsole({
  device,
  vibeMode,
  onVibeChange,
  onBackToPool,
  onRelease,
  releasing = false,
}: Props) {
  const [health, setHealth] = useState<DeviceHealth | null>(null)
  const [currentApp, setCurrentApp] = useState<string>('')
  const [lastAction, setLastAction] = useState<string>('')

  const [textOpen, setTextOpen] = useState(false)
  const [textVal, setTextVal] = useState('')
  const [appOpen, setAppOpen] = useState(false)
  const [transferOpen, setTransferOpen] = useState(false)
  const [appVal, setAppVal] = useState('')
  const [busy, setBusy] = useState(false)
  const [stayOn, setStayOn] = useState(false)
  const [localVibeMode, setLocalVibeMode] = useState(() => localStorage.getItem(VIBE_KEY) === '1')
  const activeVibeMode = vibeMode ?? localVibeMode

  useEffect(() => {
    localStorage.setItem(VIBE_KEY, activeVibeMode ? '1' : '0')
  }, [activeVibeMode])

  useEffect(() => {
    let stop = false
    const load = async () => {
      try {
        const h = await getHealth(device.device_id)
        if (!stop) setHealth(h)
      } catch {
        /* ignore */
      }
      try {
        const a = await getCurrentApp(device.device_id)
        if (!stop) setCurrentApp(a.current_app)
      } catch {
        /* ignore */
      }
    }
    load()
    const t = window.setInterval(load, 8000)
    return () => {
      stop = true
      clearInterval(t)
    }
  }, [device.device_id])

  const submitText = async () => {
    if (!textVal.trim()) return
    setBusy(true)
    try {
      await inputText(device.device_id, textVal)
      message.success('文本已输入到设备')
      setTextOpen(false)
      setTextVal('')
    } catch (e) {
      message.error(e instanceof Error ? e.message : '输入失败')
    } finally {
      setBusy(false)
    }
  }

  const submitApp = async () => {
    if (!appVal.trim()) return
    setBusy(true)
    try {
      await launchApp(device.device_id, appVal.trim())
      message.success(`正在启动「${appVal.trim()}」`)
      setAppOpen(false)
      setAppVal('')
    } catch (e) {
      message.error(e instanceof Error ? e.message : '启动失败')
    } finally {
      setBusy(false)
    }
  }

  const quickLaunch = async (name: string) => {
    try {
      await launchApp(device.device_id, name)
      message.success(`正在启动「${name}」`)
      setLastAction(`启动 ${name}`)
    } catch (e) {
      message.error(e instanceof Error ? e.message : '启动失败')
    }
  }

  const handleWake = async () => {
    try {
      const result = await wakeUnlockDevice(device.device_id, undefined, true)
      setStayOn(true)
      if (result.ok && result.unlocked !== false) message.success('已唤醒并解锁设备')
      else message.warning('设备已唤醒，但仍可能需要手动解锁')
    } catch (e) {
      message.error(e instanceof Error ? e.message : '唤醒解锁失败')
    }
  }

  const handleStayAwake = async (on: boolean) => {
    setStayOn(on)
    try {
      await stayAwake(device.device_id, on)
      message.success(on ? '已开启充电常亮' : '已关闭充电常亮')
    } catch (e) {
      setStayOn(!on)
      message.error(e instanceof Error ? e.message : '设置失败')
    }
  }

  const handleVideoReset = async () => {
    try {
      await videoReset(device.device_id)
      message.success('已停止该设备的视频流')
    } catch (e) {
      message.error(e instanceof Error ? e.message : '操作失败')
    }
  }

  const toggleVibeMode = () => {
    const next = !activeVibeMode
    setLocalVibeMode(next)
    onVibeChange?.(next)
  }

  const consoleToolbar = (
    <div className="console-toolbar glass-card">
      <div className="console-device-strip">
        <div className="console-device-main">
          <span
            className="console-device-dot"
            style={{ background: device.online ? '#52c41a' : '#8c8c8c' }}
          />
          <MobileOutlined />
          <div className="console-device-text">
            <span className="console-device-name">{device.model || device.device_id}</span>
            <span className="console-device-id">{device.device_id}</span>
          </div>
        </div>
        <div className="console-device-actions">
          {onBackToPool && (
            <Button icon={<ArrowLeftOutlined />} onClick={onBackToPool}>
              返回设备池
            </Button>
          )}
          {onRelease && (
            <Button danger icon={<DisconnectOutlined />} loading={releasing} onClick={onRelease}>
              释放
            </Button>
          )}
        </div>
      </div>

      <Tooltip title="切换沉浸操控布局">
        <Button
          type={activeVibeMode ? 'primary' : 'default'}
          icon={<ThunderboltOutlined />}
          className={`vibe-toggle ${activeVibeMode ? 'is-active' : ''}`}
          onClick={toggleVibeMode}
        >
          <span className="vibe-toggle-text">{activeVibeMode ? '退出 Vibe' : '进入 Vibe'}</span>
        </Button>
      </Tooltip>

      <div className="health-pills">
        {HEALTH_PILLS.map((p) => {
          const ok = health ? Boolean(health[p.key]) : undefined
          return (
            <span
              key={p.key as string}
              className={`health-pill ${ok === undefined ? 'unknown' : ok ? 'ok' : 'bad'}`}
            >
              <span className="health-dot" />
              {p.label}
            </span>
          )
        })}
        {health?.capture_failed && (
          <Tooltip title={health.error || '设备截屏失败 / 无响应（可能已超时）'}>
            <span className="health-pill bad">
              <span className="health-dot" /> 截屏异常
            </span>
          </Tooltip>
        )}
        {currentApp && (
          <span className="health-pill app">
            <AppstoreAddOutlined /> {currentApp}
          </span>
        )}
        {lastAction && <span className="last-action">最近：{lastAction}</span>}
      </div>

      <div className="console-actions">
        <Tooltip title="向设备输入文本">
          <Button icon={<EditOutlined />} onClick={() => setTextOpen(true)}>
            输入文本
          </Button>
        </Tooltip>
        <Tooltip title="上传图片、音频或附件到手机">
          <Button icon={<PaperClipOutlined />} onClick={() => setTransferOpen(true)}>
            传文件
          </Button>
        </Tooltip>
        <Tooltip title="启动指定 App">
          <Button icon={<AppstoreAddOutlined />} onClick={() => setAppOpen(true)}>
            启动应用
          </Button>
        </Tooltip>
        <Tooltip title="亮屏、解锁并保持充电常亮">
          <Button icon={<BulbOutlined />} onClick={handleWake}>
            唤醒并解锁
          </Button>
        </Tooltip>
        <Tooltip title="充电常亮开关">
          <span className="stay-awake-switch">
            <Switch size="small" checked={stayOn} onChange={handleStayAwake} /> 常亮
          </span>
        </Tooltip>
        <Tooltip title="停止该设备视频流（释放编码器）">
          <Button icon={<StopOutlined />} onClick={handleVideoReset}>
            停止流
          </Button>
        </Tooltip>
      </div>

      <div className="console-quicklaunch">
        <span className="quicklaunch-label">
          <AppstoreAddOutlined /> 快捷启动
        </span>
        <div className="quicklaunch-chips">
          {QUICK_APPS.map((name) => (
            <button key={name} className="quicklaunch-chip" onClick={() => quickLaunch(name)}>
              {name}
            </button>
          ))}
        </div>
      </div>
    </div>
  )

  return (
    <div className={`console-wrap ${activeVibeMode ? 'is-vibe' : ''}`}>
      <div className="control-workspace slide-up stagger-1">
        <div className="console-side">
          {consoleToolbar}
          <CopilotPanel deviceId={device.device_id} />
        </div>
        <DeviceScreen
          deviceId={device.device_id}
          online={device.online}
          currentApp={currentApp}
          onActivity={setLastAction}
        />
      </div>

      <Modal
        title={
          <span>
            <EditOutlined /> 输入文本
          </span>
        }
        open={textOpen}
        onCancel={() => setTextOpen(false)}
        onOk={submitText}
        confirmLoading={busy}
        okText="输入到设备"
        cancelText="取消"
      >
        <Input.TextArea
          value={textVal}
          onChange={(e) => setTextVal(e.target.value)}
          placeholder="输入要发送到设备当前输入框的文本"
          autoSize={{ minRows: 3, maxRows: 6 }}
          autoFocus
        />
        <div className="modal-hint">
          <ThunderboltOutlined /> 文本会输入到设备当前聚焦的输入框，发送动作请在画面上点击。
        </div>
      </Modal>

      <Modal
        title={
          <span>
            <AppstoreAddOutlined /> 启动应用
          </span>
        }
        open={appOpen}
        onCancel={() => setAppOpen(false)}
        onOk={submitApp}
        confirmLoading={busy}
        okText="启动"
        cancelText="取消"
      >
        <Input
          value={appVal}
          onChange={(e) => setAppVal(e.target.value)}
          placeholder="应用名称，例如：WeChat / 微信 / Settings"
          onPressEnter={submitApp}
          prefix={<HeartOutlined />}
          autoFocus
        />
        <div className="modal-hint">
          <ThunderboltOutlined /> 支持中文/英文应用名，由执行层智能匹配并打开。
        </div>
      </Modal>

      <DeviceTransferDrawer
        deviceId={device.device_id}
        open={transferOpen}
        onClose={() => setTransferOpen(false)}
      />
    </div>
  )
}
