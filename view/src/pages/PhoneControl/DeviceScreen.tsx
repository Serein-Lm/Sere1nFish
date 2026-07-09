import { useEffect, useRef, useState, type PointerEvent } from 'react'
import { Tooltip } from 'antd'
import {
  ApiOutlined,
  AppstoreOutlined,
  LeftOutlined,
  HomeOutlined,
  ReloadOutlined,
  ThunderboltOutlined,
  LoadingOutlined,
  DisconnectOutlined,
  ExpandOutlined,
  VideoCameraOutlined,
  CameraOutlined,
  AimOutlined,
} from '@ant-design/icons'
import {
  fetchScreenshotBlob,
  tap as apiTap,
  swipe as apiSwipe,
  pressKey,
  MobileError,
} from '../../services/mobileService'
import { MobileVideoStream, isVideoStreamSupported } from '../../services/mobileVideoStream'

type ScreenStatus = 'connecting' | 'live' | 'error' | 'offline'
type StreamMode = 'video' | 'screenshot'
type Quality = 'hd' | 'balanced' | 'smooth'

/**
 * 画质 / 流畅度档位（对齐 MOBILE_API.md §0.2 推荐档位）。
 * 视频流逐连接下发 maxSize/bitRate/maxFps/downsizeOnError；截图流用 shotInterval 控制轮询节奏。
 */
const QUALITY_PRESETS: Record<
  Quality,
  {
    label: string
    short: string
    maxSize: number
    bitRate: number
    maxFps: number
    downsizeOnError: boolean
    shotInterval: number
  }
> = {
  hd: { label: '高清', short: 'HD', maxSize: 1920, bitRate: 8_000_000, maxFps: 60, downsizeOnError: false, shotInterval: 320 },
  balanced: { label: '均衡', short: 'SD', maxSize: 1280, bitRate: 4_000_000, maxFps: 45, downsizeOnError: true, shotInterval: 600 },
  smooth: { label: '弱网', short: 'LOW', maxSize: 960, bitRate: 2_000_000, maxFps: 30, downsizeOnError: true, shotInterval: 900 },
}
const QUALITY_ORDER: Quality[] = ['hd', 'balanced', 'smooth']
const QUALITY_KEY = 'mobile_screen_quality'
const CLICK_MOVE_THRESHOLD = 14
const CONTROL_TOUCH_START_THRESHOLD = CLICK_MOVE_THRESHOLD

interface Ripple {
  id: number
  x: number
  y: number
}

interface DragState {
  x1: number
  y1: number
  x2: number
  y2: number
}

interface Props {
  deviceId: string
  online: boolean
  currentApp?: string
  onActivity?: (label: string) => void
}

const VIDEO_SUPPORTED = isVideoStreamSupported()

