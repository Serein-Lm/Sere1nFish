import { useState, useEffect, useRef, useCallback } from 'react'
import {
  Card, Row, Col, Button, Input, Table, Tag, Space, Modal,
  Form, Select, InputNumber, Switch, Typography, Empty,
  Tooltip, message, Popconfirm, Upload, Checkbox,
} from 'antd'
import type { ColumnsType } from 'antd/es/table'
import {
  AudioOutlined, PlusOutlined, SoundOutlined, DeleteOutlined,
  PlayCircleOutlined, PauseCircleOutlined, ReloadOutlined,
  HistoryOutlined, InfoCircleOutlined, CheckCircleOutlined,
  CloseCircleOutlined, LoadingOutlined, CloudUploadOutlined,
  StopOutlined,
} from '@ant-design/icons'
import {
  createVoice, listVoices, deleteVoice, streamSpeech, listRecords,
  uploadAudio, getRecord,
  type VoiceClone as VoiceCloneType, type SynthesisRecord,
  type VoiceStreamMetadata,
} from '../../services/voiceService'
import './VoiceClone.css'

const { Title, Paragraph, Text } = Typography
const { TextArea } = Input

const LANGUAGE_OPTIONS = [
  { value: 'zh', label: '中文' },
  { value: 'en', label: '英文' },
  { value: 'ja', label: '日语' },
  { value: 'ko', label: '韩语' },
  { value: 'fr', label: '法语' },
  { value: 'de', label: '德语' },
  { value: 'ru', label: '俄语' },
  { value: 'pt', label: '葡萄牙语' },
  { value: 'th', label: '泰语' },
  { value: 'id', label: '印尼语' },
  { value: 'vi', label: '越南语' },
  { value: 'it', label: '意大利语' },
  { value: 'es', label: '西班牙语' },
  { value: 'ms', label: '马来西亚语' },
  { value: 'fil', label: '菲律宾语' },
  { value: 'ar', label: '阿拉伯语' },
]

function formatTime(ts: number | null): string {
  if (!ts) return '-'
  return new Date(ts * 1000).toLocaleString('zh-CN')
}

function formatBytes(bytes: number): string {
  if (bytes === 0) return '0 B'
  const k = 1024
  const sizes = ['B', 'KB', 'MB']
  const i = Math.floor(Math.log(bytes) / Math.log(k))
  return `${(bytes / Math.pow(k, i)).toFixed(1)} ${sizes[i]}`
}

function createPcmWav(
  chunks: Uint8Array[],
  audioBytes: number,
  sampleRate: number,
): Blob {
  const buffer = new ArrayBuffer(44 + audioBytes)
  const view = new DataView(buffer)
  const writeAscii = (offset: number, value: string) => {
    for (let index = 0; index < value.length; index += 1) {
      view.setUint8(offset + index, value.charCodeAt(index))
    }
  }
  writeAscii(0, 'RIFF')
  view.setUint32(4, 36 + audioBytes, true)
  writeAscii(8, 'WAVE')
  writeAscii(12, 'fmt ')
  view.setUint32(16, 16, true)
  view.setUint16(20, 1, true)
  view.setUint16(22, 1, true)
  view.setUint32(24, sampleRate, true)
  view.setUint32(28, sampleRate * 2, true)
  view.setUint16(32, 2, true)
  view.setUint16(34, 16, true)
  writeAscii(36, 'data')
  view.setUint32(40, audioBytes, true)

  const target = new Uint8Array(buffer, 44)
  let offset = 0
  chunks.forEach(chunk => {
    target.set(chunk, offset)
    offset += chunk.byteLength
  })
  return new Blob([buffer], { type: 'audio/wav' })
}

interface LiveMetrics {
  model: string
  sampleRate: number
  firstChunkLatencyMs: number
  providerTtfaMs: number
  rtf: number
}

