import { apiFetch, openAuthenticatedWebSocket } from './http'
import { PcmMicrophoneCapture } from './pcmAudio'

export type RealtimeTurnMode = 'smart_turn' | 'server_vad' | 'manual'
export type RealtimeSessionState = 'idle' | 'connecting' | 'listening' | 'speaking' | 'error'

export interface RealtimeVoiceOption {
  voice_id: string
  label: string
  kind: 'system' | 'clone'
  model?: string
}

export interface RealtimeVoiceConfig {
  available: boolean
  provider: string
  model: string
  active_sessions: number
  remote_output_supported: boolean
  input_audio: {
    encoding: string
    sample_rate: number
    channels: number
  }
  output_audio: {
    encoding: string
    sample_rate: number
    channels: number
  }
  default_voice: string
  default_mode: RealtimeTurnMode
  default_instructions: string
  max_history_turns: number
  system_voices: RealtimeVoiceOption[]
  cloned_voices: RealtimeVoiceOption[]
  turn_modes: RealtimeTurnMode[]
}

export interface RealtimeServerEvent {
  type: string
  session_id?: string
  model?: string
  voice?: string
  mode?: RealtimeTurnMode
  transcript?: string
  delta?: string
  message?: string
  response?: {
    id?: string
    status?: string
    status_details?: unknown
  }
  error?: {
    type?: string
    code?: string
    message?: string
  }
}

export interface RealtimeVoiceStartOptions {
  model: string
  voice: string
  mode: RealtimeTurnMode
  instructions?: string
  maxHistoryTurns?: number
  inputDeviceId?: string
  outputDeviceId?: string
  outputSessionId?: string
  playLocally?: boolean
}

export interface RealtimeVoiceCallbacks {
  onState?: (state: RealtimeSessionState) => void
  onEvent?: (event: RealtimeServerEvent) => void
  onError?: (error: Error) => void
}

export interface LocalAudioDevice {
  deviceId: string
  label: string
  kind: 'audioinput' | 'audiooutput'
}

type AudioContextWithSink = AudioContext & {
  setSinkId?: (sinkId: string) => Promise<void>
}

class PcmPlaybackQueue {
  private context: AudioContextWithSink | null = null
  private sources = new Set<AudioBufferSourceNode>()
  private nextStartTime = 0
  private trailingByte: number | null = null

  async start(outputDeviceId?: string): Promise<void> {
    const context = new AudioContext({ latencyHint: 'interactive' }) as AudioContextWithSink
    if (outputDeviceId && context.setSinkId) await context.setSinkId(outputDeviceId)
    await context.resume()
    this.context = context
    this.nextStartTime = context.currentTime + 0.04
  }

  append(payload: ArrayBuffer, sampleRate: number): void {
    const context = this.context
    if (!context || context.state === 'closed') return
    let bytes = new Uint8Array(payload)
    if (this.trailingByte !== null) {
      const joined = new Uint8Array(bytes.byteLength + 1)
      joined[0] = this.trailingByte
      joined.set(bytes, 1)
      bytes = joined
      this.trailingByte = null
    }
    if (bytes.byteLength % 2) {
      this.trailingByte = bytes[bytes.byteLength - 1]
      bytes = bytes.subarray(0, bytes.byteLength - 1)
    }
    if (!bytes.byteLength) return
    const sampleCount = bytes.byteLength / 2
    const audioBuffer = context.createBuffer(1, sampleCount, sampleRate)
    const channel = audioBuffer.getChannelData(0)
    const view = new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength)
    for (let index = 0; index < sampleCount; index += 1) {
      channel[index] = view.getInt16(index * 2, true) / 32768
    }
    const source = context.createBufferSource()
    source.buffer = audioBuffer
    source.connect(context.destination)
    source.onended = () => this.sources.delete(source)
    this.sources.add(source)
    this.nextStartTime = Math.max(this.nextStartTime, context.currentTime + 0.025)
    source.start(this.nextStartTime)
    this.nextStartTime += audioBuffer.duration
  }

  clear(): void {
    this.sources.forEach(source => {
      try {
        source.stop()
      } catch {
        // Already completed.
      }
    })
    this.sources.clear()
    this.trailingByte = null
    if (this.context) this.nextStartTime = this.context.currentTime + 0.025
  }

  async stop(): Promise<void> {
    this.clear()
    const context = this.context
    this.context = null
    if (context && context.state !== 'closed') await context.close()
  }
}

export function getRealtimeVoiceConfig(): Promise<RealtimeVoiceConfig> {
  return apiFetch<RealtimeVoiceConfig>('/v1/voice/realtime/config')
}

export async function listLocalAudioDevices(): Promise<LocalAudioDevice[]> {
  if (!navigator.mediaDevices?.enumerateDevices) return []
  const devices = await navigator.mediaDevices.enumerateDevices()
  let inputIndex = 0
  let outputIndex = 0
  return devices
    .filter((device): device is MediaDeviceInfo & { kind: 'audioinput' | 'audiooutput' } => (
      device.kind === 'audioinput' || device.kind === 'audiooutput'
    ))
    .map(device => {
      const sequence = device.kind === 'audioinput' ? ++inputIndex : ++outputIndex
      return {
        deviceId: device.deviceId,
        kind: device.kind,
        label: device.label || `${device.kind === 'audioinput' ? '麦克风' : '扬声器'} ${sequence}`,
      }
    })
}

