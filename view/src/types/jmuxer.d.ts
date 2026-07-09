declare module 'jmuxer' {
  export interface JMuxerOptions {
    node: string | HTMLVideoElement
    mode?: 'video' | 'audio' | 'both'
    flushingTime?: number
    fps?: number
    debug?: boolean
    clearBuffer?: boolean
    maxDelay?: number
    readFpsFromTrack?: boolean
    onReady?: () => void
    onError?: (data: unknown) => void
  }

  export interface JMuxerChunk {
    video?: Uint8Array
    audio?: Uint8Array
    duration?: number
    compositionTimeOffset?: number
  }

  export default class JMuxer {
    constructor(options: JMuxerOptions)
    feed(data: JMuxerChunk): void
    destroy(): void
    reset(): void
  }
}
