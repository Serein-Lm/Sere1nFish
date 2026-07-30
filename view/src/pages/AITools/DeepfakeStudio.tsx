import { useCallback, useEffect, useRef, useState, type CSSProperties } from 'react'
import {
  Alert,
  Button,
  Checkbox,
  Collapse,
  Image,
  Input,
  Modal,
  Segmented,
  Space,
  Spin,
  Statistic,
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
  type DeepfakeStatus,
} from '../../services/deepfakeService'
import {
  createRemoteMediaOutput,
  deleteRemoteMediaOutput,
  remoteMediaOutputViewerUrl,
  type RemoteMediaOutputSession,
} from '../../services/mediaOutputService'
import RealtimeVoicePanel from './RealtimeVoicePanel'

const { Text } = Typography

type StudioMode = 'image' | 'realtime'
type QualityProfile = 'quality' | 'balanced' | 'fast'

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

function useFilePreview(file: File | null): string {
  const [url, setUrl] = useState('')
  useEffect(() => {
    if (!file) {
      setUrl('')
      return
    }
    const next = URL.createObjectURL(file)
    setUrl(next)
    return () => URL.revokeObjectURL(next)
  }, [file])
  return url
}

function useFilePreviews(files: File[]): string[] {
  const [urls, setUrls] = useState<string[]>([])
  useEffect(() => {
    const next = files.map((file) => URL.createObjectURL(file))
    setUrls(next)
    return () => next.forEach((url) => URL.revokeObjectURL(url))
  }, [files])
  return urls
}

function ImagePicker({
  label,
  file,
  preview,
  onChange,
}: {
  label: string
  file: File | null
  preview: string
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
        {preview ? <img src={preview} alt={label} /> : <PictureOutlined />}
      </div>
      <Text type="secondary" ellipsis title={file?.name}>{file?.name || '未选择'}</Text>
    </div>
  )
}