export class RealtimeVoiceClient {
  private socket: WebSocket | null = null
  private capture = new PcmMicrophoneCapture()
  private playback = new PcmPlaybackQueue()
  private options: RealtimeVoiceStartOptions | null = null
  private ready = false
  private stopping = false
  private pushToTalk = false
  private manualAudioSent = false
  private readonly callbacks: RealtimeVoiceCallbacks

  constructor(callbacks: RealtimeVoiceCallbacks = {}) {
    this.callbacks = callbacks
  }

  async start(options: RealtimeVoiceStartOptions): Promise<void> {
    if (this.socket) throw new Error('全双工语音会话已经启动')
    this.options = options
    this.stopping = false
    this.callbacks.onState?.('connecting')
    try {
      if (options.playLocally !== false) {
        await this.playback.start(options.outputDeviceId)
      }
      await this.capture.startDevice(16000, options.inputDeviceId, pcm => {
        const socket = this.socket
        const canSend = options.mode !== 'manual' || this.pushToTalk
        if (!this.ready || !canSend || socket?.readyState !== WebSocket.OPEN) return
        socket.send(pcm)
        if (options.mode === 'manual') this.manualAudioSent = true
      })
      const socket = openAuthenticatedWebSocket('/v1/voice/realtime')
      socket.binaryType = 'arraybuffer'
      this.socket = socket
      await new Promise<void>((resolve, reject) => {
        const timeout = window.setTimeout(() => reject(new Error('全双工语音连接超时')), 20000)
        const fail = (error: Error) => {
          window.clearTimeout(timeout)
          reject(error)
        }
        socket.onopen = () => {
          socket.send(JSON.stringify({
            type: 'session.start',
            model: options.model,
            voice: options.voice,
            mode: options.mode,
            instructions: options.instructions?.trim() || '',
            max_history_turns: options.maxHistoryTurns,
            output_session_id: options.outputSessionId || '',
          }))
        }
        socket.onmessage = event => {
          if (event.data instanceof ArrayBuffer) {
            this.playback.append(event.data, 24000)
            return
          }
          let payload: RealtimeServerEvent
          try {
            payload = JSON.parse(String(event.data)) as RealtimeServerEvent
          } catch {
            fail(new Error('全双工语音服务返回了无效事件'))
            return
          }
          if (payload.type === 'session.ready') {
            window.clearTimeout(timeout)
            this.ready = true
            this.callbacks.onState?.('listening')
            this.callbacks.onEvent?.(payload)
            resolve()
            return
          }
          if (payload.type === 'session.error' || payload.type === 'error') {
            const error = new Error(payload.message || payload.error?.message || '全双工语音服务异常')
            if (!this.ready) fail(error)
            this.callbacks.onState?.('error')
            this.callbacks.onError?.(error)
            return
          }
          if (payload.type === 'input_audio_buffer.speech_started') {
            this.playback.clear()
            this.callbacks.onState?.('listening')
          } else if (payload.type === 'response.created') {
            this.callbacks.onState?.('speaking')
          } else if (payload.type === 'response.done' || payload.type === 'response.cancelled') {
            this.callbacks.onState?.('listening')
          }
          this.callbacks.onEvent?.(payload)
        }
        socket.onerror = () => fail(new Error('全双工语音 WebSocket 连接失败'))
        socket.onclose = event => {
          window.clearTimeout(timeout)
          const wasReady = this.ready
          this.ready = false
          this.socket = null
          if (!this.stopping) {
            const error = new Error(event.reason || '全双工语音连接已断开')
            if (!wasReady) reject(error)
            this.callbacks.onState?.('error')
            this.callbacks.onError?.(error)
          }
        }
      })
    } catch (error) {
      await this.stop()
      const cause = error instanceof Error ? error : new Error('全双工语音启动失败')
      this.callbacks.onState?.('error')
      throw cause
    }
  }

  setPushToTalk(active: boolean): void {
    if (this.options?.mode !== 'manual' || !this.ready) return
    const wasActive = this.pushToTalk
    this.pushToTalk = active
    if (active) {
      this.manualAudioSent = false
      this.playback.clear()
      this.callbacks.onState?.('listening')
      return
    }
    if (wasActive && this.manualAudioSent && this.socket?.readyState === WebSocket.OPEN) {
      this.socket.send(JSON.stringify({ type: 'input_audio_buffer.commit' }))
      this.socket.send(JSON.stringify({ type: 'response.create' }))
    }
  }

  cancelResponse(): void {
    if (this.socket?.readyState !== WebSocket.OPEN) return
    this.playback.clear()
    this.socket.send(JSON.stringify({ type: 'response.cancel' }))
  }

  async stop(): Promise<void> {
    this.stopping = true
    this.ready = false
    this.pushToTalk = false
    const socket = this.socket
    this.socket = null
    if (socket?.readyState === WebSocket.OPEN) {
      socket.send(JSON.stringify({ type: 'session.stop' }))
      socket.close(1000, 'client stop')
    } else if (socket && socket.readyState < WebSocket.CLOSING) {
      socket.close()
    }
    await Promise.allSettled([this.capture.stop(), this.playback.stop()])
    this.options = null
    this.callbacks.onState?.('idle')
  }
}
