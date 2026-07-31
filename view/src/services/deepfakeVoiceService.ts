import { openAuthenticatedWebSocket } from './http'
import { PcmMicrophoneCapture } from './pcmAudio'

export type DeepfakeVoiceState = 'idle' | 'connecting' | 'live' | 'error'

export interface DeepfakeVoiceProgress {
  outputBytes: number
  outputChunks: number
}

export interface DeepfakeVoiceCallbacks {
  onState?: (state: DeepfakeVoiceState) => void
  onProgress?: (progress: DeepfakeVoiceProgress) => void
  onError?: (error: Error) => void
}

interface DeepfakeVoiceEvent {
  type?: string
  message?: string
  sample_rate?: number
  output_bytes?: number
  output_chunks?: number
}

export class DeepfakeBrowserVoiceClient {
  private socket: WebSocket | null = null
  private capture = new PcmMicrophoneCapture()
  private ready = false
  private stopping = false
  private readonly callbacks: DeepfakeVoiceCallbacks

  constructor(callbacks: DeepfakeVoiceCallbacks = {}) {
    this.callbacks = callbacks
  }

  async start(
    streamPath: string,
    outputSessionId: string,
    mediaStream: MediaStream,
  ): Promise<void> {
    if (this.socket) throw new Error('MeanVC 浏览器音频流已经启动')
    if (!streamPath || !outputSessionId) throw new Error('MeanVC 浏览器音频流配置不完整')
    this.stopping = false
    this.callbacks.onState?.('connecting')
    const url = new URL(streamPath, window.location.origin)
    url.searchParams.set('output_session_id', outputSessionId)
    const socket = openAuthenticatedWebSocket(`${url.pathname}${url.search}`)
    this.socket = socket

    try {
      await new Promise<void>((resolve, reject) => {
        let settled = false
        const timeout = window.setTimeout(() => {
          if (!settled) {
            settled = true
            reject(new Error('MeanVC 浏览器音频连接超时'))
          }
        }, 20000)
        const fail = (error: Error) => {
          if (!settled) {
            settled = true
            window.clearTimeout(timeout)
            reject(error)
          }
        }
        socket.onmessage = event => {
          if (typeof event.data !== 'string') return
          let payload: DeepfakeVoiceEvent
          try {
            payload = JSON.parse(event.data) as DeepfakeVoiceEvent
          } catch {
            fail(new Error('MeanVC 返回了无效事件'))
            return
          }
          if (payload.type === 'session.ready' && !this.ready) {
            void this.capture.startStream(mediaStream, 16000, pcm => {
              if (
                !this.ready
                || socket.readyState !== WebSocket.OPEN
                || socket.bufferedAmount > 256 * 1024
              ) return
              socket.send(pcm)
            }).then(() => {
              this.ready = true
              this.callbacks.onState?.('live')
              if (!settled) {
                settled = true
                window.clearTimeout(timeout)
                resolve()
              }
            }).catch(error => {
              fail(error instanceof Error ? error : new Error('麦克风 PCM 采集启动失败'))
            })
            return
          }
          if (payload.type === 'voice.progress') {
            this.callbacks.onProgress?.({
              outputBytes: Number(payload.output_bytes || 0),
              outputChunks: Number(payload.output_chunks || 0),
            })
            return
          }
          if (payload.type === 'error') {
            const error = new Error(payload.message || 'MeanVC 浏览器音频流异常')
            fail(error)
            this.callbacks.onState?.('error')
            this.callbacks.onError?.(error)
          }
        }
        socket.onerror = () => fail(new Error('MeanVC 浏览器音频 WebSocket 连接失败'))
        socket.onclose = event => {
          window.clearTimeout(timeout)
          const wasReady = this.ready
          this.ready = false
          this.socket = null
          if (this.stopping) return
          const error = new Error(event.reason || 'MeanVC 浏览器音频连接已断开')
          if (!wasReady) fail(error)
          this.callbacks.onState?.('error')
          this.callbacks.onError?.(error)
        }
      })
    } catch (error) {
      await this.stop()
      const cause = error instanceof Error ? error : new Error('MeanVC 浏览器音频启动失败')
      this.callbacks.onState?.('error')
      throw cause
    }
  }

  async stop(): Promise<void> {
    this.stopping = true
    this.ready = false
    const socket = this.socket
    this.socket = null
    if (socket?.readyState === WebSocket.OPEN) {
      socket.close(1000, 'client stop')
    } else if (socket && socket.readyState < WebSocket.CLOSING) {
      socket.close()
    }
    await this.capture.stop()
    this.callbacks.onState?.('idle')
  }
}
