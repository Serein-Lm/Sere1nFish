/**
 * Socket.IO H264 Annex-B 视频流封装
 *
 * 后端: MOBILE_API.md §3a (路径 /socket.io, connect-device 启动, 不断推送 video-data)。
 * 前端: 用 jmuxer 把 Annex-B 喂给 MSE → <video> 播放。
 *
 * 路径直接用 '/socket.io'(相对, 经 vite 代理转 ws://127.0.0.1:8000);
 * 生产同源部署时同样工作; 跨源需后端 CORS + 显式 base URL。
 *
 * v3 同步：
 *  - connect-device 支持逐连接画质/流畅度参数 maxSize/bitRate/maxFps/downsizeOnError(§0.2)。
 *  - 同一条连接支持低延迟手动控制 control-touch(§4.1)，用于拖拽/连续手势，毫秒级跟手。
 */

import { io, type Socket } from 'socket.io-client'
import JMuxer from 'jmuxer'
import { getToken } from './http'

export interface VideoMetadata {
  deviceName?: string
  width: number
  height: number
  codec?: string
}

export interface VideoPacket {
  type: 'config' | 'data'
  data: ArrayBuffer | Uint8Array
  keyframe?: boolean
  pts?: number
}

export type VideoStreamStatus = 'idle' | 'connecting' | 'live' | 'closed' | 'error'

export interface VideoStreamHandlers {
  onMetadata?: (meta: VideoMetadata) => void
  onStatus?: (status: VideoStreamStatus) => void
  onError?: (err: Error) => void
}

export interface VideoStreamOptions {
  /** 长边分辨率（清晰度）。默认 1280，高清可调 1920 */
  maxSize?: number
  /** 码率 bps（清晰度）。默认 4_000_000 */
  bitRate?: number
  /** 帧率（流畅度，1–120）。v3 新增，默认 60 */
  maxFps?: number
  /** 失败自动降分辨率重试。v3 新增，弱网建议 true */
  downsizeOnError?: boolean
}

/** 低延迟手动控制动作（v3 §4.1 control-touch） */
export type TouchAction = 'down' | 'move' | 'up' | 'cancel'

export class MobileVideoStream {
  private socket: Socket | null = null
  private jmuxer: JMuxer | null = null
  private readonly video: HTMLVideoElement
  private readonly deviceId: string
  private readonly handlers: VideoStreamHandlers
  private stopped = false

  constructor(video: HTMLVideoElement, deviceId: string, handlers: VideoStreamHandlers = {}) {
    this.video = video
    this.deviceId = deviceId
    this.handlers = handlers
  }

  start(opts: VideoStreamOptions = {}): void {
    if (this.socket) return
    this.stopped = false
    this.handlers.onStatus?.('connecting')

    // 相对路径 '/' 即同源；dev 走 vite 代理；生产同源即可
    const token = getToken()
    const socket = io('/', {
      path: '/socket.io',
      transports: ['websocket', 'polling'],
      auth: token ? { token } : undefined,
      reconnection: true,
      reconnectionAttempts: Infinity,
      reconnectionDelay: 1000,
      reconnectionDelayMax: 8000,
      timeout: 8000,
    })
    this.socket = socket

    socket.on('connect', () => {
      if (this.stopped) return
      socket.emit('connect-device', {
        device_id: this.deviceId,
        maxSize: opts.maxSize ?? 1280,
        bitRate: opts.bitRate ?? 4_000_000,
        maxFps: opts.maxFps ?? 60,
        downsizeOnError: opts.downsizeOnError ?? false,
      })
    })

    socket.on('video-metadata', (meta: VideoMetadata) => {
      if (this.stopped) return
      // 重新初始化 jmuxer（每次 metadata 视为新流）
      if (this.jmuxer) {
        try {
          this.jmuxer.destroy()
        } catch {
          /* ignore */
        }
        this.jmuxer = null
      }
      try {
        this.jmuxer = new JMuxer({
          node: this.video,
          mode: 'video',
          flushingTime: 0,
          clearBuffer: true,
          debug: false,
          onError: (e) => this.handlers.onError?.(new Error(`jmuxer: ${String(e)}`)),
        })
      } catch (e) {
        this.handlers.onError?.(e instanceof Error ? e : new Error('jmuxer init failed'))
        this.handlers.onStatus?.('error')
        return
      }
      this.handlers.onMetadata?.(meta)
      this.handlers.onStatus?.('live')
    })

    socket.on('video-data', (pkt: VideoPacket) => {
      if (this.stopped || !this.jmuxer) return
      const buf =
        pkt.data instanceof Uint8Array
          ? pkt.data
          : new Uint8Array(pkt.data as ArrayBuffer)
      try {
        this.jmuxer.feed({ video: buf })
      } catch (e) {
        this.handlers.onError?.(e instanceof Error ? e : new Error('feed failed'))
      }
    })

    socket.on('connect_error', (e) => {
      if (this.stopped) return
      this.handlers.onError?.(e instanceof Error ? e : new Error(String(e)))
      this.handlers.onStatus?.('error')
    })

    socket.on('disconnect', () => {
      if (this.stopped) return
      this.handlers.onStatus?.('connecting')
    })

    socket.on('error', (e: unknown) => {
      if (this.stopped) return
      this.handlers.onError?.(e instanceof Error ? e : new Error(String(e)))
    })
  }

  stop(): void {
    this.stopped = true
    if (this.jmuxer) {
      try {
        this.jmuxer.destroy()
      } catch {
        /* ignore */
      }
      this.jmuxer = null
    }
    if (this.socket) {
      try {
        this.socket.removeAllListeners()
        this.socket.disconnect()
      } catch {
        /* ignore */
      }
      this.socket = null
    }
    try {
      this.video.pause()
      this.video.removeAttribute('src')
      this.video.load()
    } catch {
      /* ignore */
    }
    this.handlers.onStatus?.('closed')
  }

  /** 同一连接是否已就绪，可用于低延迟控制 */
  get isConnected(): boolean {
    return !this.stopped && !!this.socket?.connected
  }

  /**
   * 低延迟手动控制（v3 §4.1，scrcpy 控制通道，毫秒级）。
   * x/y 为设备真实像素。返回 ack 的 success；socket 未就绪返回 false。
   * 适合拖拽/滑动/手势：pointerdown→down，pointermove→move(按帧节流)，pointerup→up。
   */
  sendControlTouch(action: TouchAction, x: number, y: number): boolean {
    const socket = this.socket
    if (this.stopped || !socket?.connected) return false
    try {
      socket.emit('control-touch', {
        device_id: this.deviceId,
        action,
        x: Math.round(x),
        y: Math.round(y),
      })
      return true
    } catch {
      return false
    }
  }
}

/** 检测浏览器是否支持视频流回放（MSE 或 WebCodecs） */
export function isVideoStreamSupported(): boolean {
  if (typeof window === 'undefined') return false
  const w = window as unknown as { MediaSource?: unknown; ManagedMediaSource?: unknown }
  return !!w.MediaSource || !!w.ManagedMediaSource
}
