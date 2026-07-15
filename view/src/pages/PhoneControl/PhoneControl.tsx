import { useState, useEffect, useCallback } from 'react'
import { Typography, Button, message, Tabs } from 'antd'
import {
  ArrowLeftOutlined,
  MobileOutlined,
  ReloadOutlined,
  WarningOutlined,
  ProfileOutlined,
  CommentOutlined,
} from '@ant-design/icons'
import { useNavigate, useSearchParams } from 'react-router-dom'
import DevicePool from './DevicePool'
import DeviceConsole from './DeviceConsole'
import MobileProfiles from '../MobileProfiles/MobileProfiles'
import MobileAutoChat from '../MobileAutoChat/MobileAutoChat'
import {
  getPool,
  autoConnect,
  acquireDevice,
  releaseDevice,
  wakeUnlockDevice,
  connectWifi,
  MobileError,
  type PoolDevice,
} from '../../services/mobileService'
import { getCurrentUser } from '../../services/authService'
import './PhoneControl.css'

const { Title, Paragraph, Text } = Typography

type View = 'pool' | 'console'
type PhoneTab = 'control' | 'profiles' | 'auto-chat'

const VIBE_KEY = 'mobile_console_vibe_mode'

const isPhoneTab = (value: string | null): value is PhoneTab =>
  value === 'control' || value === 'profiles' || value === 'auto-chat'

