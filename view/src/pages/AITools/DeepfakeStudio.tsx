import { useCallback, useEffect, useRef, useState, type CSSProperties } from 'react'
import {
  Alert,
  Button,
  Checkbox,
  Image,
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
  CloudUploadOutlined,
  DisconnectOutlined,
  FullscreenOutlined,
  PictureOutlined,
  ReloadOutlined,
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

const { Text } = Typography

type StudioMode = 'image' | 'realtime'

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

export default function DeepfakeStudio() {
  const [mode, setMode] = useState<StudioMode>('image')
  const [status, setStatus] = useState<DeepfakeStatus | null>(null)
  const [statusLoading, setStatusLoading] = useState(false)
  const [sourceFile, setSourceFile] = useState<File | null>(null)
  const [targetFile, setTargetFile] = useState<File | null>(null)
  const [authorized, setAuthorized] = useState(false)
  const [imageLoading, setImageLoading] = useState(false)
  const [imageResult, setImageResult] = useState('')
  const [imageInferenceMs, setImageInferenceMs] = useState(0)
  const [realtimeWidth, setRealtimeWidth] = useState(960)
  const [streamAspectRatio, setStreamAspectRatio] = useState(16 / 9)
  const [starting, setStarting] = useState(false)
  const [streaming, setStreaming] = useState(false)
  const [streamResult, setStreamResult] = useState('')
  const [sessionStatus, setSessionStatus] = useState<DeepfakeSessionStatus | null>(null)
  const sourcePreview = useFilePreview(sourceFile)
  const targetPreview = useFilePreview(targetFile)
  const videoRef = useRef<HTMLVideoElement | null>(null)
  const canvasRef = useRef<HTMLCanvasElement | null>(null)
  const outputViewRef = useRef<HTMLDivElement | null>(null)
  const socketRef = useRef<WebSocket | null>(null)
  const mediaRef = useRef<MediaStream | null>(null)
  const sessionIdRef = useRef('')
  const outputUrlRef = useRef('')
  const imageUrlRef = useRef('')
  const captureTimerRef = useRef<number | null>(null)

  const loadStatus = useCallback(async () => {
    setStatusLoading(true)
    try {
      setStatus(await getDeepfakeStatus())
    } catch (error) {
      message.error(error instanceof Error ? error.message : 'GPU 状态读取失败')
    } finally {
      setStatusLoading(false)
    }
  }, [])

  useEffect(() => {
    void loadStatus()
  }, [loadStatus])

  const setOutputBlob = useCallback((blob: Blob) => {
    if (outputUrlRef.current) URL.revokeObjectURL(outputUrlRef.current)
    const next = URL.createObjectURL(blob)
    outputUrlRef.current = next
    setStreamResult(next)
  }, [])

  const captureFrame = useCallback(() => {
    const socket = socketRef.current
    const video = videoRef.current
    const canvas = canvasRef.current
    if (!socket || socket.readyState !== WebSocket.OPEN || !video || !canvas || video.videoWidth === 0) return
    const width = Math.min(realtimeWidth, video.videoWidth)
    const height = Math.max(64, Math.round(video.videoHeight * (width / video.videoWidth)))
    canvas.width = width
    canvas.height = height
    const context = canvas.getContext('2d', { alpha: false })
    if (!context) return
    context.drawImage(video, 0, 0, width, height)
    canvas.toBlob(async (blob) => {
      if (!blob || socket.readyState !== WebSocket.OPEN) return
      socket.send(await blob.arrayBuffer())
    }, 'image/jpeg', 0.84)
  }, [realtimeWidth])

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
    if (!sourceFile || !targetFile || !authorized) {
      message.warning('请选择两张图片并确认素材授权')
      return
    }
    setImageLoading(true)
    try {
      const result = await swapDeepfakeImage(sourceFile, targetFile)
      if (imageUrlRef.current) URL.revokeObjectURL(imageUrlRef.current)
      const next = URL.createObjectURL(result.blob)
      imageUrlRef.current = next
      setImageResult(next)
      setImageInferenceMs(result.inferenceMs)
      message.success('换脸完成')
    } catch (error) {
      message.error(error instanceof Error ? error.message : '换脸失败')
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
      message.error('无法进入全屏模式')
    }
  }

  const startRealtime = async () => {
    if (!sourceFile || !authorized) {
      message.warning('请选择身份图片并确认素材授权')
      return
    }
    setStarting(true)
    try {
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
      const session = await createDeepfakeSession(sourceFile, realtimeWidth)
      sessionIdRef.current = session.session_id
      const socket = openDeepfakeSocket(session.stream_path)
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
            message.warning(payload.message || '当前帧未处理')
            captureTimerRef.current = window.setTimeout(captureFrame, 250)
          }
        } catch {
          message.error('实时流返回了无效数据')
        }
      }
      socket.onerror = () => message.error('实时流连接失败')
      socket.onclose = () => setStreaming(false)
    } catch (error) {
      await stopRealtime()
      message.error(error instanceof Error ? error.message : '摄像头启动失败')
    } finally {
      setStarting(false)
    }
  }

  return (
    <div className="deepfake-studio">
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
            <ImagePicker label="身份图片" file={sourceFile} preview={sourcePreview} onChange={setSourceFile} />
            <ImagePicker label="目标图片" file={targetFile} preview={targetPreview} onChange={setTargetFile} />
          </div>
          <div className="deepfake-actions">
            <Checkbox checked={authorized} onChange={(event) => setAuthorized(event.target.checked)}>
              我确认已获得人脸素材授权
            </Checkbox>
            <Button type="primary" icon={<SwapOutlined />} loading={imageLoading} onClick={runImageSwap}>
              开始换脸
            </Button>
          </div>
          <div className="deepfake-result">
            {imageLoading ? <Spin /> : imageResult ? <Image src={imageResult} alt="换脸结果" /> : <PictureOutlined />}
          </div>
          {imageResult && <Text type="secondary">GPU 推理 {imageInferenceMs.toFixed(0)} ms</Text>}
        </div>
      ) : (
        <div className="deepfake-workspace">
          <div className="deepfake-realtime-controls">
            <ImagePicker label="身份图片" file={sourceFile} preview={sourcePreview} onChange={setSourceFile} />
            <div className="deepfake-session-controls">
              <Text strong>传输宽度</Text>
              <Segmented
                value={realtimeWidth}
                disabled={streaming || starting}
                onChange={(value) => setRealtimeWidth(Number(value))}
                options={[
                  { value: 640, label: '640' },
                  { value: 960, label: '960' },
                  { value: 1280, label: '1280' },
                ]}
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
            <Tag>{sessionStatus?.measured_fps?.toFixed(1) || '0.0'} FPS</Tag>
            <Tag>{sessionStatus?.average_inference_ms?.toFixed(0) || '0'} ms</Tag>
            <Tag>{sessionStatus?.frame_count || 0} 帧</Tag>
          </Space>
        </div>
      )}
    </div>
  )
}