export default function VoiceClone() {
  const [messageApi, messageContextHolder] = message.useMessage()
  const [voices, setVoices] = useState<VoiceCloneType[]>([])
  const [voicesTotal, setVoicesTotal] = useState(0)
  const [voicesPage, setVoicesPage] = useState(0)
  const [voicesLoading, setVoicesLoading] = useState(false)

  const [records, setRecords] = useState<SynthesisRecord[]>([])
  const [recordsTotal, setRecordsTotal] = useState(0)
  const [recordsPage, setRecordsPage] = useState(0)
  const [recordsLoading, setRecordsLoading] = useState(false)

  const [createOpen, setCreateOpen] = useState(false)
  const [creating, setCreating] = useState(false)
  const [uploading, setUploading] = useState(false)
  const [uploadedUrl, setUploadedUrl] = useState<string | null>(null)
  const [uploadMode, setUploadMode] = useState<'url' | 'file'>('file')
  const [form] = Form.useForm()

  const [synthText, setSynthText] = useState('')
  const [synthInstruction, setSynthInstruction] = useState('')
  const [selectedVoice, setSelectedVoice] = useState<string | null>(null)
  const [synthesizing, setSynthesizing] = useState(false)
  const [audioUrl, setAudioUrl] = useState<string | null>(null)
  const [isPlaying, setIsPlaying] = useState(false)
  const [liveMetrics, setLiveMetrics] = useState<LiveMetrics | null>(null)
  const audioRef = useRef<HTMLAudioElement | null>(null)
  const abortRef = useRef<AbortController | null>(null)
  const audioContextRef = useRef<AudioContext | null>(null)
  const sourceNodesRef = useRef(new Set<AudioBufferSourceNode>())
  const realtimePlaybackRef = useRef(false)
  const synthesisRunRef = useRef(0)

  const [activeSection, setActiveSection] = useState<'voices' | 'synthesize' | 'history'>('synthesize')

  const loadVoices = useCallback(async (page = 0) => {
    setVoicesLoading(true)
    try {
      const data = await listVoices({ page, page_size: 10 })
      setVoices(data.items)
      setVoicesTotal(data.total)
      setVoicesPage(page)
    } catch (e: unknown) {
      messageApi.error(`加载音色列表失败: ${e instanceof Error ? e.message : '未知错误'}`)
    } finally {
      setVoicesLoading(false)
    }
  }, [messageApi])

  const loadRecords = useCallback(async (page = 0) => {
    setRecordsLoading(true)
    try {
      const data = await listRecords({ page, page_size: 10 })
      setRecords(data.items)
      setRecordsTotal(data.total)
      setRecordsPage(page)
    } catch (e: unknown) {
      messageApi.error(`加载合成记录失败: ${e instanceof Error ? e.message : '未知错误'}`)
    } finally {
      setRecordsLoading(false)
    }
  }, [messageApi])

  useEffect(() => {
    loadVoices()
    loadRecords()
  }, [loadVoices, loadRecords])

  const handleFileUpload = async (file: File) => {
    setUploading(true)
    try {
      const result = await uploadAudio(file)
      setUploadedUrl(result.url)
      form.setFieldValue('url', result.url)
      messageApi.success(`音频上传成功: ${result.original_name}`)
    } catch (e: unknown) {
      messageApi.error(`上传失败: ${e instanceof Error ? e.message : '未知错误'}`)
    } finally {
      setUploading(false)
    }
    return false
  }

  const handleCreate = async (values: Record<string, unknown>) => {
    const url = uploadMode === 'file' ? uploadedUrl : (values.url as string)
    if (!url) {
      messageApi.warning(uploadMode === 'file' ? '请先上传音频文件' : '请输入音频 URL')
      return
    }

    setCreating(true)
    try {
      const resp = await createVoice({
        url,
        prefix: (values.prefix as string) || undefined,
        language_hints: (values.language_hints as string[]) || undefined,
        max_prompt_audio_length: (values.max_prompt_audio_length as number) || undefined,
        enable_preprocess: values.enable_preprocess as boolean | undefined,
        authorized_use: values.authorized_use === true,
      })
      messageApi.success(`音色创建成功: ${resp.voice_id}`)
      setCreateOpen(false)
      setUploadedUrl(null)
      form.resetFields()
      loadVoices()
    } catch (e: unknown) {
      messageApi.error(`创建失败: ${e instanceof Error ? e.message : '未知错误'}`)
    } finally {
      setCreating(false)
    }
  }

  const handleDelete = async (voiceId: string) => {
    try {
      await deleteVoice(voiceId)
      messageApi.success('音色已删除')
      loadVoices(voicesPage)
    } catch (e: unknown) {
      messageApi.error(`删除失败: ${e instanceof Error ? e.message : '未知错误'}`)
    }
  }

  const stopRealtimePlayback = useCallback(() => {
    realtimePlaybackRef.current = false
    sourceNodesRef.current.forEach(source => {
      try {
        source.stop()
      } catch {
        // The source may already have ended.
      }
    })
    sourceNodesRef.current.clear()
    const context = audioContextRef.current
    audioContextRef.current = null
    if (context && context.state !== 'closed') void context.close()
    setIsPlaying(false)
  }, [])

  const handleCancelSynthesis = useCallback(() => {
    abortRef.current?.abort()
    stopRealtimePlayback()
  }, [stopRealtimePlayback])

  const handleSynthesize = async () => {
    if (!synthText.trim() || !selectedVoice) {
      messageApi.warning('请输入文本并选择音色')
      return
    }

    abortRef.current?.abort()
    stopRealtimePlayback()
    const runId = synthesisRunRef.current + 1
    synthesisRunRef.current = runId
    const controller = new AbortController()
    abortRef.current = controller
    setSynthesizing(true)
    setLiveMetrics(null)
    if (audioRef.current) {
      audioRef.current.pause()
      setIsPlaying(false)
    }
    setAudioUrl(previous => {
      if (previous) URL.revokeObjectURL(previous)
      return null
    })

    let context: AudioContext
    try {
      context = new AudioContext({ latencyHint: 'interactive' })
      audioContextRef.current = context
      await context.resume()
    } catch (error) {
      abortRef.current = null
      setSynthesizing(false)
      messageApi.error(`无法初始化实时播放器: ${error instanceof Error ? error.message : '未知错误'}`)
      return
    }
    const pcmChunks: Uint8Array[] = []
    let pcmBytes = 0
    let sampleRate = 24000
    let nextStartTime = context.currentTime + 0.08
    let trailingByte: number | null = null
    let streamFinished = false

    const finishRealtimePlayback = () => {
      if (
        streamFinished
        && sourceNodesRef.current.size === 0
        && synthesisRunRef.current === runId
      ) {
        realtimePlaybackRef.current = false
        if (audioContextRef.current === context) audioContextRef.current = null
        if (context.state !== 'closed') void context.close()
        setIsPlaying(false)
      }
    }

    const scheduleChunk = (chunk: Uint8Array) => {
      if (controller.signal.aborted || synthesisRunRef.current !== runId) return
      const storedChunk = chunk.slice()
      pcmChunks.push(storedChunk)
      pcmBytes += storedChunk.byteLength

      let bytes = storedChunk
      if (trailingByte !== null) {
        const joined = new Uint8Array(storedChunk.byteLength + 1)
        joined[0] = trailingByte
        joined.set(storedChunk, 1)
        bytes = joined
        trailingByte = null
      }
      if (bytes.byteLength % 2 !== 0) {
        trailingByte = bytes[bytes.byteLength - 1]
        bytes = bytes.subarray(0, bytes.byteLength - 1)
      }
      if (!bytes.byteLength) return

      const sampleCount = bytes.byteLength / 2
      const audioBuffer = context.createBuffer(1, sampleCount, sampleRate)
      const samples = audioBuffer.getChannelData(0)
      const pcm = new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength)
      for (let index = 0; index < sampleCount; index += 1) {
        samples[index] = pcm.getInt16(index * 2, true) / 32768
      }

      const source = context.createBufferSource()
      source.buffer = audioBuffer
      source.connect(context.destination)
      sourceNodesRef.current.add(source)
      source.onended = () => {
        sourceNodesRef.current.delete(source)
        finishRealtimePlayback()
      }
      nextStartTime = Math.max(nextStartTime, context.currentTime + 0.04)
      source.start(nextStartTime)
      nextStartTime += audioBuffer.duration
      realtimePlaybackRef.current = true
      setIsPlaying(true)
    }

    try {
      const result = await streamSpeech({
        text: synthText.trim(),
        voiceId: selectedVoice,
        instruction: synthInstruction,
        signal: controller.signal,
        onOpen: (metadata: VoiceStreamMetadata) => {
          sampleRate = metadata.sampleRate
        },
        onChunk: scheduleChunk,
      })
      streamFinished = true
      finishRealtimePlayback()
      if (!result.audioBytes) throw new Error('语音服务返回了空音频')

      const wav = createPcmWav(pcmChunks, pcmBytes, result.sampleRate)
      const url = URL.createObjectURL(wav)
      setAudioUrl(url)

      let record: SynthesisRecord | null = null
      if (result.recordId) {
        try {
          for (let attempt = 0; attempt < 3; attempt += 1) {
            record = await getRecord(result.recordId)
            if (record.status !== 'processing') break
            await new Promise(resolve => window.setTimeout(resolve, 100))
          }
        } catch {
          record = null
        }
      }
      setLiveMetrics({
        model: result.model,
        sampleRate: result.sampleRate,
        firstChunkLatencyMs: result.firstChunkLatencyMs,
        providerTtfaMs: record?.first_pkg_delay_ms || 0,
        rtf: record?.rtf || 0,
      })
      messageApi.success(`实时合成完成，端到端首包 ${result.firstChunkLatencyMs}ms`)
      void loadRecords()
    } catch (e: unknown) {
      streamFinished = true
      stopRealtimePlayback()
      if (e instanceof DOMException && e.name === 'AbortError') {
        messageApi.info('已停止实时合成')
      } else {
        messageApi.error(`合成失败: ${e instanceof Error ? e.message : '未知错误'}`)
      }
    } finally {
      if (synthesisRunRef.current === runId) {
        abortRef.current = null
        setSynthesizing(false)
      }
    }
  }

  const togglePlay = () => {
    if (realtimePlaybackRef.current) {
      stopRealtimePlayback()
      return
    }
    if (!audioRef.current || !audioUrl) return
    if (isPlaying) {
      audioRef.current.pause()
    } else {
      audioRef.current.play()
    }
    setIsPlaying(!isPlaying)
  }

  useEffect(() => () => {
    abortRef.current?.abort()
    stopRealtimePlayback()
  }, [stopRealtimePlayback])

  useEffect(() => () => {
    if (audioUrl) URL.revokeObjectURL(audioUrl)
  }, [audioUrl])

  const voiceColumns: ColumnsType<VoiceCloneType> = [
    {
      title: '音色 ID',
      dataIndex: 'voice_id',
      key: 'voice_id',
      ellipsis: true,
      render: (id: string) => (
        <Tooltip title={id}>
          <Text copyable={{ text: id }} className="voice-id-text">
            {id.length > 30 ? `...${id.slice(-20)}` : id}
          </Text>
        </Tooltip>
      ),
    },
    {
      title: '前缀',
      dataIndex: 'prefix',
      key: 'prefix',
      width: 100,
    },
    {
      title: '模型',
      dataIndex: 'model',
      key: 'model',
      width: 180,
      render: (m: string) => <Tag color="blue">{m}</Tag>,
    },
    {
      title: '状态',
      dataIndex: 'status',
      key: 'status',
      width: 80,
      render: (s: string) => (
        <Tag color={s === 'active' ? 'success' : 'default'} icon={
          s === 'active' ? <CheckCircleOutlined /> : <CloseCircleOutlined />
        }>
          {s === 'active' ? '可用' : s}
        </Tag>
      ),
    },
    {
      title: '创建时间',
      dataIndex: 'created_at',
      key: 'created_at',
      width: 170,
      render: formatTime,
    },
    {
      title: '操作',
      key: 'actions',
      width: 120,
      render: (_: unknown, record: VoiceCloneType) => (
        <Space>
          <Button
            type="link" size="small"
            icon={<SoundOutlined />}
            onClick={() => {
              setSelectedVoice(record.voice_id)
              setActiveSection('synthesize')
            }}
          >
            合成
          </Button>
          <Popconfirm title="确定删除此音色？" onConfirm={() => handleDelete(record.voice_id)}>
            <Button type="link" size="small" danger icon={<DeleteOutlined />} />
          </Popconfirm>
        </Space>
      ),
    },
  ]

  const recordColumns: ColumnsType<SynthesisRecord> = [
    {
      title: '记录 ID',
      dataIndex: 'record_id',
      key: 'record_id',
      width: 150,
      ellipsis: true,
    },
    {
      title: '文本',
      dataIndex: 'text',
      key: 'text',
      ellipsis: true,
      render: (t: string) => (
        <Tooltip title={t}>
          <span>{t.length > 40 ? t.slice(0, 40) + '...' : t}</span>
        </Tooltip>
      ),
    },
    {
      title: '状态',
      dataIndex: 'status',
      key: 'status',
      width: 100,
      render: (s: string) => {
        const map: Record<string, { color: string; icon: React.ReactNode }> = {
          completed: { color: 'success', icon: <CheckCircleOutlined /> },
          processing: { color: 'processing', icon: <LoadingOutlined /> },
          failed: { color: 'error', icon: <CloseCircleOutlined /> },
          cancelled: { color: 'default', icon: <StopOutlined /> },
        }
        const cfg = map[s] || { color: 'default', icon: null }
        const labels: Record<string, string> = {
          completed: '完成',
          processing: '处理中',
          failed: '失败',
          cancelled: '已停止',
        }
        return <Tag color={cfg.color} icon={cfg.icon}>{labels[s] || s}</Tag>
      },
    },
    {
      title: '模式',
      dataIndex: 'streaming',
      key: 'streaming',
      width: 80,
      render: (streaming: boolean) => (
        <Tag color={streaming ? 'cyan' : 'default'}>{streaming ? '实时' : '完整'}</Tag>
      ),
    },
    {
      title: '大小',
      dataIndex: 'audio_bytes',
      key: 'audio_bytes',
      width: 80,
      render: formatBytes,
    },
    {
      title: 'TTFA',
      dataIndex: 'first_pkg_delay_ms',
      key: 'delay',
      width: 90,
      render: (ms: number) => ms > 0 ? `${ms}ms` : '-',
    },
    {
      title: 'RTF',
      dataIndex: 'rtf',
      key: 'rtf',
      width: 75,
      render: (rtf: number) => rtf > 0 ? rtf.toFixed(3) : '-',
    },
    {
      title: '时间',
      dataIndex: 'created_at',
      key: 'created_at',
      width: 170,
      render: formatTime,
    },
  ]

  const sectionNav = [
    { key: 'synthesize', label: '语音合成', icon: <SoundOutlined /> },
    { key: 'voices', label: '音色管理', icon: <AudioOutlined /> },
    { key: 'history', label: '合成历史', icon: <HistoryOutlined /> },
  ] as const

  return (
    <>
      {messageContextHolder}
      <div className="voice-clone fade-in">
      {/* 顶部导航 */}
      <div className="vc-section-nav slide-up">
        {sectionNav.map(({ key, label, icon }) => (
          <div
            key={key}
            className={`vc-nav-item ${activeSection === key ? 'active' : ''}`}
            onClick={() => setActiveSection(key)}
          >
            {icon}
            <span>{label}</span>
          </div>
        ))}
      </div>

      {/* 语音合成 */}
      {activeSection === 'synthesize' && (
        <div className="vc-section slide-up stagger-1">
          <Card className="glass-card vc-synth-card">
            <Row gutter={24}>
              <Col xs={24} lg={16}>
                <div className="vc-synth-input">
                  <Title level={5}>
                    <SoundOutlined /> 文本转语音
                  </Title>
                  <TextArea
                    id="voice-synthesis-text"
                    name="voice-synthesis-text"
                    value={synthText}
                    onChange={e => setSynthText(e.target.value)}
                    placeholder="请输入需要合成的文本内容（最多 5000 字符）..."
                    maxLength={5000}
                    showCount
                    autoSize={{ minRows: 4, maxRows: 10 }}
                    className="vc-textarea"
                  />
                  <Input
                    id="voice-synthesis-instruction"
                    name="voice-synthesis-instruction"
                    value={synthInstruction}
                    onChange={event => setSynthInstruction(event.target.value)}
                    placeholder="语气指令（可选），例如：自然、平静、稍慢"
                    maxLength={128}
                    className="vc-instruction-input"
                  />
                  <div className="vc-synth-controls">
                    <Select
                      value={selectedVoice}
                      onChange={setSelectedVoice}
                      placeholder="选择音色"
                      className="vc-voice-select"
                      allowClear
                      showSearch
                      optionFilterProp="label"
                      options={voices.map(v => ({
                        value: v.voice_id,
                        label: `${v.prefix} (${v.voice_id.slice(-8)})`,
                      }))}
                      notFoundContent={
                        voices.length === 0
                          ? <Empty description="暂无音色，请先创建" image={Empty.PRESENTED_IMAGE_SIMPLE} />
                          : undefined
                      }
                    />
                    <Button
                      type="primary"
                      danger={synthesizing}
                      icon={synthesizing ? <StopOutlined /> : <SoundOutlined />}
                      onClick={synthesizing ? handleCancelSynthesis : handleSynthesize}
                      disabled={!synthesizing && (!synthText.trim() || !selectedVoice)}
                      className="vc-synth-btn"
                    >
                      {synthesizing ? '停止' : '实时合成'}
                    </Button>
                  </div>
                </div>
              </Col>
              <Col xs={24} lg={8}>
                <div className="vc-player">
                  <Title level={5}>
                    <PlayCircleOutlined /> 播放器
                  </Title>
                  {audioUrl || synthesizing ? (
                    <div className="vc-player-active">
                      <div
                        className={`vc-play-btn ${isPlaying ? 'playing' : ''}`}
                        onClick={synthesizing ? handleCancelSynthesis : togglePlay}
                      >
                        {isPlaying
                          ? <PauseCircleOutlined />
                          : synthesizing
                            ? <LoadingOutlined />
                            : <PlayCircleOutlined />}
                      </div>
                      <audio
                        ref={audioRef}
                        src={audioUrl || undefined}
                        onEnded={() => {
                          if (!realtimePlaybackRef.current) setIsPlaying(false)
                        }}
                        onPause={() => {
                          if (!realtimePlaybackRef.current) setIsPlaying(false)
                        }}
                        onPlay={() => {
                          if (!realtimePlaybackRef.current) setIsPlaying(true)
                        }}
                      />
                      <Text type="secondary" className="vc-player-hint">
                        {synthesizing ? '实时接收与播放中' : '点击播放合成音频'}
                      </Text>
                      {liveMetrics && (
                        <div className="vc-live-metrics">
                          <Tooltip title="浏览器发起请求至收到首个音频分片">
                            <div><Text type="secondary">端到端首包</Text><strong>{liveMetrics.firstChunkLatencyMs}ms</strong></div>
                          </Tooltip>
                          <Tooltip title="百炼服务从请求开始至产生首个音频分片">
                            <div><Text type="secondary">服务 TTFA</Text><strong>{liveMetrics.providerTtfaMs || '-'}{liveMetrics.providerTtfaMs ? 'ms' : ''}</strong></div>
                          </Tooltip>
                          <Tooltip title="生成耗时与音频时长之比，小于 1 表示快于实时">
                            <div><Text type="secondary">RTF</Text><strong>{liveMetrics.rtf ? liveMetrics.rtf.toFixed(3) : '-'}</strong></div>
                          </Tooltip>
                          <Tooltip title={liveMetrics.model}>
                            <div><Text type="secondary">采样率</Text><strong>{liveMetrics.sampleRate / 1000}kHz</strong></div>
                          </Tooltip>
                        </div>
                      )}
                    </div>
                  ) : (
                    <div className="vc-player-empty">
                      <AudioOutlined className="vc-player-empty-icon" />
                      <Text type="secondary">输入文本并选择音色后合成</Text>
                    </div>
                  )}
                </div>
              </Col>
            </Row>
          </Card>
        </div>
      )}

      {/* 音色管理 */}
      {activeSection === 'voices' && (
        <div className="vc-section slide-up stagger-1">
          <Card
            className="glass-card"
            title={
              <Space>
                <AudioOutlined />
                <span>音色列表</span>
                <Tag>{voicesTotal} 个</Tag>
              </Space>
            }
            extra={
              <Space>
                <Button icon={<ReloadOutlined />} onClick={() => loadVoices(voicesPage)}>
                  刷新
                </Button>
                <Button type="primary" icon={<PlusOutlined />} onClick={() => setCreateOpen(true)}>
                  创建音色
                </Button>
              </Space>
            }
          >
            <Table
              columns={voiceColumns}
              dataSource={voices}
              rowKey="voice_id"
              loading={voicesLoading}
              pagination={{
                current: voicesPage + 1,
                total: voicesTotal,
                pageSize: 10,
                showTotal: t => `共 ${t} 条`,
                onChange: p => loadVoices(p - 1),
              }}
              scroll={{ x: 800 }}
              locale={{ emptyText: <Empty description="暂无音色，点击「创建音色」开始" /> }}
            />
          </Card>
        </div>
      )}

      {/* 合成历史 */}
      {activeSection === 'history' && (
        <div className="vc-section slide-up stagger-1">
          <Card
            className="glass-card"
            title={
              <Space>
                <HistoryOutlined />
                <span>合成记录</span>
                <Tag>{recordsTotal} 条</Tag>
              </Space>
            }
            extra={
              <Button icon={<ReloadOutlined />} onClick={() => loadRecords(recordsPage)}>
                刷新
              </Button>
            }
          >
            <Table
              columns={recordColumns}
              dataSource={records}
              rowKey="record_id"
              loading={recordsLoading}
              pagination={{
                current: recordsPage + 1,
                total: recordsTotal,
                pageSize: 10,
                showTotal: t => `共 ${t} 条`,
                onChange: p => loadRecords(p - 1),
              }}
              scroll={{ x: 800 }}
              locale={{ emptyText: <Empty description="暂无合成记录" /> }}
            />
          </Card>
        </div>
      )}

      {/* 创建音色弹窗 */}
      <Modal
        title={
          <Space>
            <CloudUploadOutlined />
            <span>创建复刻音色</span>
          </Space>
        }
        open={createOpen}
        onCancel={() => { setCreateOpen(false); setUploadedUrl(null); form.resetFields() }}
        footer={null}
        width={520}
        destroyOnHidden
      >
        <Form
          form={form}
          layout="vertical"
          onFinish={handleCreate}
          initialValues={{
            language_hints: ['zh'],
            max_prompt_audio_length: 20,
            enable_preprocess: false,
            authorized_use: false,
          }}
        >
          <Form.Item label="音频来源">
            <Space>
              <Button
                type={uploadMode === 'file' ? 'primary' : 'default'}
                size="small"
                icon={<CloudUploadOutlined />}
                onClick={() => setUploadMode('file')}
              >
                本地上传
              </Button>
              <Button
                type={uploadMode === 'url' ? 'primary' : 'default'}
                size="small"
                icon={<InfoCircleOutlined />}
                onClick={() => setUploadMode('url')}
              >
                URL 输入
              </Button>
            </Space>
          </Form.Item>

          {uploadMode === 'file' ? (
            <Form.Item
              label="上传音频文件"
              extra="支持 wav/mp3/m4a，≤10MB，建议 10-20 秒清晰人声"
            >
              <Upload.Dragger
                accept=".wav,.mp3,.m4a"
                maxCount={1}
                showUploadList={!!uploadedUrl}
                beforeUpload={handleFileUpload}
                disabled={uploading}
              >
                {uploading ? (
                  <div style={{ padding: '20px 0' }}>
                    <LoadingOutlined style={{ fontSize: 24, color: '#1677ff' }} />
                    <p style={{ marginTop: 8 }}>上传中...</p>
                  </div>
                ) : (
                  <>
                    <p className="ant-upload-drag-icon">
                      <CloudUploadOutlined />
                    </p>
                    <p className="ant-upload-text">点击或拖拽音频文件到此处</p>
                    <p className="ant-upload-hint">wav / mp3 / m4a</p>
                  </>
                )}
              </Upload.Dragger>
              {uploadedUrl && (
                <Tag color="success" icon={<CheckCircleOutlined />} style={{ marginTop: 8 }}>
                  音频已上传
                </Tag>
              )}
            </Form.Item>
          ) : (
            <Form.Item
              name="url"
              label="音频 URL"
              rules={[
                { required: uploadMode === 'url', message: '请输入音频文件 URL' },
                { type: 'url', message: '请输入有效的 URL' },
              ]}
              extra="公网可访问的 wav/mp3/m4a，≤10MB，建议 10-20 秒清晰人声"
            >
              <Input placeholder="https://oss.example.com/audio/sample.wav" />
            </Form.Item>
          )}

          <Form.Item
            name="prefix"
            label="音色名前缀"
            normalize={(value: string) => value?.toLowerCase()}
            rules={[
              { pattern: /^[a-z0-9]*$/, message: '仅限小写字母和数字' },
              { max: 10, message: '不超过 10 个字符' },
            ]}
            extra="生成格式: {模型}-{前缀}-{唯一标识}"
          >
            <Input placeholder="user01" maxLength={10} />
          </Form.Item>

          <Row gutter={16}>
            <Col span={12}>
              <Form.Item name="language_hints" label="语种提示">
                <Select
                  mode="multiple"
                  options={LANGUAGE_OPTIONS}
                  placeholder="选择语种"
                  maxCount={1}
                />
              </Form.Item>
            </Col>
            <Col span={12}>
              <Form.Item name="max_prompt_audio_length" label="参考音频时长(秒)">
                <InputNumber min={3} max={60} step={0.5} style={{ width: '100%' }} />
              </Form.Item>
            </Col>
          </Row>

          <Form.Item name="enable_preprocess" label="音频预处理" valuePropName="checked">
            <Switch checkedChildren="开启" unCheckedChildren="关闭" />
          </Form.Item>
          <Paragraph type="secondary" style={{ marginTop: -16, marginBottom: 16, fontSize: 12 }}>
            降噪 + 音频增强 + 音量规整。有背景噪音时建议开启
          </Paragraph>

          <Form.Item
            name="authorized_use"
            valuePropName="checked"
            rules={[{
              validator: (_, checked) => checked
                ? Promise.resolve()
                : Promise.reject(new Error('请先确认声音授权')),
            }]}
          >
            <Checkbox>我已获得声音所有者授权，并仅将合成内容用于合法用途</Checkbox>
          </Form.Item>

          <Form.Item>
            <Button type="primary" htmlType="submit" loading={creating} block size="large">
              {creating ? '创建中...' : '创建音色'}
            </Button>
          </Form.Item>
        </Form>
      </Modal>
      </div>
    </>
  )
}
