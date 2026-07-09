import { useEffect, useMemo, useState, type ReactNode } from 'react'
import {
  Button,
  Input,
  Segmented,
  Tooltip,
  Empty,
  Modal,
  Spin,
  Dropdown,
  QRCode,
  Typography,
  Select,
  Tag,
  ColorPicker,
  Popconfirm,
  message,
  type MenuProps,
} from 'antd'
import {
  MobileOutlined,
  SearchOutlined,
  ReloadOutlined,
  ThunderboltOutlined,
  ApiOutlined,
  UsbOutlined,
  WifiOutlined,
  CloudServerOutlined,
  PlayCircleOutlined,
  LoginOutlined,
  DisconnectOutlined,
  LockFilled,
  PoweroffOutlined,
  GlobalOutlined,
  MoreOutlined,
  SwapOutlined,
  DeleteOutlined,
  CheckCircleFilled,
  EditOutlined,
  TagsOutlined,
  FolderOutlined,
  PlusOutlined,
  CopyOutlined,
  LinkOutlined,
  WarningOutlined,
  QrcodeOutlined,
} from '@ant-design/icons'
import {
  relativeTime,
  getOverview,
  getDevices,
  remoteDiscover,
  remoteAdd,
  remoteRemove,
  usbToWifi,
  disconnectDevice,
  listGroups,
  createGroup,
  deleteGroup,
  updateDeviceMeta,
  getEasyTierAccess,
  pairAdbWithCode,
  connectAdbWireless,
  startAdbQrPairing,
  completeAdbQrPairing,
  wakeUnlockDevice,
  type PoolDevice,
  type Overview,
  type SimpleDevice,
  type DeviceGroup,
  type EasyTierAccessProfile,
  type AdbPairQrSession,
} from '../../services/mobileService'
import { downloadWithAuth } from '../../services/http'

const { Text } = Typography

type Filter = 'all' | 'free' | 'reserved' | 'offline'

const GROUP_COLORS = ['#10b981', '#1677ff', '#722ed1', '#fa8c16', '#eb2f96', '#13c2c2', '#f5222d', '#52c41a']

/** 设备显示名优先级：meta.display_name → model → device_id */
function deviceLabel(d: PoolDevice): string {
  return d.meta?.display_name || d.model || d.device_id
}

function suggestAdbPairIp(profile: EasyTierAccessProfile | null): string {
  const cidrIp = profile?.phone_ipv4_cidr?.split('/')[0]
  if (!cidrIp) return ''
  const parts = cidrIp.split('.').map((part) => Number(part))
  if (parts.length !== 4 || parts.some((part) => Number.isNaN(part))) return ''
  if (parts[3] === 0) return `${parts[0]}.${parts[1]}.${parts[2]}.2`
  return cidrIp
}

interface Props {
  devices: PoolDevice[]
  loading: boolean
  autoConnecting: boolean
  me: string
  busyId: string | null
  onRefresh: () => void
  onAutoConnect: () => void
  onConnectWifi: (ip: string, port: number) => void
  onUse: (d: PoolDevice) => void
  onEnter: (d: PoolDevice) => void
  onRelease: (d: PoolDevice) => void
  onWake: (d: PoolDevice) => void
}

function connMeta(type?: string | null): { icon: ReactNode; label: string } {
  switch ((type || '').toLowerCase()) {
    case 'usb':
      return { icon: <UsbOutlined />, label: 'USB' }
    case 'wifi':
      return { icon: <WifiOutlined />, label: 'WiFi' }
    case 'remote':
      return { icon: <CloudServerOutlined />, label: '远程' }
    case 'easytier':
      return { icon: <GlobalOutlined />, label: 'EasyTier' }
    default:
      return { icon: <ApiOutlined />, label: type || '未知' }
  }
}