export default function DeviceScreen({ deviceId, online, currentApp, onActivity }: Props) {
  const imgRef = useRef<HTMLImageElement>(null)
  const videoRef = useRef<HTMLVideoElement>(null)
  const screenRef = useRef<HTMLDivElement>(null)
  const urlRef = useRef<string | null>(null)
  const streamRef = useRef<MobileVideoStream | null>(null)
  const lastTsRef = useRef(0)
  const fpsBufRef = useRef<number[]>([])
  const pointerStart = useRef<{ x: number; y: number; nx: number; ny: number; t: number } | null>(null)
  // control-touch（v3 §4.1）低延迟手动控制状态
  const touchDragActive = useRef(false)
  const lastPoint = useRef<{ x: number; y: number } | null>(null)
  const pendingMove = useRef<{ x: number; y: number } | null>(null)
  const moveRaf = useRef<number | null>(null)

  const hoverTs = useRef(0)

  const [mode, setMode] = useState<StreamMode>(VIDEO_SUPPORTED ? 'video' : 'screenshot')
  const [src, setSrc] = useState<string | null>(null)
  const [size, setSize] = useState<{ w: number; h: number } | null>(null)
  const [status, setStatus] = useState<ScreenStatus>('connecting')
  const [fps, setFps] = useState(0)
  const [quality, setQuality] = useState<Quality>(
    () => (localStorage.getItem(QUALITY_KEY) as Quality) || 'balanced',
  )
  const [errorMsg, setErrorMsg] = useState<string | null>(null)
  const [ripples, setRipples] = useState<Ripple[]>([])
  const [drag, setDrag] = useState<DragState | null>(null)
  const [hoverPos, setHoverPos] = useState<{ x: number; y: number } | null>(null)

  const preset = QUALITY_PRESETS[quality]

  useEffect(() => {
    localStorage.setItem(QUALITY_KEY, quality)
  }, [quality])

  const cycleQuality = () =>
    setQuality((q) => QUALITY_ORDER[(QUALITY_ORDER.indexOf(q) + 1) % QUALITY_ORDER.length])

  // 截图：解码后再上屏，消除闪烁
  const swapFrame = async (blob: Blob) => {
    const url = URL.createObjectURL(blob)
    await new Promise<void>((resolve, reject) => {
      const im = new Image()
      im.onload = () => {
        setSize((prev) =>
          prev && prev.w === im.naturalWidth && prev.h === im.naturalHeight
            ? prev
            : { w: im.naturalWidth, h: im.naturalHeight },
        )
        resolve()
      }
      im.onerror = () => reject(new Error('decode failed'))
      im.src = url
    })
    const prev = urlRef.current
    urlRef.current = url
    setSrc(url)
    if (prev) window.setTimeout(() => URL.revokeObjectURL(prev), 150)
  }

  // 主管道：按 mode 分支
  useEffect(() => {
    if (!online) {
      setStatus('offline')
      return
    }
    setStatus('connecting')
    setErrorMsg(null)

    // ----- 视频流 (Socket.IO + jmuxer + MSE) -----
    if (mode === 'video') {
      const v = videoRef.current
      if (!v) return
      const stream = new MobileVideoStream(v, deviceId, {
        onMetadata: (meta) => {
          setSize((prev) =>
            prev && prev.w === meta.width && prev.h === meta.height
              ? prev
              : { w: meta.width, h: meta.height },
          )
        },
        onStatus: (s) => {
          if (s === 'live') setStatus('live')
          else if (s === 'error') setStatus('error')
          else setStatus('connecting')
        },
        onError: (e) => setErrorMsg(e.message),
      })
      streamRef.current = stream
      // v3 §0.2 推荐档位：高清 1920/8M/60；均衡 1280/4M/45；弱网 960/2M/30（弱网自动降分辨率）
      stream.start({
        maxSize: preset.maxSize,
        bitRate: preset.bitRate,
        maxFps: preset.maxFps,
        downsizeOnError: preset.downsizeOnError,
      })
      return () => {
        stream.stop()
        streamRef.current = null
        setSrc(null)
      }
    }

    // ----- 截图轮询 -----
    let stopped = false
    let timer: number | undefined
    const ctrl = new AbortController()
    const interval = preset.shotInterval

    const tick = async () => {
      if (stopped) return
      const start = performance.now()
      try {
        const blob = await fetchScreenshotBlob(deviceId, ctrl.signal)
        if (stopped) return
        await swapFrame(blob)
        setStatus('live')
        setErrorMsg(null)
        const now = performance.now()
        const dt = now - (lastTsRef.current || now)
        lastTsRef.current = now
        if (dt > 0) {
          const buf = fpsBufRef.current
          buf.push(1000 / dt)
          if (buf.length > 6) buf.shift()
          setFps(Math.round(buf.reduce((a, b) => a + b, 0) / buf.length))
        }
        timer = window.setTimeout(tick, Math.max(interval - (performance.now() - start), 60))
      } catch (e) {
        if (stopped || ctrl.signal.aborted) return
        if (e instanceof MobileError && e.status === 401) return
        setStatus('error')
        setErrorMsg(e instanceof Error ? e.message : '画面获取失败')
        timer = window.setTimeout(tick, 2200)
      }
    }
    tick()

    return () => {
      stopped = true
      ctrl.abort()
      if (timer) clearTimeout(timer)
      if (urlRef.current) {
        URL.revokeObjectURL(urlRef.current)
        urlRef.current = null
      }
      setSrc(null)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [deviceId, online, quality, mode])

  useEffect(
    () => () => {
      if (moveRaf.current != null) cancelAnimationFrame(moveRaf.current)
      moveRaf.current = null
      pendingMove.current = null
    },
    [],
  )

  // 视频模式 FPS（rVFC 可选 API）
  useEffect(() => {
    if (mode !== 'video') return
    const v = videoRef.current
    if (!v) return
    const rvfc = (v as unknown as {
      requestVideoFrameCallback?: (cb: () => void) => number
    }).requestVideoFrameCallback
    if (typeof rvfc !== 'function') return
    let stop = false
    let frames = 0
    let last = performance.now()
    const cb = () => {
      if (stop) return
      frames += 1
      const now = performance.now()
      if (now - last >= 1000) {
        setFps(Math.round((frames * 1000) / (now - last)))
        frames = 0
        last = now
      }
      rvfc.call(v, cb)
    }
    rvfc.call(v, cb)
    return () => {
      stop = true
    }
  }, [mode, deviceId, online])

  // 截图模式：主动抓一帧（操作后立即反馈）
  const grabFrame = async () => {
    if (mode !== 'screenshot') return
    try {
      const blob = await fetchScreenshotBlob(deviceId)
      await swapFrame(blob)
    } catch {
      /* 静默 */
    }
  }
  const grabBurst = () => {
    if (mode !== 'screenshot') return
    grabFrame()
    window.setTimeout(grabFrame, 240)
    window.setTimeout(grabFrame, 560)
  }

  // 鼠标滚轮 → 设备滑动（桌面端浏览体验）。原生监听以便 passive:false 阻止页面滚动。
  useEffect(() => {
    const el = screenRef.current
    if (!el) return
    let accum = 0
    let timer: number | undefined
    const flush = () => {
      timer = undefined
      const delta = accum
      accum = 0
      if (status !== 'live' || !size || Math.abs(delta) < 6) return
      const dir = delta > 0 ? 1 : -1 // 滚轮向下 → 内容上移（手指上滑）
      const mag = Math.min(Math.abs(delta), 260) / 260
      const span = size.h * (0.2 + mag * 0.32)
      const cx = size.w / 2
      const startY = dir > 0 ? size.h * 0.62 : size.h * 0.38
      const endY = dir > 0 ? startY - span : startY + span
      apiSwipe(deviceId, cx, startY, cx, endY, 160)
        .then(() => grabBurst())
        .catch(() => {})
      onActivity?.(dir > 0 ? '滚动浏览 ↑' : '滚动浏览 ↓')
    }
    const onWheel = (e: WheelEvent) => {
      if (status !== 'live' || !size) return
      e.preventDefault()
      accum += e.deltaY
      if (timer == null) timer = window.setTimeout(flush, 130)
    }
    el.addEventListener('wheel', onWheel, { passive: false })
    return () => {
      el.removeEventListener('wheel', onWheel)
      if (timer) clearTimeout(timer)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [status, size, deviceId, mode])

  // 坐标换算：显示坐标 → 设备真实像素（size 必须来自后端 video-metadata 或截图真实尺寸）
  const toDevice = (clientX: number, clientY: number, clamp = false) => {
    const el = screenRef.current
    if (!el || !size) return null
    const r = el.getBoundingClientRect()
    if (!r.width || !r.height) return null
    let nx = (clientX - r.left) / r.width
    let ny = (clientY - r.top) / r.height
    if (clamp) {
      nx = Math.min(1, Math.max(0, nx))
      ny = Math.min(1, Math.max(0, ny))
    } else if (nx < 0 || ny < 0 || nx > 1 || ny > 1) {
      return null
    }
    return {
      x: nx * size.w,
      y: ny * size.h,
      nx,
      ny,
      px: nx * 100,
      py: ny * 100,
    }
  }

  const spawnRipple = (px: number, py: number) => {
    const id = Date.now() + Math.random()
    setRipples((rs) => [...rs, { id, x: px, y: py }])
    window.setTimeout(() => setRipples((rs) => rs.filter((r) => r.id !== id)), 600)
  }

  /** 是否走低延迟 control-touch（仅视频模式且同条 socket 已就绪） */
  const canControlTouch = () => mode === 'video' && !!streamRef.current?.isConnected

  const cancelControlTouch = () => {
    if (moveRaf.current != null) {
      cancelAnimationFrame(moveRaf.current)
      moveRaf.current = null
    }
    pendingMove.current = null
    if (touchDragActive.current && streamRef.current) {
      const last = lastPoint.current
      streamRef.current.sendControlTouch('cancel', last?.x ?? 0, last?.y ?? 0)
    }
    touchDragActive.current = false
  }

  const queueControlMove = (x: number, y: number) => {
    pendingMove.current = { x, y }
    if (moveRaf.current != null) return
    moveRaf.current = requestAnimationFrame(() => {
      moveRaf.current = null
      const p = pendingMove.current
      pendingMove.current = null
      if (p && touchDragActive.current) {
        streamRef.current?.sendControlTouch('move', p.x, p.y)
      }
    })
  }

  const handlePointerDown = (e: PointerEvent<HTMLDivElement>) => {
    if (status !== 'live') return
    const p = toDevice(e.clientX, e.clientY)
    if (!p) return
    pointerStart.current = { x: p.x, y: p.y, nx: p.nx, ny: p.ny, t: Date.now() }
    lastPoint.current = { x: p.x, y: p.y }
    touchDragActive.current = false
    setDrag({ x1: p.px, y1: p.py, x2: p.px, y2: p.py })
    spawnRipple(p.px, p.py)
    e.currentTarget.setPointerCapture?.(e.pointerId)
  }

  const handlePointerMove = (e: PointerEvent<HTMLDivElement>) => {
    // 悬停坐标实时回显（节流，避免逐帧重渲染）
    const now = performance.now()
    if (now - hoverTs.current > 50) {
      hoverTs.current = now
      const hp = toDevice(e.clientX, e.clientY)
      setHoverPos(hp ? { x: Math.round(hp.x), y: Math.round(hp.y) } : null)
    }
    if (!pointerStart.current) return
    const p = toDevice(e.clientX, e.clientY, true)
    if (!p) return
    lastPoint.current = { x: p.x, y: p.y }
    setDrag((d) => (d ? { ...d, x2: p.px, y2: p.py } : d))

    if (!touchDragActive.current && canControlTouch()) {
      const start = pointerStart.current
      const dist = Math.hypot(p.x - start.x, p.y - start.y)
      if (dist >= CONTROL_TOUCH_START_THRESHOLD) {
        touchDragActive.current = streamRef.current!.sendControlTouch('down', start.x, start.y)
      }
    }

    // 视频流模式：确认进入拖拽后下发 down/move；点击保持为纯 HTTP /tap。
    if (touchDragActive.current) queueControlMove(p.x, p.y)
  }

  const handlePointerUp = async (e: PointerEvent<HTMLDivElement>) => {
    const start = pointerStart.current
    pointerStart.current = null
    setDrag(null)
    const wasControlTouch = touchDragActive.current
    touchDragActive.current = false
    if (!start || status !== 'live') return
    const p = toDevice(e.clientX, e.clientY, true)
    if (!p) return
    lastPoint.current = { x: p.x, y: p.y }
    if (moveRaf.current != null) {
      cancelAnimationFrame(moveRaf.current)
      moveRaf.current = null
    }
    pendingMove.current = null

    const dist = Math.hypot(p.x - start.x, p.y - start.y)
    const dt = Date.now() - start.t
    if (import.meta.env.DEV) {
      const kind = dist < CLICK_MOVE_THRESHOLD ? 'tap' : 'swipe'
      console.debug('[DeviceScreen control]', {
        kind,
        deviceId,
        start: { x: Math.round(start.x), y: Math.round(start.y) },
        end: { x: Math.round(p.x), y: Math.round(p.y) },
        normalized10000: {
          x: Math.round(start.nx * 10000),
          y: Math.round(start.ny * 10000),
        },
        size,
        dist: Math.round(dist),
        durationMs: dt,
        mode,
      })
    }

    // 点击走纯 HTTP /tap；control-touch 短按在部分后端实现里会和 scrcpy 坐标/时序产生偏差。
    if (dist < CLICK_MOVE_THRESHOLD) {
      if (wasControlTouch && streamRef.current?.isConnected) {
        streamRef.current.sendControlTouch('cancel', p.x, p.y)
      }
      try {
        await apiTap(deviceId, start.nx * 10000, start.ny * 10000, 'normalized_10000')
        onActivity?.(`点击 (${Math.round(start.x)}, ${Math.round(start.y)})`)
        grabBurst()
      } catch (err) {
        setErrorMsg(err instanceof Error ? err.message : '操作下发失败')
      }
      return
    }

    // 视频流拖拽/滑动：down/move/up 形成真实手势。
    if (wasControlTouch && streamRef.current?.isConnected) {
      streamRef.current.sendControlTouch('up', p.x, p.y)
      onActivity?.('滑动操作')
      return
    }

    try {
      const dur = Math.min(Math.max(dt, 120), 800)
      await apiSwipe(deviceId, start.x, start.y, p.x, p.y, dur)
      onActivity?.('滑动操作')
      grabBurst()
    } catch (err) {
      setErrorMsg(err instanceof Error ? err.message : '操作下发失败')
    }
  }

  const handleKey = async (key: 'back' | 'home') => {
    try {
      await pressKey(deviceId, key)
      onActivity?.(key === 'back' ? '返回' : '主页')
      grabBurst()
    } catch (err) {
      setErrorMsg(err instanceof Error ? err.message : '操作失败')
    }
  }

  const enterFullscreen = () => {
    screenRef.current?.requestFullscreen?.().catch(() => {})
  }

  const handlePointerCancel = () => {
    cancelControlTouch()
    pointerStart.current = null
    setDrag(null)
    setHoverPos(null)
  }

  const dragLen = drag ? Math.hypot(drag.x2 - drag.x1, drag.y2 - drag.y1) : 0
  const modeLabel = mode === 'video' ? '视频流' : '截图流'

  return (
    <div className="device-stage glass-card">
      <div className="stage-statusbar">
        <div className="stage-live">
          <span className={`live-badge ${status === 'live' ? '' : 'connecting'}`}>
            <span className="live-dot" />
            {status === 'live'
              ? 'LIVE'
              : status === 'error'
                ? '重连中'
                : status === 'offline'
                  ? '离线'
                  : '连接中'}
          </span>
          {status === 'live' && (
            <span className="stage-metric">{fps > 0 ? `${fps} FPS` : '…'}</span>
          )}
        </div>
        <div className="stage-metrics">
          {hoverPos && status === 'live' && (
            <span className="stage-metric stage-coord">
              <AimOutlined /> {hoverPos.x},{hoverPos.y}
            </span>
          )}
          <span className="stage-metric">
            {mode === 'video' ? <VideoCameraOutlined /> : <ApiOutlined />} {modeLabel}
          </span>
          <span className={`stage-metric stage-quality q-${quality}`}>
            <ThunderboltOutlined /> {preset.label}
          </span>
          {size && (
            <span className="stage-metric">
              {size.w}×{size.h}
            </span>
          )}
          {currentApp && (
            <span className="stage-metric">
              <AppstoreOutlined /> {currentApp}
            </span>
          )}
        </div>
      </div>

      <div className="phone-shell">
        <div
          className="phone-frame"
          style={size ? { aspectRatio: `${size.w} / ${size.h}` } : undefined}
        >
          <span className="phone-btn-power" />
          <span className="phone-btn-volup" />
          <span className="phone-btn-voldown" />

          <div
            ref={screenRef}
            className="phone-screen screen-live"
            onPointerDown={handlePointerDown}
            onPointerMove={handlePointerMove}
            onPointerUp={handlePointerUp}
            onPointerLeave={() => {
              setHoverPos(null)
            }}
            onPointerCancel={handlePointerCancel}
            onLostPointerCapture={handlePointerCancel}
          >
            {/* 媒体层：视频流 or 截图 */}
            {mode === 'video' ? (
              <video
                ref={videoRef}
                className="screen-img"
                autoPlay
                muted
                playsInline
              />
            ) : (
              src && (
                <img
                  ref={imgRef}
                  className="screen-img"
                  src={src}
                  alt="device screen"
                  draggable={false}
                />
              )
            )}

            {status !== 'live' && (
              <div className="screen-overlay-state">
                {status === 'offline' ? (
                  <>
                    <DisconnectOutlined className="overlay-icon" />
                    <div className="overlay-text">设备离线</div>
                    <div className="overlay-sub">请在设备池唤醒或重新接入</div>
                  </>
                ) : status === 'error' ? (
                  <>
                    <DisconnectOutlined className="overlay-icon danger" />
                    <div className="overlay-text">画面连接中断</div>
                    <div className="overlay-sub">{errorMsg || '正在自动重连…'}</div>
                  </>
                ) : (
                  <>
                    <LoadingOutlined className="overlay-icon spin" />
                    <div className="overlay-text">
                      {mode === 'video' ? '正在协商视频流…' : '正在建立画面…'}
                    </div>
                    <div className="overlay-sub">{deviceId}</div>
                  </>
                )}
              </div>
            )}

            {drag && dragLen > 2 && (
              <svg className="swipe-trail" viewBox="0 0 100 100" preserveAspectRatio="none">
                <line x1={drag.x1} y1={drag.y1} x2={drag.x2} y2={drag.y2} />
                <circle cx={drag.x1} cy={drag.y1} r={1.4} className="swipe-start" />
                <circle cx={drag.x2} cy={drag.y2} r={2} className="swipe-end" />
              </svg>
            )}

            {ripples.map((r) => (
              <span
                key={r.id}
                className="touch-ripple"
                style={{ left: `${r.x}%`, top: `${r.y}%` }}
              />
            ))}

            <div className="screen-scanline" />
          </div>
        </div>

        <div className="phone-controls">
          <Tooltip title="返回">
            <button
              className="ctrl-btn"
              onClick={() => handleKey('back')}
              disabled={status !== 'live'}
            >
              <LeftOutlined />
            </button>
          </Tooltip>
          <Tooltip title="主页">
            <button
              className="ctrl-btn"
              onClick={() => handleKey('home')}
              disabled={status !== 'live'}
            >
              <HomeOutlined />
            </button>
          </Tooltip>
          {mode === 'screenshot' && (
            <Tooltip title="刷新画面">
              <button className="ctrl-btn" onClick={grabFrame} disabled={!online}>
                <ReloadOutlined />
              </button>
            </Tooltip>
          )}
          <span className="ctrl-divider" />
          <Tooltip
            title={
              VIDEO_SUPPORTED
                ? `当前 ${modeLabel}，点击切换`
                : '浏览器不支持 MSE，仅截图流可用'
            }
          >
            <button
              className={`ctrl-btn ${mode === 'video' ? 'active' : ''}`}
              onClick={() =>
                VIDEO_SUPPORTED && setMode((m) => (m === 'video' ? 'screenshot' : 'video'))
              }
              disabled={!VIDEO_SUPPORTED}
            >
              {mode === 'video' ? <VideoCameraOutlined /> : <CameraOutlined />}
            </button>
          </Tooltip>
          <Tooltip
            title={`画质 · ${preset.label}（${preset.maxSize}p · ${(preset.bitRate / 1_000_000).toFixed(0)}Mbps · ${preset.maxFps}fps${preset.downsizeOnError ? ' · 弱网降级' : ''}），点击切换`}
          >
            <button className={`ctrl-btn ctrl-btn-labeled q-${quality}`} onClick={cycleQuality}>
              <ThunderboltOutlined />
              <span className="ctrl-btn-tag">{preset.short}</span>
            </button>
          </Tooltip>
          <Tooltip title="全屏">
            <button
              className="ctrl-btn"
              onClick={enterFullscreen}
              disabled={status !== 'live'}
            >
              <ExpandOutlined />
            </button>
          </Tooltip>
        </div>
      </div>
    </div>
  )
}
