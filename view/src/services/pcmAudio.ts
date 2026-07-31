const CAPTURE_PROCESSOR = `
class Sere1nFishPcmCapture extends AudioWorkletProcessor {
  constructor() {
    super()
    this.batchSize = Math.max(128, Math.round(sampleRate * 0.04))
    this.buffer = new Float32Array(this.batchSize)
    this.offset = 0
  }
  process(inputs) {
    const channel = inputs[0] && inputs[0][0]
    if (!channel) return true
    let sourceOffset = 0
    while (sourceOffset < channel.length) {
      const length = Math.min(channel.length - sourceOffset, this.batchSize - this.offset)
      this.buffer.set(channel.subarray(sourceOffset, sourceOffset + length), this.offset)
      this.offset += length
      sourceOffset += length
      if (this.offset === this.batchSize) {
        const payload = this.buffer
        this.port.postMessage(payload, [payload.buffer])
        this.buffer = new Float32Array(this.batchSize)
        this.offset = 0
      }
    }
    return true
  }
}
registerProcessor('sere1nfish-pcm-capture', Sere1nFishPcmCapture)
`

function floatToPcm16(samples: Float32Array): ArrayBuffer {
  const output = new ArrayBuffer(samples.length * 2)
  const view = new DataView(output)
  for (let index = 0; index < samples.length; index += 1) {
    const sample = Math.max(-1, Math.min(1, samples[index]))
    view.setInt16(index * 2, sample < 0 ? sample * 32768 : sample * 32767, true)
  }
  return output
}

class StreamingResampler {
  private carry = new Float32Array(0)
  private position = 0
  private readonly inputRate: number
  private readonly outputRate: number

  constructor(inputRate: number, outputRate: number) {
    this.inputRate = inputRate
    this.outputRate = outputRate
  }

  process(chunk: Float32Array): ArrayBuffer | null {
    if (this.inputRate === this.outputRate) return floatToPcm16(chunk)
    const input = new Float32Array(this.carry.length + chunk.length)
    input.set(this.carry)
    input.set(chunk, this.carry.length)
    const ratio = this.inputRate / this.outputRate
    const output: number[] = []
    while (this.position + 1 < input.length) {
      const left = Math.floor(this.position)
      const fraction = this.position - left
      output.push(input[left] + (input[left + 1] - input[left]) * fraction)
      this.position += ratio
    }
    const consumed = Math.floor(this.position)
    this.carry = input.slice(consumed)
    this.position -= consumed
    return output.length ? floatToPcm16(Float32Array.from(output)) : null
  }
}

export class PcmMicrophoneCapture {
  private context: AudioContext | null = null
  private ownedStream: MediaStream | null = null
  private source: MediaStreamAudioSourceNode | null = null
  private processor: AudioWorkletNode | null = null
  private silentGain: GainNode | null = null

  async startDevice(
    targetSampleRate: number,
    inputDeviceId: string | undefined,
    onPcm: (pcm: ArrayBuffer) => void,
  ): Promise<void> {
    const stream = await navigator.mediaDevices.getUserMedia({
      audio: {
        channelCount: 1,
        echoCancellation: true,
        noiseSuppression: true,
        autoGainControl: true,
        ...(inputDeviceId ? { deviceId: { exact: inputDeviceId } } : {}),
      },
      video: false,
    })
    this.ownedStream = stream
    try {
      await this.startStream(stream, targetSampleRate, onPcm)
    } catch (error) {
      stream.getTracks().forEach(track => track.stop())
      this.ownedStream = null
      throw error
    }
  }

  async startStream(
    stream: MediaStream,
    targetSampleRate: number,
    onPcm: (pcm: ArrayBuffer) => void,
  ): Promise<void> {
    if (this.context) throw new Error('PCM 麦克风采集已经启动')
    if (!stream.getAudioTracks().length) throw new Error('浏览器没有可用的麦克风音轨')
    const context = new AudioContext({ latencyHint: 'interactive' })
    const moduleUrl = URL.createObjectURL(
      new Blob([CAPTURE_PROCESSOR], { type: 'text/javascript' }),
    )
    try {
      await context.audioWorklet.addModule(moduleUrl)
    } catch (error) {
      await context.close()
      throw error
    } finally {
      URL.revokeObjectURL(moduleUrl)
    }
    try {
      const source = context.createMediaStreamSource(stream)
      const processor = new AudioWorkletNode(context, 'sere1nfish-pcm-capture', {
        numberOfInputs: 1,
        numberOfOutputs: 1,
        outputChannelCount: [1],
      })
      const silentGain = context.createGain()
      silentGain.gain.value = 0
      const resampler = new StreamingResampler(context.sampleRate, targetSampleRate)
      processor.port.onmessage = (event: MessageEvent<Float32Array>) => {
        const pcm = resampler.process(event.data)
        if (pcm?.byteLength) onPcm(pcm)
      }
      source.connect(processor)
      processor.connect(silentGain)
      silentGain.connect(context.destination)
      await context.resume()

      this.context = context
      this.source = source
      this.processor = processor
      this.silentGain = silentGain
    } catch (error) {
      await context.close()
      throw error
    }
  }

  async stop(): Promise<void> {
    this.processor?.port.close()
    this.processor?.disconnect()
    this.source?.disconnect()
    this.silentGain?.disconnect()
    this.ownedStream?.getTracks().forEach(track => track.stop())
    const context = this.context
    this.context = null
    this.ownedStream = null
    this.source = null
    this.processor = null
    this.silentGain = null
    if (context && context.state !== 'closed') await context.close()
  }
}