export default function PhoneControl() {
  const navigate = useNavigate()
  const [searchParams, setSearchParams] = useSearchParams()
  const [devices, setDevices] = useState<PoolDevice[]>([])
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [view, setView] = useState<View>('pool')
  const [selected, setSelected] = useState<PoolDevice | null>(null)
  const [autoConnecting, setAutoConnecting] = useState(false)
  const [busyId, setBusyId] = useState<string | null>(null)
  const [vibeMode, setVibeMode] = useState(() => localStorage.getItem(VIBE_KEY) === '1')

  const [me, setMe] = useState<string>(() => {
    try {
      return JSON.parse(localStorage.getItem('userInfo') || '{}').username || ''
    } catch {
      return ''
    }
  })

  const tabParam = searchParams.get('tab')
  const activeTab: PhoneTab = isPhoneTab(tabParam) ? tabParam : 'control'

  const handleTabChange = (key: string) => {
    const next = new URLSearchParams(searchParams)
    if (key === 'control') {
      next.delete('tab')
    } else {
      next.set('tab', key)
    }
    setSearchParams(next, { replace: true })
  }

  useEffect(() => {
    localStorage.setItem(VIBE_KEY, vibeMode ? '1' : '0')
  }, [vibeMode])

  useEffect(() => {
    if (!me) {
      getCurrentUser()
        .then((u) => setMe(u.username))
        .catch(() => {})
    }
  }, [me])

  const refresh = useCallback(async () => {
    setLoading(true)
    try {
      const res = await getPool()
      setDevices(res.devices)
      setError(null)
    } catch (e) {
      setError(e instanceof Error ? e.message : '获取设备池失败')
    } finally {
      setLoading(false)
    }
  }, [])

  const prepareForControl = useCallback(async (d: PoolDevice) => {
    try {
      const result = await wakeUnlockDevice(d.device_id, undefined, true)
      if (!result.ok || result.unlocked === false) {
        message.warning('设备已唤醒，但仍可能需要手动解锁')
      }
    } catch (e) {
      message.warning(e instanceof Error ? `自动唤醒解锁未完成：${e.message}` : '自动唤醒解锁未完成')
    }
  }, [])

  // 设备池视图下自动轮询占用状态
  useEffect(() => {
    if (activeTab !== 'control') return
    refresh()
    if (view !== 'pool') return
    const t = window.setInterval(refresh, 6000)
    return () => clearInterval(t)
  }, [activeTab, view, refresh])

  // 同步已选设备的最新池信息
  useEffect(() => {
    if (!selected) return
    const fresh = devices.find((d) => d.device_id === selected.device_id)
    if (fresh && (fresh.online !== selected.online || fresh.reserved !== selected.reserved)) {
      setSelected(fresh)
    }
  }, [devices, selected])

  const handleUse = async (d: PoolDevice) => {
    setBusyId(d.device_id)
    try {
      await acquireDevice(d.device_id, '云手机操控台')
      message.success(`已占用 ${d.device_id}`)
      setSelected({ ...d, reserved: true, owner: me })
      setView('console')
      void prepareForControl(d)
    } catch (e) {
      if (e instanceof MobileError && e.status === 409) {
        message.warning('该设备已被他人占用')
      } else {
        message.error(e instanceof Error ? e.message : '占用失败')
      }
      refresh()
    } finally {
      setBusyId(null)
    }
  }

  const handleEnter = (d: PoolDevice) => {
    setSelected(d)
    setView('console')
    void prepareForControl(d)
  }

  const handleRelease = async (d: PoolDevice) => {
    setBusyId(d.device_id)
    try {
      await releaseDevice(d.device_id)
      message.success('已释放设备')
    } catch (e) {
      message.error(e instanceof Error ? e.message : '释放失败')
    } finally {
      setBusyId(null)
      if (selected?.device_id === d.device_id) {
        setSelected(null)
        setView('pool')
      }
      refresh()
    }
  }

  const handleWake = async (d: PoolDevice) => {
    try {
      const result = await wakeUnlockDevice(d.device_id, undefined, true)
      if (result.ok && result.unlocked !== false) message.success('已唤醒并解锁设备')
      else message.warning('设备已唤醒，但仍可能需要手动解锁')
      window.setTimeout(refresh, 1500)
    } catch (e) {
      message.error(e instanceof Error ? e.message : '唤醒解锁失败')
    }
  }

  const handleAutoConnect = async () => {
    setAutoConnecting(true)
    try {
      const res = await autoConnect()
      const scanned = res.scan?.scanned ?? 0
      const open = res.scan?.open ?? 0
      const scanPort = res.scan?.port ?? 5555
      const pairingCandidates = res.scan?.pairing_candidates ?? res.pairing_candidates?.length ?? 0
      if (res.count > 0) {
        message.success(`自动接入完成，新增 ${res.count} 台`)
      } else if (pairingCandidates > 0) {
        message.info(`发现 ${pairingCandidates} 台 EasyTier 已入网手机，已加入设备池待配对`)
      } else if (res.scan?.enabled && scanned > 0 && open === 0) {
        message.warning(
          `EasyTier 已扫描 ${res.scan?.cidr || '虚拟网段'} 的 ${scanned} 个地址，但没有发现开放的 ADB ${scanPort} 端口`,
        )
      } else if (res.errors?.length) {
        message.warning(res.errors[0]?.message || '自动接入未发现可连接设备')
      } else {
        message.info('自动接入未发现可连接设备')
      }
      refresh()
    } catch (e) {
      message.error(e instanceof Error ? e.message : '自动接入失败')
    } finally {
      setAutoConnecting(false)
    }
  }

  const handleConnectWifi = async (ip: string, port: number) => {
    try {
      await connectWifi(ip, port)
      message.success(`已接入 ${ip}:${port}`)
      refresh()
    } catch (e) {
      message.error(e instanceof Error ? e.message : '远程接入失败')
    }
  }

  const handleBackPage = () => {
    if (window.history.length > 1) navigate(-1)
    else navigate('/dashboard')
  }

  const controlContent = (
    <div className="phone-control-panel is-control">
      {error && view === 'pool' && (
        <div className="pool-error-banner slide-up">
          <span className="pool-error-text">
            <WarningOutlined /> {error}
          </span>
          <Button size="small" icon={<ReloadOutlined />} onClick={refresh}>
            重试
          </Button>
        </div>
      )}

      {view === 'pool' || !selected ? (
        <DevicePool
          devices={devices}
          loading={loading}
          autoConnecting={autoConnecting}
          me={me}
          busyId={busyId}
          onRefresh={refresh}
          onAutoConnect={handleAutoConnect}
          onConnectWifi={handleConnectWifi}
          onUse={handleUse}
          onEnter={handleEnter}
          onRelease={handleRelease}
          onWake={handleWake}
        />
      ) : (
        <DeviceConsole
          device={selected}
          vibeMode={vibeMode}
          onVibeChange={setVibeMode}
          onBackToPool={() => setView('pool')}
          onRelease={() => handleRelease(selected)}
          releasing={busyId === selected.device_id}
        />
      )}
    </div>
  )

  const isVibeActive = activeTab === 'control' && view === 'console' && Boolean(selected) && vibeMode

  useEffect(() => {
    document.body.classList.toggle('phone-vibe-active', isVibeActive)
    return () => document.body.classList.remove('phone-vibe-active')
  }, [isVibeActive])

  return (
    <div className={`phone-control page-container fade-in ${isVibeActive ? 'is-vibe-active' : ''}`}>
      <div className="pc-header slide-up">
        <div className="pc-header-text">
          <Title level={2} className="page-title">
            <MobileOutlined /> 云手机操控台
          </Title>
          <Paragraph className="page-description">
            真实接入设备资源池 · 实时画面 · 像素级操控 · AI 规划执行与话术辅助
          </Paragraph>
        </div>
        <div className="pc-header-actions">
          <Button icon={<ArrowLeftOutlined />} onClick={handleBackPage}>
            返回页面
          </Button>
        </div>
        {view === 'console' && selected && activeTab === 'control' && (
          <div className="pc-header-device">
            <div className="pc-header-chip">
              <span
                className="pc-header-dot"
                style={{ background: selected.online ? '#52c41a' : '#8c8c8c' }}
              />
              <MobileOutlined />
              <span className="pc-header-name">{selected.model || selected.device_id}</span>
              <Text type="secondary" className="pc-header-id">
                {selected.device_id}
              </Text>
              <span className={`pc-header-state ${selected.online ? 'on' : 'off'}`}>
                {selected.online ? '在线' : '离线'}
              </span>
            </div>
          </div>
        )}
      </div>

      <Tabs
        activeKey={activeTab}
        onChange={handleTabChange}
        className="phone-control-tabs"
        items={[
          {
            key: 'control',
            label: (
              <span>
                <MobileOutlined /> 手机操控
              </span>
            ),
            children: controlContent,
          },
          {
            key: 'profiles',
            label: (
              <span>
                <ProfileOutlined /> 人物画像
              </span>
            ),
            children: <MobileProfiles embedded />,
          },
          {
            key: 'auto-chat',
            label: (
              <span>
                <CommentOutlined /> 自动聊天
              </span>
            ),
            children: <MobileAutoChat embedded />,
          },
        ]}
      />
    </div>
  )
}