function IdentityPicker({
  files,
  previews,
  maxCount,
  disabled,
  onAdd,
  onRemove,
}: {
  files: File[]
  previews: string[]
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
            <img src={previews[index]} alt={`身份图片 ${index + 1}`} />
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
  const [streamAspectRatio, setStreamAspectRatio] = useState(16 / 9)
  const [starting, setStarting] = useState(false)
  const [streaming, setStreaming] = useState(false)
  const [streamResult, setStreamResult] = useState('')
  const [sessionStatus, setSessionStatus] = useState<DeepfakeSessionStatus | null>(null)
  const [obsGuideOpen, setObsGuideOpen] = useState(false)
  const [obsCreating, setObsCreating] = useState(false)
  const [obsOutput, setObsOutput] = useState<RemoteMediaOutputSession | null>(null)
  const sourcePreviews = useFilePreviews(sourceFiles)
  const targetPreview = useFilePreview(targetFile)
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
  const obsViewerUrl = obsOutput ? remoteMediaOutputViewerUrl(obsOutput) : ''

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

  useEffect(() => {
    void loadStatus()
  }, [loadStatus])

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
    if (!streaming || !sessionIdRef.current) return
    const poll = window.setInterval(async () => {
      try {
        setSessionStatus(await getDeepfakeSession(sessionIdRef.current))
      } catch {
        // The WebSocket handler surfaces terminal errors.
      }
    }, 2000)
    return () => window.clearInterval(poll)
  }, [streaming])

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
      const session = await createDeepfakeSession(sourceFiles, realtimeWidth, realtimeProfile)
      effectiveRealtimeWidthRef.current = session.max_width
      if (session.max_width !== realtimeWidth) setRealtimeWidth(session.max_width)
      sessionIdRef.current = session.session_id
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
              previews={sourcePreviews}
              maxCount={maxSourceImages}
              onAdd={addSourceFile}
              onRemove={removeSourceFile}
            />
            <ImagePicker label="目标图片" file={targetFile} preview={targetPreview} onChange={setTargetFile} />
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
          <div className="deepfake-realtime-controls">
            <IdentityPicker
              files={sourceFiles}
              previews={sourcePreviews}
              maxCount={maxSourceImages}
              disabled={streaming || starting}
              onAdd={addSourceFile}
              onRemove={removeSourceFile}
            />
            <div className="deepfake-session-controls">
              <Text strong>质量</Text>
              <Segmented<QualityProfile>
                value={realtimeProfile}
                disabled={streaming || starting}
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
                disabled={streaming || starting}
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
              {streaming ? (
                <Button danger icon={<DisconnectOutlined />} onClick={() => void stopRealtime()}>
                  停止
                </Button>
              ) : (
                <Button type="primary" icon={<CameraOutlined />} loading={starting} onClick={startRealtime}>
                  启动摄像头
                </Button>
              )}
            </div>
          </div>
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
                <Text strong>远端 OBS 输出</Text>
                <Tag color={obsOutput ? 'success' : 'default'}>
                  {obsOutput ? '已就绪' : '未创建'}
                </Tag>
                {obsOutput && (
                  <Tag>{new Date(obsOutput.expires_at * 1000).toLocaleTimeString()} 到期</Tag>
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
            {obsOutput ? (
              <div className="deepfake-remote-output-url">
                <Input value={obsViewerUrl} readOnly aria-label="OBS 浏览器源地址" />
                <Space wrap>
                  <Button
                    icon={<CopyOutlined />}
                    onClick={async () => {
                      try {
                        await navigator.clipboard.writeText(obsViewerUrl)
                        messageApi.success('OBS 地址已复制')
                      } catch {
                        messageApi.error('复制失败，请手动复制地址')
                      }
                    }}
                  >
                    复制地址
                  </Button>
                  <Button
                    icon={<LinkOutlined />}
                    onClick={() => window.open(obsViewerUrl, '_blank', 'noopener,noreferrer')}
                  >
                    预览
                  </Button>
                  <Tooltip title={streaming ? '停止实时换脸后可关闭输出' : '立即使 OBS 地址失效'}>
                    <Button
                      danger
                      icon={<DeleteOutlined />}
                      disabled={streaming}
                      onClick={() => void closeObsOutput()}
                      aria-label="关闭远端 OBS 输出"
                    />
                  </Tooltip>
                </Space>
              </div>
            ) : (
              <Button
                type="primary"
                icon={<LaptopOutlined />}
                loading={obsCreating}
                onClick={() => void createObsOutput()}
              >
                创建 OBS 浏览器源
              </Button>
            )}
          </section>

          <Collapse
            className="deepfake-output-voice"
            items={[{
              key: 'voice-output',
              label: (
                <Space wrap>
                  <AudioOutlined />
                  <Text strong>目标音色与全双工对话</Text>
                  <Tag color={obsOutput ? 'cyan' : 'default'}>
                    {obsOutput ? '同步到 OBS' : '未绑定输出'}
                  </Tag>
                </Space>
              ),
              children: obsOutput ? (
                <RealtimeVoicePanel outputSessionId={obsOutput.session_id} />
              ) : (
                <Button
                  icon={<LaptopOutlined />}
                  loading={obsCreating}
                  onClick={() => void createObsOutput()}
                >
                  先创建 OBS 浏览器源
                </Button>
              ),
            }]}
          />
        </div>
      )}

      <Modal
        title={<Space><LaptopOutlined />远端 OBS 接入</Space>}
        open={obsGuideOpen}
        onCancel={() => setObsGuideOpen(false)}
        footer={<Button type="primary" onClick={() => setObsGuideOpen(false)}>知道了</Button>}
        width={580}
      >
        <ol className="deepfake-obs-guide">
          <li>创建远端输出并复制地址；启动摄像头后，换脸帧会直接进入该输出。</li>
          <li>在本机 OBS 添加“浏览器”来源，把复制的 HTTPS 地址粘贴到 URL。</li>
          <li>宽高建议填写 1280×720，并启用“通过 OBS 控制音频”。</li>
          <li>展开“目标音色与全双工对话”并开始会话，AI 回答音频会进入同一浏览器源。</li>
          <li>在 OBS 混音器确认浏览器源电平，再启动 OBS 虚拟摄像机。</li>
        </ol>
      </Modal>
    </div>
  )
}
