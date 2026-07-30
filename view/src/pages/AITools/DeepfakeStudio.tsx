import { useCallback, useEffect, useMemo, useRef, useState, type CSSProperties } from 'react'
import {
  Alert,
  Button,
  Checkbox,
  Collapse,
  Image,
  Input,
  InputNumber,
  Modal,
  Segmented,
  Select,
  Space,
  Spin,
  Statistic,
  Switch,
  Tag,
  Tooltip,
  Typography,
  Upload,
  message,
} from 'antd'
import {
  CameraOutlined,
  CloseOutlined,
  CloudUploadOutlined,
  CopyOutlined,
  DeleteOutlined,
  DisconnectOutlined,
  FullscreenOutlined,
  LaptopOutlined,
  LinkOutlined,
  PictureOutlined,
  ReloadOutlined,
  QuestionCircleOutlined,
  AudioOutlined,
  SwapOutlined,
} from '@ant-design/icons'
import {
  createDeepfakeSession,
  deleteDeepfakeSession,
  getDeepfakeSession,
  getDeepfakeStatus,
  openDeepfakeSocket,
  swapDeepfakeImage,
  type DeepfakeSessionStatus,
  type DeepfakeSession,
  type DeepfakeStatus,
} from '../../services/deepfakeService'
import {
  createRemoteMediaOutput,
  deleteRemoteMediaOutput,
  remoteMediaOutputViewerUrl,
  type RemoteMediaOutputSession,
} from '../../services/mediaOutputService'
import RealtimeVoicePanel from './RealtimeVoicePanel'
import {
  getRealtimeVoiceConfig,
  type RealtimeTurnMode,
  type RealtimeVoiceConfig,
} from '../../services/voiceRealtimeService'

const { Text } = Typography

type StudioMode = 'image' | 'realtime'
type QualityProfile = 'quality' | 'balanced' | 'fast'
type RealtimeTransport = 'obs_whip' | 'frame_ws'

const PROFILE_OPTIONS = [
  { value: 'quality', label: '效果优先' },
  { value: 'balanced', label: '均衡' },
  { value: 'fast', label: '视频通话' },
] satisfies Array<{ value: QualityProfile; label: string }>

const PROFILE_WIDTH_FALLBACK: Record<QualityProfile, number> = {
  quality: 1280,
  balanced: 960,
  fast: 640,
}

const REALTIME_WIDTHS = [640, 960, 1280] as const

const MEDIA_STATE_LABELS: Record<string, string> = {
  waiting_input: '等待 OBS',
  starting: '正在启动',
  live: '直连中',
  reconnecting: '正在重连',
  stopped: '已停止',
}

const AUDIO_STATE_LABELS: Record<string, string> = {
  disabled: '未启用',
  connecting: '连接语音服务',
  live: 'AI 音轨在线',
  reconnecting: '语音重连中',
  waiting_audio: '等待 OBS 麦克风',
  stopped: '已停止',
}

function FilePreviewImage({ file, alt }: { file: File; alt: string }) {
  const imageRef = useRef<HTMLImageElement | null>(null)
  useEffect(() => {
    const objectUrl = URL.createObjectURL(file)
    const image = imageRef.current
    if (image) image.src = objectUrl
    return () => {
      if (image) image.removeAttribute('src')
      URL.revokeObjectURL(objectUrl)
    }
  }, [file])
  return <img ref={imageRef} alt={alt} />
}

function ImagePicker({
  label,
  file,
  onChange,
}: {
  label: string
  file: File | null
  onChange: (file: File | null) => void
}) {
  return (
    <div className="deepfake-picker">
      <div className="deepfake-picker-head">
        <Text strong>{label}</Text>
        <Upload
          accept="image/jpeg,image/png,image/webp"
          maxCount={1}
          showUploadList={false}
          beforeUpload={(next) => {
            onChange(next)
            return false
          }}
        >
          <Button size="small" icon={<CloudUploadOutlined />}>选择图片</Button>
        </Upload>
      </div>
      <div className="deepfake-picker-preview">
        {file ? <FilePreviewImage file={file} alt={label} /> : <PictureOutlined />}
      </div>
      <Text type="secondary" ellipsis title={file?.name}>{file?.name || '未选择'}</Text>
    </div>
  )
}

function IdentityPicker({
  files,
  maxCount,
  disabled,
  onAdd,
  onRemove,
}: {
  files: File[]
  maxCount: number
  disabled?: boolean
  onAdd: (file: File) => void
  onRemove: (index: number) => void
}) {
  return (
    <div className="deepfake-picker deepfake-identity-picker">
      <div className="deepfake-picker-head">
        <Space size={6}>
          <Text strong>身份图片</Text>
          <Tag>{files.length}/{maxCount}</Tag>
        </Space>
        <Upload
          accept="image/jpeg,image/png,image/webp"
          multiple
          showUploadList={false}
          disabled={disabled || files.length >= maxCount}
          beforeUpload={(next) => {
            onAdd(next)
            return false
          }}
        >
          <Tooltip title="可添加正面及不同侧脸角度">
            <Button
              size="small"
              icon={<CloudUploadOutlined />}
              disabled={disabled || files.length >= maxCount}
            >
              添加
            </Button>
          </Tooltip>
        </Upload>
      </div>
      <div className="deepfake-identity-grid">
        {files.length ? files.map((file, index) => (
          <div className="deepfake-identity-item" key={`${file.name}-${file.size}-${file.lastModified}`}>
            <FilePreviewImage file={file} alt={`身份图片 ${index + 1}`} />
            <Tooltip title="移除">
              <Button
                type="text"
                size="small"
                icon={<CloseOutlined />}
                disabled={disabled}
                aria-label={`移除身份图片 ${index + 1}`}
                onClick={() => onRemove(index)}
              />
            </Tooltip>
          </div>
        )) : (
          <div className="deepfake-identity-empty"><PictureOutlined /></div>
        )}
      </div>
      <Text type="secondary" ellipsis title={files.map((file) => file.name).join(', ')}>
        {files.length ? files.map((file) => file.name).join('、') : '未选择'}
      </Text>
    </div>
  )
}