export default function DevicePool({
  devices,
  loading,
  autoConnecting,
  me,
  busyId,
  onRefresh,
  onAutoConnect,
  onConnectWifi,
  onUse,
  onEnter,
  onRelease,
  onWake,
}: Props) {
  const [filter, setFilter] = useState<Filter>('all')
  const [search, setSearch] = useState('')
  const [wifiOpen, setWifiOpen] = useState(false)
  const [ip, setIp] = useState('')
  const [port, setPort] = useState('5555')
  const [overview, setOverview] = useState<Overview | null>(null)
  const [localCount, setLocalCount] = useState(0)
  const [remoteOpen, setRemoteOpen] = useState(false)
  const [remoteBaseUrl, setRemoteBaseUrl] = useState('')
  const [discovered, setDiscovered] = useState<SimpleDevice[]>([])
  const [discovering, setDiscovering] = useState(false)
  const [easytierOpen, setEasytierOpen] = useState(false)
  const [easytier, setEasytier] = useState<EasyTierAccessProfile | null>(null)
  const [easytierLoading, setEasytierLoading] = useState(false)
  const [adbPairOpen, setAdbPairOpen] = useState(false)
  const [adbPairMode, setAdbPairMode] = useState<'code' | 'qr'>('code')
  const [adbPairIp, setAdbPairIp] = useState('')
  const [adbPairPort, setAdbPairPort] = useState('')
  const [adbPairCode, setAdbPairCode] = useState('')
  const [adbConnectPort, setAdbConnectPort] = useState('')
  const [adbPairLoading, setAdbPairLoading] = useState(false)
  const [adbPairResult, setAdbPairResult] = useState('')
  const [adbQrSession, setAdbQrSession] = useState<AdbPairQrSession | null>(null)
  const [unlockDevice, setUnlockDevice] = useState<PoolDevice | null>(null)
  const [unlockPin, setUnlockPin] = useState('')
  const [unlocking, setUnlocking] = useState(false)

  // v3 §13b：设备分组 + 元数据
  const [groups, setGroups] = useState<DeviceGroup[]>([])
  const [groupFilter, setGroupFilter] = useState<string>('all') // 'all' | 'ungrouped' | group_id
  const [groupMgrOpen, setGroupMgrOpen] = useState(false)
  const [newGroupName, setNewGroupName] = useState('')
  const [newGroupColor, setNewGroupColor] = useState(GROUP_COLORS[0])
  const [creatingGroup, setCreatingGroup] = useState(false)
  // 设备元数据编辑
  const [metaDevice, setMetaDevice] = useState<PoolDevice | null>(null)
  const [metaForm, setMetaForm] = useState<{ display_name: string; note: string; tags: string[]; group_id: string | null }>(
    { display_name: '', note: '', tags: [], group_id: null },
  )
  const [savingMeta, setSavingMeta] = useState(false)

  const loadGroups = () => {
    listGroups()
      .then((r) => setGroups(r.groups || []))
      .catch(() => {})
  }

  const copyText = async (text: string, label = '内容') => {
    try {
      await navigator.clipboard.writeText(text)
      message.success(`${label}已复制`)
    } catch {
      message.error('复制失败')
    }
  }

  const downloadArtifact = async (url: string, filename: string) => {
    try {
      await downloadWithAuth(url, filename)
    } catch (e) {
      message.error(e instanceof Error ? e.message : '下载失败')
    }
  }

  const downloadTextFile = (content: string, filename: string, mime = 'text/plain;charset=utf-8') => {
    const blob = new Blob([content], { type: mime })
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = filename
    document.body.appendChild(a)
    a.click()
    a.remove()
    URL.revokeObjectURL(url)
  }

  const loadEasyTierAccess = async () => {
    setEasytierLoading(true)
    try {
      const profile = await getEasyTierAccess()
      setEasytier(profile)
    } catch (e) {
      message.error(e instanceof Error ? e.message : '获取组网配置失败')
    } finally {
      setEasytierLoading(false)
    }
  }

  const openAdbPairing = (device?: PoolDevice) => {
    const deviceIp =
      device?.network_ip ||
      (device?.device_id?.startsWith('easytier:') ? device.device_id.replace(/^easytier:/, '') : '')
    setAdbPairIp(deviceIp || suggestAdbPairIp(easytier))
    setAdbPairPort('')
    setAdbPairCode('')
    setAdbConnectPort('')
    setAdbPairResult('')
    setAdbQrSession(null)
    setAdbPairOpen(true)
  }

  const submitAdbPairCode = async () => {
    const pairingPort = Number(adbPairPort)
    const connectPort = adbConnectPort.trim() ? Number(adbConnectPort) : undefined
    if (!adbPairIp.trim() || !pairingPort || !adbPairCode.trim()) {
      message.warning('请填写手机 IP、配对端口和配对码')
      return
    }
    setAdbPairLoading(true)
    try {
      const res = await pairAdbWithCode(adbPairIp.trim(), pairingPort, adbPairCode.trim(), connectPort)
      setAdbPairResult(JSON.stringify(res, null, 2))
      if (res.ok && connectPort) {
        message.success('ADB 配对完成')
        onRefresh()
      } else if (res.pair?.ok && !connectPort) {
        message.info('ADB 已配对；请填写无线调试主页面的连接端口后点击“仅连接”')
      } else {
        message.warning(res.pair?.stderr || res.pair?.stdout || res.message || 'ADB 配对未完成')
      }
    } catch (e) {
      message.error(e instanceof Error ? e.message : 'ADB 配对失败')
    } finally {
      setAdbPairLoading(false)
    }
  }

  const submitAdbConnect = async () => {
    const port = Number(adbConnectPort)
    if (!adbPairIp.trim() || !port) {
      message.warning('请填写手机 IP 和连接端口')
      return
    }
    setAdbPairLoading(true)
    try {
      const res = await connectAdbWireless(adbPairIp.trim(), port)
      setAdbPairResult(JSON.stringify(res, null, 2))
      if (res.ok) {
        message.success('ADB 已连接')
        onRefresh()
      } else {
        message.warning(res.stderr || res.stdout || 'ADB 连接未完成')
      }
    } catch (e) {
      message.error(e instanceof Error ? e.message : 'ADB 连接失败')
    } finally {
      setAdbPairLoading(false)
    }
  }

  const createAdbQrSession = async () => {
    setAdbPairLoading(true)
    try {
      const session = await startAdbQrPairing()
      setAdbQrSession(session)
      setAdbPairResult('')
    } catch (e) {
      message.error(e instanceof Error ? e.message : '二维码生成失败')
    } finally {
      setAdbPairLoading(false)
    }
  }

  const completeAdbQrSession = async () => {
    if (!adbQrSession) return
    setAdbPairLoading(true)
    try {
      const res = await completeAdbQrPairing(adbQrSession.service_name, adbQrSession.password, 20)
      setAdbPairResult(JSON.stringify(res, null, 2))
      if (res.ok) {
        message.success('二维码配对完成')
        onRefresh()
      } else {
        message.warning(res.message || res.pair?.stderr || '未发现二维码配对服务')
      }
    } catch (e) {
      message.error(e instanceof Error ? e.message : '二维码配对失败')
    } finally {
      setAdbPairLoading(false)
    }
  }

  useEffect(() => {
    getOverview().then(setOverview).catch(() => {})
    getDevices().then((r) => setLocalCount(r.devices.length)).catch(() => {})
    loadGroups()
  }, [])

  const counts = useMemo(() => {
    const c = { all: devices.length, free: 0, reserved: 0, offline: 0 }
    for (const d of devices) {
      if (d.pairing_required) c.free += 1
      else if (!d.online) c.offline += 1
      else if (d.reserved) c.reserved += 1
      else c.free += 1
    }
    return c
  }, [devices])

  const groupCounts = useMemo(() => {
    const m = new Map<string, number>()
    let ungrouped = 0
    for (const d of devices) {
      const gid = d.meta?.group_id
      if (gid) m.set(gid, (m.get(gid) ?? 0) + 1)
      else ungrouped += 1
    }
    return { byId: m, ungrouped }
  }, [devices])

  const filtered = useMemo(() => {
    const kw = search.trim().toLowerCase()
    return devices.filter((d) => {
      const matchFilter =
        filter === 'all' ||
        (filter === 'free' && ((d.online && !d.reserved) || d.pairing_required)) ||
        (filter === 'reserved' && d.reserved) ||
        (filter === 'offline' && !d.online && !d.pairing_required)
      const matchGroup =
        groupFilter === 'all' ||
        (groupFilter === 'ungrouped' && !d.meta?.group_id) ||
        d.meta?.group_id === groupFilter
      const matchKw =
        !kw ||
        d.device_id.toLowerCase().includes(kw) ||
        (d.network_ip || '').toLowerCase().includes(kw) ||
        (d.model || '').toLowerCase().includes(kw) ||
        (d.owner || '').toLowerCase().includes(kw) ||
        (d.meta?.display_name || '').toLowerCase().includes(kw) ||
        (d.meta?.tags || []).some((t) => t.toLowerCase().includes(kw))
      return matchFilter && matchGroup && matchKw
    })
  }, [devices, filter, groupFilter, search])

  const openMetaEditor = (d: PoolDevice) => {
    setMetaDevice(d)
    setMetaForm({
      display_name: d.meta?.display_name || '',
      note: d.meta?.note || '',
      tags: d.meta?.tags || [],
      group_id: d.meta?.group_id ?? null,
    })
  }

  const handleSaveMeta = async () => {
    if (!metaDevice) return
    setSavingMeta(true)
    try {
      await updateDeviceMeta(metaDevice.device_id, {
        display_name: metaForm.display_name.trim() || null,
        note: metaForm.note.trim() || null,
        tags: metaForm.tags,
        group_id: metaForm.group_id,
      })
      message.success('设备信息已更新')
      setMetaDevice(null)
      onRefresh()
      loadGroups()
    } catch (e) {
      message.error(e instanceof Error ? e.message : '保存失败')
    } finally {
      setSavingMeta(false)
    }
  }

  const handleCreateGroup = async () => {
    if (!newGroupName.trim()) return
    setCreatingGroup(true)
    try {
      await createGroup(newGroupName.trim(), newGroupColor)
      message.success('分组已创建')
      setNewGroupName('')
      loadGroups()
    } catch (e) {
      message.error(e instanceof Error ? e.message : '创建失败')
    } finally {
      setCreatingGroup(false)
    }
  }

  const handleDeleteGroup = async (groupId: string) => {
    try {
      await deleteGroup(groupId)
      message.success('分组已删除，组内设备自动解绑')
      if (groupFilter === groupId) setGroupFilter('all')
      loadGroups()
      onRefresh()
    } catch (e) {
      message.error(e instanceof Error ? e.message : '删除失败')
    }
  }

  const groupName = (gid?: string | null) => groups.find((g) => g.group_id === gid)?.name
  const groupColor = (gid?: string | null) => groups.find((g) => g.group_id === gid)?.color || undefined

  const submitWifi = () => {
    if (!ip.trim()) return
    onConnectWifi(ip.trim(), Number(port) || 5555)
    setWifiOpen(false)
    setIp('')
  }

  const openEasyTier = () => {
    setEasytierOpen(true)
    if (!easytier) void loadEasyTierAccess()
  }

  const handleDiscover = async () => {
    if (!remoteBaseUrl.trim()) return
    setDiscovering(true)
    try {
      const res = await remoteDiscover(remoteBaseUrl.trim())
      setDiscovered(res.devices || [])
      if (!res.devices?.length) message.info('该 Agent 未发现设备')
    } catch (e) {
      message.error(e instanceof Error ? e.message : '发现失败')
    } finally {
      setDiscovering(false)
    }
  }

  const handleRemoteAdd = async (deviceId: string) => {
    try {
      await remoteAdd(remoteBaseUrl.trim(), deviceId)
      message.success('已纳入设备池')
      onRefresh()
    } catch (e) {
      message.error(e instanceof Error ? e.message : '纳入失败')
    }
  }

  const handleCardAction = async (key: string, d: PoolDevice) => {
    if (key === 'meta') {
      openMetaEditor(d)
      return
    }
    try {
      if (key === 'u2w') {
        await usbToWifi(d.device_id)
        message.success('已切换到 WiFi 连接')
      } else if (key === 'disc') {
        await disconnectDevice(d.device_id)
        message.success('已断开连接')
      } else if (key === 'rm') {
        await remoteRemove(d.device_id)
        message.success('已移除远程设备')
      }
      onRefresh()
    } catch (e) {
      message.error(e instanceof Error ? e.message : '操作失败')
    }
  }

  const handleWakeUnlock = async () => {
    if (!unlockDevice) return
    setUnlocking(true)
    try {
      const res = await wakeUnlockDevice(unlockDevice.device_id, unlockPin.trim() || undefined, true)
      if (res.ok) message.success('已发送唤醒解锁指令')
      else message.warning('已发送指令，但设备可能未完成解锁')
      setUnlockDevice(null)
      setUnlockPin('')
      window.setTimeout(onRefresh, 1500)
    } catch (e) {
      message.error(e instanceof Error ? e.message : '唤醒解锁失败')
    } finally {
      setUnlocking(false)
    }
  }

  const cardMenu = (d: PoolDevice): MenuProps['items'] => {
    const items: MenuProps['items'] = [
      { key: 'meta', icon: <EditOutlined />, label: '编辑信息 / 分组' },
    ]
    const ct = (d.connection_type || '').toLowerCase()
    if (ct === 'usb') items.push({ key: 'u2w', icon: <SwapOutlined />, label: 'USB 转 WiFi' })
    if (ct === 'wifi' || ct === 'remote') items.push({ key: 'disc', icon: <DisconnectOutlined />, label: '断开连接' })
    if (ct === 'remote') items.push({ key: 'rm', icon: <DeleteOutlined />, label: '移除远程', danger: true })
    return items
  }

  return (
    <div className="device-select slide-up stagger-1">
      {overview && (
        <div className="pool-overview">
          <div className="pool-ov-item">
            <span className="pool-ov-num">
              {overview.devices?.online ?? '-'}
              <span className="pool-ov-sep">/</span>
              {overview.devices?.total ?? '-'}
            </span>
            <span className="pool-ov-label">在线 / 总数</span>
          </div>
          <div className="pool-ov-item">
            <span className="pool-ov-num">{localCount}</span>
            <span className="pool-ov-label">ADB 设备</span>
          </div>
          <div className="pool-ov-item wide">
            <span className={`pool-ov-dot ${overview.config?.llm_configured ? 'on' : 'off'}`} />
            <span className="pool-ov-label">
              LLM {overview.config?.llm_configured ? '就绪' : '未配置'}
              {overview.config?.models?.default ? ` · ${overview.config.models.default}` : ''}
            </span>
          </div>
          <div className="pool-ov-item">
            <span className="pool-ov-num">{overview.running_tasks?.length ?? 0}</span>
            <span className="pool-ov-label">运行中任务</span>
          </div>
        </div>
      )}
      <div className="device-toolbar">
        <Segmented
          value={filter}
          onChange={(v) => setFilter(v as Filter)}
          options={[
            { label: `全部 ${counts.all}`, value: 'all' },
            { label: `空闲 ${counts.free}`, value: 'free' },
            { label: `占用 ${counts.reserved}`, value: 'reserved' },
            { label: `离线 ${counts.offline}`, value: 'offline' },
          ]}
        />
        <Input
          id="device-pool-search"
          name="device_pool_search"
          allowClear
          prefix={<SearchOutlined />}
          placeholder="搜索设备 ID / 型号 / 占用人"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          className="device-search"
        />
        <div className="toolbar-spacer" />
        <Tooltip title="自动接入 mDNS 与 EasyTier 虚拟网段发现的设备">
          <Button icon={<ThunderboltOutlined />} loading={autoConnecting} onClick={onAutoConnect}>
            自动接入
          </Button>
        </Tooltip>
        <Tooltip title="接入远程 WiFi 设备">
          <Button icon={<GlobalOutlined />} onClick={() => setWifiOpen(true)}>
            远程接入
          </Button>
        </Tooltip>
        <Tooltip title="公网 EasyTier 组网配置">
          <Button icon={<GlobalOutlined />} onClick={openEasyTier}>
            公网组网
          </Button>
        </Tooltip>
        <Tooltip title="从远程 Device Agent 发现并接入">
          <Button icon={<CloudServerOutlined />} onClick={() => setRemoteOpen(true)}>
            Agent 组网
          </Button>
        </Tooltip>
        <Tooltip title="管理设备分组">
          <Button icon={<FolderOutlined />} onClick={() => setGroupMgrOpen(true)}>
            管理分组
          </Button>
        </Tooltip>
        <Tooltip title="刷新设备池">
          <Button icon={<ReloadOutlined />} loading={loading} onClick={onRefresh} />
        </Tooltip>
      </div>

      {(groups.length > 0 || groupCounts.ungrouped > 0) && (
        <div className="group-filter-bar">
          <button
            className={`group-chip ${groupFilter === 'all' ? 'active' : ''}`}
            onClick={() => setGroupFilter('all')}
          >
            <FolderOutlined /> 全部分组 <span className="group-chip-count">{devices.length}</span>
          </button>
          {groups.map((g) => {
            const cnt = groupCounts.byId.get(g.group_id) ?? 0
            return (
              <button
                key={g.group_id}
                className={`group-chip ${groupFilter === g.group_id ? 'active' : ''}`}
                onClick={() => setGroupFilter(g.group_id)}
              >
                <span className="group-chip-dot" style={{ background: g.color || 'var(--text-tertiary)' }} />
                {g.name} <span className="group-chip-count">{cnt}</span>
              </button>
            )
          })}
          {groupCounts.ungrouped > 0 && (
            <button
              className={`group-chip ${groupFilter === 'ungrouped' ? 'active' : ''}`}
              onClick={() => setGroupFilter('ungrouped')}
            >
              未分组 <span className="group-chip-count">{groupCounts.ungrouped}</span>
            </button>
          )}
        </div>
      )}

      {loading && devices.length === 0 ? (
        <div className="pool-loading">
          <Spin size="large" />
          <div className="pool-loading-text">正在加载设备池…</div>
        </div>
      ) : filtered.length === 0 ? (
        <Empty
          className="pool-empty"
          description={
            devices.length === 0
              ? '设备池为空 — 插入 USB、开启模拟器或点「自动接入」'
              : '没有匹配的设备'
          }
        >
          {devices.length === 0 && (
            <Button type="primary" icon={<ThunderboltOutlined />} loading={autoConnecting} onClick={onAutoConnect}>
              自动接入设备
            </Button>
          )}
        </Empty>
      ) : (
        <div className="device-grid">
          {filtered.map((d, idx) => {
            const conn = connMeta(d.connection_type)
            const isPairingCandidate = !!d.pairing_required
            const mine = d.reserved && d.owner === me
            const othersHold = d.reserved && d.owner !== me
            const statusClass = isPairingCandidate ? 'is-pairing' : !d.online ? 'is-offline' : othersHold ? 'is-busy' : 'is-idle'
            const isBusy = busyId === d.device_id
            return (
              <div
                key={d.device_id}
                className={`device-card glass-card hover-float ${statusClass} fade-in`}
                style={{ animationDelay: `${Math.min(idx * 0.04, 0.4)}s` }}
              >
                <div className="device-card-head">
                  <div className={`device-thumb ${d.online ? 'online' : ''} ${isPairingCandidate ? 'pairing' : ''}`}>
                    <MobileOutlined />
                    <span
                      className="device-live-dot"
                      style={{
                        background: isPairingCandidate
                          ? '#1677ff'
                          : !d.online
                            ? 'var(--text-disabled)'
                            : othersHold
                              ? '#faad14'
                              : '#52c41a',
                      }}
                    />
                  </div>
                  <div className="device-head-info">
                    <div className="device-name">{deviceLabel(d)}</div>
                    <div className="device-model">
                      {d.meta?.display_name && d.model ? `${d.model} · ` : ''}
                      {d.device_id}
                    </div>
                  </div>
                  <span className={`pool-status-tag ${statusClass}`}>
                    {isPairingCandidate ? '待配对' : !d.online ? '离线' : othersHold ? '占用中' : mine ? '我的' : '空闲'}
                  </span>
                </div>

                <div className="device-meta">
                  <div className="meta-item">
                    {conn.icon}
                    <span>{conn.label}</span>
                  </div>
                  <div className="meta-item">
                    <ApiOutlined />
                    <span>{isPairingCandidate ? d.network_ip || d.status : d.status}</span>
                  </div>
                  {isPairingCandidate ? (
                    <div className="meta-item">
                      <QrcodeOutlined />
                      <span>等待 ADB 配对</span>
                    </div>
                  ) : d.reserved ? (
                    <div className="meta-item">
                      <LockFilled style={{ color: mine ? 'var(--color-success)' : 'var(--color-warning)' }} />
                      <span>{d.owner || '已占用'}</span>
                    </div>
                  ) : (
                    <div className="meta-item">
                      <LockFilled style={{ opacity: 0.3 }} />
                      <span>未占用</span>
                    </div>
                  )}
                  <div className="meta-item">
                    <ThunderboltOutlined />
                    <span>{isPairingCandidate ? d.easytier_peer?.lat_ms ? `${d.easytier_peer.lat_ms} ms` : '已入网' : d.since ? relativeTime(d.since) : '—'}</span>
                  </div>
                </div>

                {(d.meta?.group_id || (d.meta?.tags?.length ?? 0) > 0) && (
                  <div className="device-tags">
                    {d.meta?.group_id && (
                      <span
                        className="device-group-chip"
                        style={{ borderColor: groupColor(d.meta.group_id), color: groupColor(d.meta.group_id) }}
                      >
                        <FolderOutlined /> {groupName(d.meta.group_id) || '分组'}
                      </span>
                    )}
                    {d.meta?.tags?.map((t) => (
                      <span key={t} className="device-tag-chip">
                        {t}
                      </span>
                    ))}
                  </div>
                )}

                {d.meta?.note && <div className="device-note">备注：{d.meta.note}</div>}
                {d.note && <div className="device-note occupy-note">占用：{d.note}</div>}

                <div className="device-card-foot">
                  <span className="device-owner-hint">
                    {othersHold ? (
                      <>
                        <LockFilled /> {d.owner} 操作中
                      </>
                    ) : (
                      <span />
                    )}
                  </span>
                  <div className="device-foot-actions">
                    {(cardMenu(d) || []).length > 0 && (
                      <Dropdown
                        trigger={['click']}
                        menu={{ items: cardMenu(d), onClick: ({ key }) => handleCardAction(key, d) }}
                      >
                        <Button size="small" icon={<MoreOutlined />} />
                      </Dropdown>
                    )}
                    {isPairingCandidate ? (
                      <Button
                        type="primary"
                        size="small"
                        icon={<QrcodeOutlined />}
                        onClick={() => openAdbPairing(d)}
                      >
                        配对
                      </Button>
                    ) : !d.online ? (
                      <Button size="small" icon={<PoweroffOutlined />} onClick={() => onWake(d)}>
                        唤醒
                      </Button>
                    ) : mine ? (
                      <>
                        <Tooltip title="释放设备">
                          <Button
                            size="small"
                            danger
                            icon={<DisconnectOutlined />}
                            onClick={() => onRelease(d)}
                            loading={isBusy}
                          />
                        </Tooltip>
                        <Tooltip title="唤醒亮屏并尝试解锁">
                          <Button
                            size="small"
                            icon={<PoweroffOutlined />}
                            onClick={() => setUnlockDevice(d)}
                          >
                            唤醒解锁
                          </Button>
                        </Tooltip>
                        <Button
                          type="primary"
                          size="small"
                          icon={<LoginOutlined />}
                          onClick={() => onEnter(d)}
                        >
                          进入操控
                        </Button>
                      </>
                    ) : othersHold ? (
                      <Button size="small" disabled icon={<LockFilled />}>
                        已被占用
                      </Button>
                    ) : (
                      <Button
                        type="primary"
                        size="small"
                        icon={<PlayCircleOutlined />}
                        onClick={() => onUse(d)}
                        loading={isBusy}
                      >
                        接入控制
                      </Button>
                    )}
                  </div>
                </div>
              </div>
            )
          })}
        </div>
      )}

      <Modal
        title={
          <span>
            <PoweroffOutlined /> 唤醒解锁{unlockDevice ? ` · ${deviceLabel(unlockDevice)}` : ''}
          </span>
        }
        open={!!unlockDevice}
        onCancel={() => {
          setUnlockDevice(null)
          setUnlockPin('')
        }}
        onOk={handleWakeUnlock}
        confirmLoading={unlocking}
        okText="发送指令"
        cancelText="取消"
      >
        <div className="unlock-form">
          <Input.Password
            id="device-unlock-pin"
            name="device_unlock_pin"
            value={unlockPin}
            onChange={(e) => setUnlockPin(e.target.value)}
            placeholder="可选：输入一次性锁屏 PIN，不会保存"
            maxLength={32}
          />
        </div>
        <div className="modal-hint">
          <WarningOutlined /> 仅支持已开机且 ADB/Agent 在线的设备。完全关机无法网络唤醒；强锁屏、生物识别或企业策略可能需要预授权 Agent/无障碍权限。
        </div>
      </Modal>

      <Modal
        title={
          <span>
            <GlobalOutlined /> 公网 EasyTier 手机接入
          </span>
        }
        open={easytierOpen}
        onCancel={() => setEasytierOpen(false)}
        footer={[
          <Button key="refresh" icon={<ReloadOutlined />} loading={easytierLoading} onClick={loadEasyTierAccess}>
            刷新配置
          </Button>,
          <Button key="close" type="primary" onClick={() => setEasytierOpen(false)}>
            完成
          </Button>,
        ]}
        width={760}
      >
        <Spin spinning={easytierLoading}>
          {easytier && (
            <div className="easytier-access">
              <div className="easytier-config-panel">
                <div className="easytier-config-card">
                  <div>
                    <span>网络名</span>
                    <strong>{easytier.network_name}</strong>
                  </div>
                  <div>
                    <span>公网 Peer</span>
                    <strong>{easytier.peers[0] || '未配置'}</strong>
                  </div>
                  <div>
                    <span>虚拟网段</span>
                    <strong>{easytier.virtual_cidr}</strong>
                  </div>
                  <div>
                    <span>DHCP 网段</span>
                    <strong>{easytier.phone_ipv4_cidr}</strong>
                  </div>
                  <div>
                    <span>ADB 端口</span>
                    <strong>{easytier.adb_port}</strong>
                  </div>
                </div>
                <div className="easytier-config-actions">
                  <Button
                    type="primary"
                    icon={<LinkOutlined />}
                    onClick={() => downloadTextFile(
                      easytier.config_toml,
                      easytier.config_filename || 'sere1nfish-mobile.toml',
                      'application/toml;charset=utf-8',
                    )}
                  >
                    下载 TOML 配置
                  </Button>
                  <Button icon={<CopyOutlined />} onClick={() => copyText(easytier.config_toml, 'TOML 配置')}>
                    复制 TOML 配置
                  </Button>
                  <Button icon={<CopyOutlined />} onClick={() => copyText(easytier.phone_command, '手机端命令')}>
                    复制手机命令
                  </Button>
                  <Button icon={<ApiOutlined />} onClick={() => openAdbPairing()}>
                    手动 ADB 配对
                  </Button>
                  {easytier.agent_download_url && (
                    <Button
                      icon={<LinkOutlined />}
                      onClick={() => downloadArtifact(easytier.agent_download_url, 'mobile-agent.apk')}
                    >
                      下载 Agent
                    </Button>
                  )}
                  <Button
                    icon={<LinkOutlined />}
                    onClick={() => downloadArtifact(easytier.android_download_url, 'easytier-v2.6.4-arm64.apk')}
                  >
                    EasyTier 下载
                  </Button>
                  <Button
                    icon={<LinkOutlined />}
                    onClick={() => window.open(easytier.docs_url, '_blank', 'noopener,noreferrer')}
                  >
                    共享节点文档
                  </Button>
                </div>
              </div>

              <div className="easytier-info">
                <div className="easytier-title-row">
                  <div>
                    <div className="easytier-title">手机导入配置文件后加入同一虚拟网络</div>
                    <div className="easytier-subtitle">
                      公网节点 {easytier.public_host || '未配置'} · 后端 peer {easytier.backend_peer_ipv4} · ADB {easytier.adb_port}
                    </div>
                  </div>
                  <Tag color={easytier.enabled ? 'success' : 'warning'}>
                    {easytier.enabled ? '已启用' : '未启用'}
                  </Tag>
                </div>

                {easytier.warnings.length > 0 && (
                  <div className="easytier-warnings">
                    {easytier.warnings.map((w) => (
                      <div key={w}>
                        <WarningOutlined /> {w}
                      </div>
                    ))}
                  </div>
                )}

                <div className="easytier-steps">
                  <div className="easytier-step">
                    <span>1</span>
                    <p>公网服务器运行 Compose 中的 `easytier-server`，后端 peer 加入 {easytier.virtual_cidr}，安全组放行 EasyTier 端口和 443。</p>
                  </div>
                  <div className="easytier-step">
                    <span>2</span>
                    <p>手机安装 EasyTier 与项目 Agent，优先导入下载的标注 TOML；配置使用 EasyTier DHCP，但地址限定在 {easytier.phone_ipv4_cidr} 网段内。导入成功后需要在 EasyTier App 内确认保存并启动；如果 GUI 不支持写入，用「复制 TOML」粘贴到自定义配置，或手动填写网络名、密钥、DHCP 网段和公网 peer。</p>
                  </div>
                  <div className="easytier-step">
                    <span>3</span>
                    <p>回到设备池点击「自动接入」发现设备，纳入资源池后按分组申请、唤醒、进入操控；Agent 组网和远程接入只作为兜底。</p>
                  </div>
                </div>

                <div className="easytier-command-block">
                  <div className="command-head">
                    <span>服务端命令</span>
                    <Button size="small" icon={<CopyOutlined />} onClick={() => copyText(easytier.server_command, '服务端命令')} />
                  </div>
                  <pre>{easytier.server_command}</pre>
                </div>
                <div className="easytier-command-block">
                  <div className="command-head">
                    <span>手机端命令</span>
                    <Button size="small" icon={<CopyOutlined />} onClick={() => copyText(easytier.phone_command, '手机端命令')} />
                  </div>
                  <pre>{easytier.phone_command}</pre>
                </div>

                <div className="easytier-notes">
                  <div>自动发现：{easytier.auto_scan_enabled ? '已启用 EasyTier ADB 扫描' : '仅启用 mDNS'}；后端 peer：{easytier.backend_peer_hostname} / {easytier.backend_peer_ipv4}。</div>
                  <div>资源分割：用设备分组区分客服号、内容号、测试机和保留机；接任务前必须先申请占用，释放后其他人才能使用。</div>
                  <div>唤醒边界：已开机且无线 ADB/Agent 在线时可远程亮屏并保持常亮；手机完全关机无法通过网络唤醒，锁屏解锁需要设备预先关闭锁屏密码或由 Agent 持有系统/无障碍权限执行。</div>
                </div>
              </div>
            </div>
          )}
        </Spin>
      </Modal>

      <Modal
        title={
          <span>
            <ApiOutlined /> 无线 ADB 配对
          </span>
        }
        open={adbPairOpen}
        onCancel={() => setAdbPairOpen(false)}
        footer={[
          <Button key="close" type="primary" onClick={() => setAdbPairOpen(false)}>
            完成
          </Button>,
        ]}
        width={680}
      >
        <Spin spinning={adbPairLoading}>
          <div className="adb-pair-access">
            <Segmented
              value={adbPairMode}
              onChange={(value) => setAdbPairMode(value as 'code' | 'qr')}
              options={[
                { label: '配对码', value: 'code' },
                { label: '二维码', value: 'qr' },
              ]}
            />

            {adbPairMode === 'code' ? (
              <div className="easytier-command-block">
                <div className="adb-pair-grid">
                  <label className="adb-pair-field" htmlFor="adb-pair-ip">
                    <span>手机 IP</span>
                    <Input
                      id="adb-pair-ip"
                      name="adb_pair_ip"
                      value={adbPairIp}
                      onChange={(e) => setAdbPairIp(e.target.value)}
                      placeholder="10.144.144.2"
                    />
                  </label>
                  <label className="adb-pair-field" htmlFor="adb-pair-port">
                    <span>配对端口</span>
                    <Input
                      id="adb-pair-port"
                      name="adb_pair_port"
                      value={adbPairPort}
                      onChange={(e) => setAdbPairPort(e.target.value)}
                      placeholder="手机显示的配对端口"
                    />
                  </label>
                  <label className="adb-pair-field" htmlFor="adb-pair-code">
                    <span>配对码</span>
                    <Input
                      id="adb-pair-code"
                      name="adb_pair_code"
                      value={adbPairCode}
                      onChange={(e) => setAdbPairCode(e.target.value)}
                      placeholder="6 位数字"
                    />
                  </label>
                  <label className="adb-pair-field" htmlFor="adb-connect-port">
                    <span>连接端口（无线调试主页面）</span>
                    <Input
                      id="adb-connect-port"
                      name="adb_connect_port"
                      value={adbConnectPort}
                      onChange={(e) => setAdbConnectPort(e.target.value)}
                      placeholder="可选"
                    />
                  </label>
                </div>
                <div className="easytier-config-actions">
                  <Button type="primary" icon={<CheckCircleFilled />} onClick={submitAdbPairCode}>
                    配对并连接
                  </Button>
                  <Button icon={<LinkOutlined />} onClick={submitAdbConnect}>
                    仅连接
                  </Button>
                </div>
              </div>
            ) : (
              <div className="easytier-command-block">
                <div className="easytier-config-actions">
                  <Button type="primary" icon={<QrcodeOutlined />} onClick={createAdbQrSession}>
                    生成二维码
                  </Button>
                  <Button icon={<CheckCircleFilled />} disabled={!adbQrSession} onClick={completeAdbQrSession}>
                    完成配对
                  </Button>
                  {adbQrSession && (
                    <Button icon={<CopyOutlined />} onClick={() => copyText(adbQrSession.qr_payload, 'ADB 二维码内容')}>
                      复制二维码内容
                    </Button>
                  )}
                </div>
                <div className="modal-hint">
                  <WarningOutlined /> 二维码配对依赖手机广播 mDNS。EasyTier 不转发该服务时会超时；此时请切回“配对码”模式。
                </div>
                {adbQrSession && (
                  <div className="easytier-qr-panel">
                    <QRCode value={adbQrSession.qr_payload} size={240} />
                    <pre>{adbQrSession.qr_payload}</pre>
                  </div>
                )}
              </div>
            )}

            {adbPairResult && (
              <div className="easytier-command-block">
                <div className="command-head">
                  <span>执行结果</span>
                  <Button size="small" icon={<CopyOutlined />} onClick={() => copyText(adbPairResult, '执行结果')} />
                </div>
                <pre>{adbPairResult}</pre>
              </div>
            )}
          </div>
        </Spin>
      </Modal>

      <Modal
        title={
          <span>
            <GlobalOutlined /> 接入远程设备（easytier WiFi）
          </span>
        }
        open={wifiOpen}
        onCancel={() => setWifiOpen(false)}
        onOk={submitWifi}
        okText="接入"
        cancelText="取消"
      >
        <div className="wifi-form">
          <Input
            id="wifi-device-ip"
            name="wifi_device_ip"
            value={ip}
            onChange={(e) => setIp(e.target.value)}
            placeholder="设备 IP，如 10.1.1.2"
            prefix={<ApiOutlined />}
          />
          <Input
            id="wifi-device-port"
            name="wifi_device_port"
            value={port}
            onChange={(e) => setPort(e.target.value)}
            placeholder="端口"
            style={{ maxWidth: 120 }}
          />
        </div>
        <div className="modal-hint">
          <ThunderboltOutlined /> 组网后填写 ip:port，纳入资源池后即可像本地设备一样操控。
        </div>
      </Modal>

      <Modal
        title={
          <span>
            <CloudServerOutlined /> 远程 Device Agent 组网
          </span>
        }
        open={remoteOpen}
        onCancel={() => setRemoteOpen(false)}
        footer={null}
        width={520}
      >
        <div className="remote-form">
          <Input
            id="remote-agent-base-url"
            name="remote_agent_base_url"
            value={remoteBaseUrl}
            onChange={(e) => setRemoteBaseUrl(e.target.value)}
            placeholder="Device Agent 地址，如 http://host:8787"
            prefix={<CloudServerOutlined />}
            onPressEnter={handleDiscover}
          />
          <Button type="primary" icon={<SearchOutlined />} loading={discovering} onClick={handleDiscover}>
            发现设备
          </Button>
        </div>
        {discovered.length > 0 && (
          <div className="remote-list">
            {discovered.map((d) => (
              <div key={d.device_id} className="remote-item">
                <CheckCircleFilled className="remote-ok" />
                <div className="remote-item-info">
                  <div className="remote-item-name">{d.model || d.device_id}</div>
                  <Text type="secondary" className="remote-item-id">
                    {d.device_id}
                  </Text>
                </div>
                <Button size="small" type="primary" onClick={() => handleRemoteAdd(d.device_id)}>
                  纳入池
                </Button>
              </div>
            ))}
          </div>
        )}
        <div className="modal-hint">
          <ThunderboltOutlined /> 连接远程 Device Agent，发现其暴露的设备并代理纳入本地资源池。
        </div>
      </Modal>

      <Modal
        title={
          <span>
            <EditOutlined /> 编辑设备信息{metaDevice ? ` · ${metaDevice.device_id}` : ''}
          </span>
        }
        open={!!metaDevice}
        onCancel={() => setMetaDevice(null)}
        onOk={handleSaveMeta}
        confirmLoading={savingMeta}
        okText="保存"
        cancelText="取消"
      >
        <div className="meta-form">
          <label className="field-label">显示名</label>
          <Input
            id="device-meta-display-name"
            name="device_meta_display_name"
            value={metaForm.display_name}
            onChange={(e) => setMetaForm((f) => ({ ...f, display_name: e.target.value }))}
            placeholder="自定义显示名，如 测试机A"
            prefix={<MobileOutlined />}
            maxLength={40}
          />
          <label className="field-label">分组</label>
          <Select
            value={metaForm.group_id ?? undefined}
            onChange={(v) => setMetaForm((f) => ({ ...f, group_id: v ?? null }))}
            placeholder="选择分组（可清空为未分组）"
            allowClear
            style={{ width: '100%' }}
            options={groups.map((g) => ({
              value: g.group_id,
              label: g.name,
            }))}
            notFoundContent="暂无分组，请先在「管理分组」创建"
          />
          <label className="field-label">标签</label>
          <Select
            mode="tags"
            value={metaForm.tags}
            onChange={(v) => setMetaForm((f) => ({ ...f, tags: v }))}
            placeholder="输入后回车添加，如 微信 / 一线"
            style={{ width: '100%' }}
            tokenSeparators={[',', '，', ' ']}
            suffixIcon={<TagsOutlined />}
          />
          <label className="field-label">备注</label>
          <Input.TextArea
            id="device-meta-note"
            name="device_meta_note"
            value={metaForm.note}
            onChange={(e) => setMetaForm((f) => ({ ...f, note: e.target.value }))}
            placeholder="设备备注，如 客服号"
            autoSize={{ minRows: 2, maxRows: 4 }}
            maxLength={200}
          />
        </div>
        <div className="modal-hint">
          <ThunderboltOutlined /> 信息按稳定 device_key 存储，设备掉线重连（含 WiFi 改 ip:port）也不丢失。
        </div>
      </Modal>

      <Modal
        title={
          <span>
            <FolderOutlined /> 管理设备分组
          </span>
        }
        open={groupMgrOpen}
        onCancel={() => setGroupMgrOpen(false)}
        footer={null}
        width={520}
      >
        <div className="group-create-row">
          <Input
            id="device-group-name"
            name="device_group_name"
            value={newGroupName}
            onChange={(e) => setNewGroupName(e.target.value)}
            placeholder="新分组名称，如 客服组"
            onPressEnter={handleCreateGroup}
            prefix={<FolderOutlined />}
          />
          <ColorPicker
            value={newGroupColor}
            onChange={(c) => setNewGroupColor(c.toHexString())}
            presets={[{ label: '推荐', colors: GROUP_COLORS }]}
          />
          <Button type="primary" icon={<PlusOutlined />} loading={creatingGroup} onClick={handleCreateGroup}>
            新建
          </Button>
        </div>
        {groups.length === 0 ? (
          <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="暂无分组，创建后可在设备卡片中归类" />
        ) : (
          <div className="group-list">
            {groups.map((g) => (
              <div key={g.group_id} className="group-list-item">
                <span className="group-chip-dot" style={{ background: g.color || 'var(--text-tertiary)' }} />
                <span className="group-list-name">{g.name}</span>
                <Tag bordered={false}>{(groupCounts.byId.get(g.group_id) ?? 0)} 台</Tag>
                <div className="toolbar-spacer" />
                <Popconfirm
                  title="删除该分组？"
                  description="组内设备会自动解绑（不删除设备元数据）"
                  okText="删除"
                  cancelText="取消"
                  okButtonProps={{ danger: true }}
                  onConfirm={() => handleDeleteGroup(g.group_id)}
                >
                  <Button size="small" danger icon={<DeleteOutlined />} />
                </Popconfirm>
              </div>
            ))}
          </div>
        )}
        <div className="modal-hint">
          <ThunderboltOutlined /> 在设备卡片「⋯ → 编辑信息 / 分组」里把设备归入分组；分组按设备稳定 key 关联。
        </div>
      </Modal>
    </div>
  )
}