export default function DeepfakeStudio() {
  const [messageApi, messageContextHolder] = message.useMessage()
  const [mode, setMode] = useState<StudioMode>('image')
  const [status, setStatus] = useState<DeepfakeStatus | null>(null)
  const [statusLoading, setStatusLoading] = useState(false)
  const [sourceFiles, setSourceFiles] = useState<File[]>([])
  const [targetFile, setTargetFile] = useState<File | null>(null)
  const [authorized, setAuthorized] = useState(false)
  const [imageLoading, setImageLoading] = useState(false)
  const [imageResult, setImageResult] = useState('')
  const [imageInferenceMs, setImageInferenceMs] = useState(0)
  const [imageProfile, setImageProfile] = useState<QualityProfile>('quality')
  const [imageSourceCount, setImageSourceCount] = useState(0)
  const [imageSourceConsistency, setImageSourceConsistency] = useState(1)
  const [realtimeWidth, setRealtimeWidth] = useState(640)
  const [realtimeProfile, setRealtimeProfile] = useState<QualityProfile>('fast')
  const [realtimeTransport, setRealtimeTransport] = useState<RealtimeTransport>('frame_ws')
  const [streamAspectRatio, setStreamAspectRatio] = useState(16 / 9)
  const [starting, setStarting] = useState(false)
  const [streaming, setStreaming] = useState(false)
  const [streamResult, setStreamResult] = useState('')
  const [sessionStatus, setSessionStatus] = useState<DeepfakeSessionStatus | null>(null)
  const [directSession, setDirectSession] = useState<DeepfakeSession | null>(null)
  const [voiceConfig, setVoiceConfig] = useState<RealtimeVoiceConfig | null>(null)
  const [voiceConfigLoading, setVoiceConfigLoading] = useState(false)
  const [integratedVoice, setIntegratedVoice] = useState(true)
  const [integratedVoiceId, setIntegratedVoiceId] = useState('')
  const [integratedVoiceMode, setIntegratedVoiceMode] = useState<Exclude<RealtimeTurnMode, 'manual'>>('smart_turn')
  const [integratedVoiceInstructions, setIntegratedVoiceInstructions] = useState('')
  const [integratedVoiceHistory, setIntegratedVoiceHistory] = useState(20)
  const [obsGuideOpen, setObsGuideOpen] = useState(false)
  const [obsCreating, setObsCreating] = useState(false)
  const [obsOutput, setObsOutput] = useState<RemoteMediaOutputSession | null>(null)
  const videoRef = useRef<HTMLVideoElement | null>(null)
  const canvasRef = useRef<HTMLCanvasElement | null>(null)
  const outputViewRef = useRef<HTMLDivElement | null>(null)
  const socketRef = useRef<WebSocket | null>(null)
  const mediaRef = useRef<MediaStream | null>(null)
  const sessionIdRef = useRef('')
  const outputUrlRef = useRef('')
  const obsOutputRef = useRef<RemoteMediaOutputSession | null>(null)
  const imageUrlRef = useRef('')
  const captureTimerRef = useRef<number | null>(null)
  const effectiveRealtimeWidthRef = useRef(640)
  const profileDefaultsAppliedRef = useRef(false)
  const maxSourceImages = status?.max_source_images || 4
  const realtimeProfileMaxWidth = (
    status?.profiles.find((profile) => profile.id === realtimeProfile)?.max_width
    || PROFILE_WIDTH_FALLBACK[realtimeProfile]
  )
  const realtimeWidthOptions = REALTIME_WIDTHS
    .filter((width) => width <= realtimeProfileMaxWidth)
    .map((width) => ({ value: width, label: String(width) }))
  const obsViewerUrl = obsOutput
    ? remoteMediaOutputViewerUrl(
      obsOutput,
      status?.media_transport?.public_base_url || window.location.origin,
    )
    : ''
  const integratedVoiceOptions = useMemo(() => {
    if (!voiceConfig) return []
    return [
      {
        label: '系统音色',
        options: voiceConfig.system_voices.map((item) => ({
          value: item.voice_id,
          label: item.label,
        })),
      },
      ...(voiceConfig.cloned_voices.length ? [{
        label: '复刻音色',
        options: voiceConfig.cloned_voices.map((item) => ({
          value: item.voice_id,
          label: item.label,
        })),
      }] : []),
    ]
  }, [voiceConfig])

  const addSourceFile = useCallback((file: File) => {
    setSourceFiles((current) => {
      if (current.length >= maxSourceImages) return current
      const duplicate = current.some((item) => (
        item.name === file.name && item.size === file.size && item.lastModified === file.lastModified
      ))
      return duplicate ? current : [...current, file]
    })
  }, [maxSourceImages])

  const removeSourceFile = useCallback((index: number) => {
    setSourceFiles((current) => current.filter((_, currentIndex) => currentIndex !== index))
  }, [])

  const loadStatus = useCallback(async () => {
    setStatusLoading(true)
    try {
      const next = await getDeepfakeStatus()
      setStatus(next)
      if (!next.media_transport?.audio_supported) setIntegratedVoice(false)
      if (!profileDefaultsAppliedRef.current) {
        const imageDefault = PROFILE_OPTIONS.some((option) => option.value === next.default_image_profile)
          ? next.default_image_profile as QualityProfile
          : 'quality'
        const realtimeDefault = PROFILE_OPTIONS.some((option) => option.value === next.default_realtime_profile)
          ? next.default_realtime_profile as QualityProfile
          : 'fast'
        const realtimeDefaultWidth = (
          next.profiles.find((profile) => profile.id === realtimeDefault)?.max_width
          || PROFILE_WIDTH_FALLBACK[realtimeDefault]
        )
        setImageProfile(imageDefault)
        setRealtimeProfile(realtimeDefault)
        setRealtimeWidth(realtimeDefaultWidth)
        effectiveRealtimeWidthRef.current = realtimeDefaultWidth
        profileDefaultsAppliedRef.current = true
      }
    } catch (error) {
      messageApi.error(error instanceof Error ? error.message : 'GPU 状态读取失败')
    } finally {
      setStatusLoading(false)
    }
  }, [messageApi])

  const loadVoiceConfig = useCallback(async () => {
    setVoiceConfigLoading(true)
    try {
      const next = await getRealtimeVoiceConfig()
      setVoiceConfig(next)
      setIntegratedVoiceId((current) => current || next.default_voice)
      setIntegratedVoiceMode(next.default_mode === 'server_vad' ? 'server_vad' : 'smart_turn')
      setIntegratedVoiceInstructions(next.default_instructions || '')
      setIntegratedVoiceHistory(next.max_history_turns)
    } catch (error) {
      setIntegratedVoice(false)
      messageApi.error(error instanceof Error ? error.message : '全双工语音配置加载失败')
    } finally {
      setVoiceConfigLoading(false)
    }
  }, [messageApi])

  useEffect(() => {
    void loadStatus()
    void loadVoiceConfig()
  }, [loadStatus, loadVoiceConfig])

  const setOutputBlob = useCallback((blob: Blob) => {
    const previous = outputUrlRef.current
    const next = URL.createObjectURL(blob)
    outputUrlRef.current = next
    setStreamResult(next)
    if (previous) window.setTimeout(() => URL.revokeObjectURL(previous), 1000)
  }, [])

  const createObsOutput = useCallback(async (): Promise<RemoteMediaOutputSession | null> => {
    if (obsOutputRef.current) return obsOutputRef.current
    setObsCreating(true)
    try {
      const next = await createRemoteMediaOutput()
      obsOutputRef.current = next
      setObsOutput(next)
      messageApi.success('远端 OBS 输出已创建')
      return next
    } catch (error) {
      messageApi.error(error instanceof Error ? error.message : '远端 OBS 输出创建失败')
      return null
    } finally {
      setObsCreating(false)
    }
  }, [messageApi])

  const closeObsOutput = useCallback(async () => {
    const current = obsOutputRef.current
    if (!current) return
    try {
      await deleteRemoteMediaOutput(current.session_id)
      obsOutputRef.current = null
      setObsOutput(null)
    } catch (error) {
      messageApi.error(error instanceof Error ? error.message : '远端 OBS 输出关闭失败')
    }
  }, [messageApi])

  const captureFrame = useCallback(() => {
    const socket = socketRef.current
    const video = videoRef.current
    const canvas = canvasRef.current
    if (!socket || socket.readyState !== WebSocket.OPEN || !video || !canvas || video.videoWidth === 0) return
    const width = Math.min(effectiveRealtimeWidthRef.current, video.videoWidth)
    const height = Math.max(64, Math.round(video.videoHeight * (width / video.videoWidth)))
    canvas.width = width
    canvas.height = height
    const context = canvas.getContext('2d', { alpha: false })
    if (!context) return
    context.drawImage(video, 0, 0, width, height)
    canvas.toBlob(async (blob) => {
      if (!blob || socket.readyState !== WebSocket.OPEN) return
      socket.send(await blob.arrayBuffer())
    }, 'image/jpeg', 0.92)
  }, [])

  const stopRealtime = useCallback(async () => {
    if (captureTimerRef.current !== null) {
      window.clearTimeout(captureTimerRef.current)
      captureTimerRef.current = null
    }
    const socket = socketRef.current
    socketRef.current = null
    if (socket && socket.readyState < WebSocket.CLOSING) socket.close(1000, 'client stop')
    mediaRef.current?.getTracks().forEach((track) => track.stop())
    mediaRef.current = null
    if (videoRef.current) videoRef.current.srcObject = null
    const sessionId = sessionIdRef.current
    sessionIdRef.current = ''
    setStreaming(false)
    setDirectSession(null)
    setSessionStatus(null)
    if (sessionId) {
      try {
        await deleteDeepfakeSession(sessionId)
      } catch {
        // Remote sessions also expire automatically.
      }
    }
  }, [])

  useEffect(() => () => {
    void stopRealtime()
    if (outputUrlRef.current) URL.revokeObjectURL(outputUrlRef.current)
    if (imageUrlRef.current) URL.revokeObjectURL(imageUrlRef.current)
    const output = obsOutputRef.current
    obsOutputRef.current = null
    if (output) void deleteRemoteMediaOutput(output.session_id, true)
  }, [stopRealtime])

  useEffect(() => {
    if ((!streaming && !directSession) || !sessionIdRef.current) return
    const poll = window.setInterval(async () => {
      try {
        setSessionStatus(await getDeepfakeSession(sessionIdRef.current))
      } catch {
        // The WebSocket handler surfaces terminal errors.
      }
    }, 2000)
    return () => window.clearInterval(poll)
  }, [directSession, streaming])

  const runImageSwap = async () => {
    if (!sourceFiles.length || !targetFile || !authorized) {
      messageApi.warning('请选择身份图片、目标图片并确认素材授权')
      return
    }
    setImageLoading(true)
    try {
      const result = await swapDeepfakeImage(sourceFiles, targetFile, 1280, imageProfile)
      if (imageUrlRef.current) URL.revokeObjectURL(imageUrlRef.current)
      const next = URL.createObjectURL(result.blob)
      imageUrlRef.current = next
      setImageResult(next)
      setImageInferenceMs(result.inferenceMs)
      setImageSourceCount(result.sourceCount)
      setImageSourceConsistency(result.sourceConsistency)
      messageApi.success('换脸完成')
    } catch (error) {
      messageApi.error(error instanceof Error ? error.message : '换脸失败')
    } finally {
      setImageLoading(false)
    }
  }

  const openOutputFullscreen = async () => {
    const output = outputViewRef.current
    if (!output || !streamResult) return
    try {
      if (document.fullscreenElement) {
        await document.exitFullscreen()
      } else {
        await output.requestFullscreen()
      }
    } catch {
      messageApi.error('无法进入全屏模式')
    }
  }

  const startRealtime = async () => {
    if (!sourceFiles.length || !authorized) {
      messageApi.warning('请选择身份图片并确认素材授权')
      return
    }
    setStarting(true)
    try {
      const output = await createObsOutput()
      if (!output) return
      const media = await navigator.mediaDevices.getUserMedia({
        video: { width: { ideal: realtimeWidth }, height: { ideal: 720 }, facingMode: 'user' },
        audio: false,
      })
      mediaRef.current = media
      if (!videoRef.current) throw new Error('摄像头预览未初始化')
      videoRef.current.srcObject = media
      await videoRef.current.play()
      if (videoRef.current.videoWidth > 0 && videoRef.current.videoHeight > 0) {
        setStreamAspectRatio(videoRef.current.videoWidth / videoRef.current.videoHeight)
      }
      const session = await createDeepfakeSession(sourceFiles, realtimeWidth, realtimeProfile, 'frame_ws')
      effectiveRealtimeWidthRef.current = session.max_width
      if (session.max_width !== realtimeWidth) setRealtimeWidth(session.max_width)
      sessionIdRef.current = session.session_id
      if (!session.stream_path) throw new Error('GPU 未返回浏览器实时流地址')
      const socket = openDeepfakeSocket(session.stream_path, output.session_id)
      socket.binaryType = 'blob'
      socketRef.current = socket
      socket.onmessage = (event) => {
        if (event.data instanceof Blob) {
          setOutputBlob(event.data)
          captureTimerRef.current = window.setTimeout(captureFrame, 0)
          return
        }
        try {
          const payload = JSON.parse(String(event.data)) as { type?: string; message?: string }
          if (payload.type === 'ready') {
            setStreaming(true)
            captureFrame()
          } else if (payload.type === 'blocked' || payload.type === 'error') {
            messageApi.warning(payload.message || '当前帧未处理')
            captureTimerRef.current = window.setTimeout(captureFrame, 250)
          }
        } catch {
          messageApi.error('实时流返回了无效数据')
        }
      }
      socket.onerror = () => messageApi.error('实时流连接失败')
      socket.onclose = () => setStreaming(false)
    } catch (error) {
      await stopRealtime()
      messageApi.error(error instanceof Error ? error.message : '摄像头启动失败')
    } finally {
      setStarting(false)
    }
  }

  const startObsDirect = async () => {
    if (!sourceFiles.length || !authorized) {
      messageApi.warning('请选择身份图片并确认素材授权')
      return
    }
    if (!status?.media_transport?.enabled) {
      messageApi.error('GPU 尚未启用 OBS 直连媒体服务')
      return
    }
    if (integratedVoice && (!status.media_transport.audio_supported || !voiceConfig || !integratedVoiceId)) {
      messageApi.error('GPU 音频桥接或全双工音色尚未就绪')
      return
    }
    setStarting(true)
    try {
      const session = await createDeepfakeSession(
        sourceFiles,
        realtimeWidth,
        realtimeProfile,
        'obs_whip',
        integratedVoice && voiceConfig ? {
          model: voiceConfig.model,
          voice: integratedVoiceId,
          mode: integratedVoiceMode,
          instructions: integratedVoiceInstructions,
          maxHistoryTurns: integratedVoiceHistory,
        } : undefined,
      )
      if (!session.media) throw new Error('GPU 未返回 OBS 直连配置')
      effectiveRealtimeWidthRef.current = session.max_width
      if (session.max_width !== realtimeWidth) setRealtimeWidth(session.max_width)
      sessionIdRef.current = session.session_id
      setDirectSession(session)
      setSessionStatus(await getDeepfakeSession(session.session_id))
      messageApi.success(integratedVoice ? 'OBS 音视频会话已创建，等待推流' : 'OBS 视频会话已创建，等待推流')
    } catch (error) {
      await stopRealtime()
      messageApi.error(error instanceof Error ? error.message : 'OBS 直连会话创建失败')
    } finally {
      setStarting(false)
    }
  }

  return (
    <div className="deepfake-studio">
      {messageContextHolder}
      <div className="deepfake-toolbar">
        <Segmented<StudioMode>
          value={mode}
          onChange={setMode}
          options={[
            { value: 'image', label: '图片验证', icon: <PictureOutlined /> },
            { value: 'realtime', label: '实时摄像头', icon: <CameraOutlined /> },
          ]}
        />
        <Space wrap>
          {status?.model && <Tag color="blue">{status.model}</Tag>}
          {status?.gpu?.name && <Tag>{status.gpu.name}</Tag>}
          <Tag color={status?.ok ? 'success' : 'error'}>{status?.ok ? 'GPU 在线' : 'GPU 离线'}</Tag>
          <Button icon={<ReloadOutlined />} loading={statusLoading} onClick={loadStatus} aria-label="刷新 GPU 状态" />
        </Space>
      </div>

      {status && (
        <div className="deepfake-stats">
          <Statistic title="显存" value={status.gpu.memory_used_mb || 0} suffix={`/ ${status.gpu.memory_total_mb || 0} MB`} />
          <Statistic title="GPU" value={status.gpu.utilization_percent || 0} suffix="%" />
          <Statistic title="实时会话" value={status.active_sessions} suffix={`/ ${status.max_sessions}`} />
          <Statistic title="运行帧率" value={status.runtime_average_fps || 0} precision={1} suffix="FPS" />
        </div>
      )}

      <Alert type="warning" showIcon title="当前模型仅限已授权的非商用素材" />

      {mode === 'image' ? (
        <div className="deepfake-workspace">
          <div className="deepfake-input-grid">
            <IdentityPicker
              files={sourceFiles}
              maxCount={maxSourceImages}
              onAdd={addSourceFile}
              onRemove={removeSourceFile}
            />
            <ImagePicker label="目标图片" file={targetFile} onChange={setTargetFile} />
          </div>
          <div className="deepfake-actions">
            <Space wrap>
              <Text strong>质量</Text>
              <Segmented<QualityProfile>
                value={imageProfile}
                disabled={imageLoading}
                onChange={setImageProfile}
                options={PROFILE_OPTIONS}
              />
            </Space>
            <Space wrap>
              <Checkbox checked={authorized} onChange={(event) => setAuthorized(event.target.checked)}>
                我确认已获得人脸素材授权
              </Checkbox>
              <Button type="primary" icon={<SwapOutlined />} loading={imageLoading} onClick={runImageSwap}>
                开始换脸
              </Button>
            </Space>
          </div>
          <div className="deepfake-result">
            {imageLoading ? <Spin /> : imageResult ? <Image src={imageResult} alt="换脸结果" /> : <PictureOutlined />}
          </div>
          {imageResult && (
            <Space wrap>
              <Tag>{imageInferenceMs.toFixed(0)} ms</Tag>
              <Tag>{imageSourceCount} 张参考图</Tag>
              {imageSourceCount > 1 && <Tag>身份一致度 {imageSourceConsistency.toFixed(2)}</Tag>}
            </Space>
          )}
        </div>
      ) : (
        <div className="deepfake-workspace">
          <div className="deepfake-transport-bar">
            <Space wrap>
              <Text strong>视频输入</Text>
              <Segmented<RealtimeTransport>
                value={realtimeTransport}
                disabled={Boolean(directSession) || streaming || starting}
                onChange={setRealtimeTransport}
                options={[
                  { value: 'frame_ws', label: '浏览器采集', icon: <CameraOutlined /> },
                  { value: 'obs_whip', label: 'OBS WHIP', icon: <LaptopOutlined /> },
                ]}
              />
              {realtimeTransport === 'obs_whip' && (
                <Space size={4}>
                  <Tag color="warning">实验模式</Tag>
                  <Tag color={status?.media_transport?.enabled ? 'success' : 'error'}>
                    {status?.media_transport?.enabled ? 'GPU 媒体服务在线' : 'GPU 媒体服务未启用'}
                  </Tag>
                </Space>
              )}
            </Space>
            <Tooltip title="OBS 接入说明">
              <Button
                type="text"
                icon={<QuestionCircleOutlined />}
                onClick={() => setObsGuideOpen(true)}
                aria-label="查看 OBS 接入说明"
              />
            </Tooltip>
          </div>
          <div className="deepfake-realtime-controls">
            <IdentityPicker
              files={sourceFiles}
              maxCount={maxSourceImages}
              disabled={Boolean(directSession) || streaming || starting}
              onAdd={addSourceFile}
              onRemove={removeSourceFile}
            />
            <div className="deepfake-session-controls">
              <Text strong>质量</Text>
              <Segmented<QualityProfile>
                value={realtimeProfile}
                disabled={Boolean(directSession) || streaming || starting}
                onChange={(value) => {
                  const maxWidth = (
                    status?.profiles.find((profile) => profile.id === value)?.max_width
                    || PROFILE_WIDTH_FALLBACK[value]
                  )
                  setRealtimeProfile(value)
                  setRealtimeWidth(maxWidth)
                  effectiveRealtimeWidthRef.current = maxWidth
                }}
                options={PROFILE_OPTIONS}
              />
              <Text strong>传输宽度</Text>
              <Segmented
                value={realtimeWidth}
                disabled={Boolean(directSession) || streaming || starting}
                onChange={(value) => {
                  const width = Number(value)
                  setRealtimeWidth(width)
                  effectiveRealtimeWidthRef.current = width
                }}
                options={realtimeWidthOptions}
              />
              <Checkbox checked={authorized} onChange={(event) => setAuthorized(event.target.checked)}>
                我确认已获得人脸素材授权
              </Checkbox>
              {streaming || directSession ? (
                <Button danger icon={<DisconnectOutlined />} onClick={() => void stopRealtime()}>
                  停止
                </Button>
              ) : (
                <Button
                  type="primary"
                  icon={realtimeTransport === 'obs_whip' ? <LaptopOutlined /> : <CameraOutlined />}
                  loading={starting}
                  onClick={realtimeTransport === 'obs_whip' ? startObsDirect : startRealtime}
                >
                  {realtimeTransport === 'obs_whip' ? '创建 OBS 直连' : '启动浏览器摄像头'}
                </Button>
              )}
            </div>
          </div>
          {realtimeTransport === 'obs_whip' && !directSession && (
            <Collapse
              className="deepfake-integrated-voice"
              defaultActiveKey={['integrated-voice']}
              items={[{
                key: 'integrated-voice',
                label: (
                  <Space wrap>
                    <AudioOutlined />
                    <Text strong>音频合流</Text>
                    <Tag color={integratedVoice ? 'cyan' : 'default'}>{integratedVoice ? 'AI 音轨' : '静音输出'}</Tag>
                    {voiceConfig?.model && integratedVoice && <Tag>{voiceConfig.model}</Tag>}
                  </Space>
                ),
                extra: (
                  <Switch
                    checked={integratedVoice}
                    disabled={starting || voiceConfigLoading || !status?.media_transport?.audio_supported}
                    loading={voiceConfigLoading}
                    onChange={setIntegratedVoice}
                    aria-label="启用 AI 音频合流"
                    onClick={(_, event) => event.stopPropagation()}
                  />
                ),
                children: integratedVoice ? (
                  <div className="deepfake-voice-settings">
                    <label>
                      <Text type="secondary">输出音色</Text>
                      <Select
                        value={integratedVoiceId || undefined}
                        options={integratedVoiceOptions}
                        onChange={setIntegratedVoiceId}
                        disabled={starting}
                        loading={voiceConfigLoading}
                        showSearch
                        optionFilterProp="label"
                      />
                    </label>
                    <label>
                      <Text type="secondary">轮次检测</Text>
                      <Select
                        value={integratedVoiceMode}
                        onChange={setIntegratedVoiceMode}
                        disabled={starting}
                        options={[
                          { value: 'smart_turn', label: '智能轮次' },
                          { value: 'server_vad', label: '快速检测' },
                        ]}
                      />
                    </label>
                    <label>
                      <Text type="secondary">历史轮次</Text>
                      <InputNumber
                        id="deepfake-voice-history"
                        name="deepfake_voice_history"
                        min={1}
                        max={50}
                        value={integratedVoiceHistory}
                        onChange={(value) => setIntegratedVoiceHistory(value || 20)}
                        disabled={starting}
                      />
                    </label>
                    <label className="deepfake-voice-instructions">
                      <Text type="secondary">会话指令</Text>
                      <Input.TextArea
                        id="deepfake-voice-instructions"
                        name="deepfake_voice_instructions"
                        value={integratedVoiceInstructions}
                        onChange={(event) => setIntegratedVoiceInstructions(event.target.value)}
                        maxLength={4000}
                        autoSize={{ minRows: 2, maxRows: 4 }}
                        disabled={starting}
                      />
                    </label>
                  </div>
                ) : null,
              }]}
            />
          )}
          {realtimeTransport === 'obs_whip' ? (
            directSession?.media ? (
              <>
                <section className="deepfake-direct-output">
                <div className="deepfake-remote-output-head">
                  <Space wrap>
                    <LaptopOutlined />
                    <Text strong>OBS 与 GPU 音视频直连</Text>
                    <Tag color={sessionStatus?.media?.state === 'live' ? 'success' : 'processing'}>
                      {MEDIA_STATE_LABELS[sessionStatus?.media?.state || 'waiting_input'] || '等待 OBS'}
                    </Tag>
                    <Tag>{directSession.media.recommended.width}px</Tag>
                    <Tag>{directSession.media.recommended.fps} FPS</Tag>
                    {directSession.media.audio?.enabled && <Tag color="cyan">H.264 + Opus</Tag>}
                  </Space>
                </div>

                <div className="deepfake-direct-fields">
                  <Text type="secondary">WHIP 地址</Text>
                  <Input value={directSession.media.publish_url} readOnly />
                  <Tooltip title="复制 WHIP 地址">
                    <Button
                      icon={<CopyOutlined />}
                      aria-label="复制 WHIP 地址"
                      onClick={() => void navigator.clipboard.writeText(directSession.media!.publish_url).then(
                        () => messageApi.success('WHIP 地址已复制'),
                        () => messageApi.error('复制失败'),
                      )}
                    />
                  </Tooltip>

                  <Text type="secondary">Bearer Token</Text>
                  <Input.Password value={directSession.media.publish_token} readOnly visibilityToggle />
                  <Tooltip title="复制 Bearer Token">
                    <Button
                      icon={<CopyOutlined />}
                      aria-label="复制 Bearer Token"
                      onClick={() => void navigator.clipboard.writeText(directSession.media!.publish_token).then(
                        () => messageApi.success('Bearer Token 已复制'),
                        () => messageApi.error('复制失败'),
                      )}
                    />
                  </Tooltip>

                  <Text type="secondary">换脸音视频输出</Text>
                  <Input value={directSession.media.viewer_url} readOnly />
                  <Space.Compact>
                    <Tooltip title="复制 OBS 浏览器源地址">
                      <Button
                        icon={<CopyOutlined />}
                        aria-label="复制换脸音视频输出地址"
                        onClick={() => void navigator.clipboard.writeText(directSession.media!.viewer_url).then(
                          () => messageApi.success('换脸输出地址已复制'),
                          () => messageApi.error('复制失败'),
                        )}
                      />
                    </Tooltip>
                    <Tooltip title="打开输出预览">
                      <Button
                        icon={<LinkOutlined />}
                        aria-label="预览换脸输出"
                        onClick={() => window.open(directSession.media!.viewer_url, '_blank', 'noopener,noreferrer')}
                      />
                    </Tooltip>
                  </Space.Compact>
                </div>

                <Space wrap>
                  <Tag>{sessionStatus?.media?.processed_frames || 0} 处理帧</Tag>
                  <Tag>{sessionStatus?.media?.dropped_frames || 0} 丢弃帧</Tag>
                  <Tag>{sessionStatus?.media?.last_inference_ms?.toFixed(0) || '0'} ms</Tag>
                  <Tag>{sessionStatus?.media?.reconnects || 0} 次重连</Tag>
                  {directSession.media.audio?.enabled && (
                    <Tag color={sessionStatus?.media?.audio?.state === 'live' ? 'success' : 'processing'}>
                      {AUDIO_STATE_LABELS[sessionStatus?.media?.audio?.state || 'connecting'] || '音频初始化中'}
                    </Tag>
                  )}
                  {directSession.media.audio?.enabled && (
                    <Tag>{Math.round((sessionStatus?.media?.audio?.output_bytes || 0) / 1024)} KB AI 音频</Tag>
                  )}
                </Space>
                {sessionStatus?.media?.publish?.attempts === 0 && (
                  <Alert
                    type="info"
                    showIcon
                    title="服务端尚未收到 OBS 的 WHIP 发布请求"
                    description="请检查 OBS 版本、WHIP 服务器地址和本机安全软件；网页预览可访问只代表 WHEP 拉流正常。"
                  />
                )}
                {(sessionStatus?.media?.publish?.attempts || 0) > 0
                  && sessionStatus?.media?.publish?.last_authorized === false && (
                  <Alert
                    type="error"
                    showIcon
                    title="OBS 已到达服务器，但 Bearer Token 鉴权失败"
                    description="请从当前会话重新复制 Token，不要添加 Bearer 前缀，也不要使用上一次会话的 Token。"
                  />
                )}
                {sessionStatus?.media?.publish?.last_authorized === true
                  && sessionStatus?.media?.state === 'waiting_input' && (
                  <Alert
                    type="warning"
                    showIcon
                    title="WHIP 鉴权成功，正在等待 WebRTC 媒体连接"
                    description="请检查客户端到 GPU 节点 8189/TCP 和 8189/UDP 的放行规则。"
                  />
                )}
                {sessionStatus?.media?.last_error && (
                  <Alert type="warning" showIcon title={sessionStatus.media.last_error} />
                )}
                {sessionStatus?.media?.audio?.last_error && (
                  <Alert type="warning" showIcon title={sessionStatus.media.audio.last_error} />
                )}
                </section>
              </>
            ) : null
          ) : (
            <>
              <div
                className="deepfake-stream-grid"
                style={{ '--deepfake-stream-aspect': streamAspectRatio } as CSSProperties}
              >
                <div className="deepfake-stream-view deepfake-stream-source">
                  <video ref={videoRef} muted playsInline className="is-mirrored" />
                  <span className="deepfake-stream-label">原始画面</span>
                </div>
                <div ref={outputViewRef} className="deepfake-stream-view deepfake-stream-output">
                  {streamResult ? <img src={streamResult} alt="实时换脸画面" className="is-mirrored" /> : <CameraOutlined />}
                  <span className="deepfake-stream-label">换脸画面</span>
                  <Tooltip title="全屏查看换脸画面">
                    <Button
                      type="text"
                      icon={<FullscreenOutlined />}
                      className="deepfake-stream-fullscreen"
                      disabled={!streamResult}
                      aria-label="全屏查看换脸画面"
                      onClick={() => void openOutputFullscreen()}
                    />
                  </Tooltip>
                </div>
              </div>
              <canvas ref={canvasRef} className="deepfake-capture-canvas" />
              <Space wrap>
                <Tag color={streaming ? 'success' : 'default'}>{streaming ? '已连接' : '未连接'}</Tag>
                <Tag>{PROFILE_OPTIONS.find((item) => item.value === realtimeProfile)?.label}</Tag>
                <Tag>{sourceFiles.length} 张参考图</Tag>
                <Tag>{sessionStatus?.measured_fps?.toFixed(1) || '0.0'} FPS</Tag>
                <Tag>{sessionStatus?.average_inference_ms?.toFixed(0) || '0'} ms</Tag>
                <Tag>{sessionStatus?.frame_count || 0} 帧</Tag>
              </Space>

              <section className="deepfake-remote-output">
                <div className="deepfake-remote-output-head">
                  <Space wrap>
                    <LaptopOutlined />
                    <Text strong>OBS 浏览器源输出</Text>
                    <Tag color={obsOutput ? 'success' : 'default'}>{obsOutput ? '已就绪' : '未创建'}</Tag>
                  </Space>
                </div>
                {obsOutput ? (
                  <div className="deepfake-remote-output-url">
                    <Input value={obsViewerUrl} readOnly aria-label="OBS 浏览器源地址" />
                    <Space wrap>
                      <Button icon={<CopyOutlined />} onClick={() => void navigator.clipboard.writeText(obsViewerUrl)}>复制地址</Button>
                      <Button icon={<LinkOutlined />} onClick={() => window.open(obsViewerUrl, '_blank', 'noopener,noreferrer')}>预览</Button>
                      <Button danger icon={<DeleteOutlined />} disabled={streaming} onClick={() => void closeObsOutput()} aria-label="关闭 OBS 浏览器源输出" />
                    </Space>
                  </div>
                ) : (
                  <Button type="primary" icon={<LaptopOutlined />} loading={obsCreating} onClick={() => void createObsOutput()}>
                    创建 OBS 浏览器源输出
                  </Button>
                )}
              </section>

              <Collapse
                className="deepfake-output-voice"
                items={[{
                  key: 'voice-output',
                  label: <Space wrap><AudioOutlined /><Text strong>目标音色与全双工对话</Text></Space>,
                  children: obsOutput ? (
                    <RealtimeVoicePanel outputSessionId={obsOutput.session_id} />
                  ) : (
                    <Button icon={<LaptopOutlined />} loading={obsCreating} onClick={() => void createObsOutput()}>
                      先创建 OBS 浏览器源输出
                    </Button>
                  ),
                }]}
              />
            </>
          )}
        </div>
      )}

      <Modal
        title={<Space><LaptopOutlined />远端 OBS 接入</Space>}
        open={obsGuideOpen}
        onCancel={() => setObsGuideOpen(false)}
        footer={<Button type="primary" onClick={() => setObsGuideOpen(false)}>知道了</Button>}
        width={640}
      >
        {realtimeTransport === 'obs_whip' ? (
          <ol className="deepfake-obs-guide">
            <li>创建“原始摄像头”场景，添加本机的视频采集设备和“音频输入采集”，并保持它作为 OBS 节目画面。</li>
            <li>在“设置 → 直播”选择 WHIP，分别填写页面生成的 WHIP 地址和 Bearer Token。</li>
            <li>视频编码选择 H.264，确认 OBS 混音器中的麦克风有电平后点击“开始直播”。</li>
            <li>创建“换脸输出”场景，添加浏览器来源并填写页面生成的换脸音视频输出地址，启用“通过 OBS 控制音频”。</li>
            <li>Mac 安装 BlackHole 2ch；在 OBS 音频设置中将监听设备设为 BlackHole 2ch，并把换脸浏览器源设为“仅监听（输出静音）”。</li>
            <li>打开虚拟摄像机设置，输出类型选择“来源”，指定换脸浏览器源；通话软件的视频选 OBS Virtual Camera，麦克风选 BlackHole 2ch。</li>
            <li>不需要开始录制；节目画面保持原始摄像头，避免将换脸结果或 AI 声音再次推回 GPU。</li>
          </ol>
        ) : (
          <ol className="deepfake-obs-guide">
            <li>选择身份图片并确认授权，点击“启动浏览器摄像头”，允许当前网页使用 Mac 摄像头。</li>
            <li>页面会自动创建 OBS 浏览器源输出，并持续把摄像头画面送到 GPU 换脸。</li>
            <li>复制“OBS 浏览器源输出”地址，在 OBS 添加浏览器来源并粘贴该地址。</li>
            <li>OBS 启动虚拟摄像机即可供通话软件选择，不需要开始直播或录制。</li>
          </ol>
        )}
      </Modal>
    </div>
  )
}
