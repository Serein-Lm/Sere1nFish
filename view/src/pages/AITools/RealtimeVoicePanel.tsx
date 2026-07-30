import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import {
  Button,
  Collapse,
  Empty,
  Input,
  InputNumber,
  Modal,
  Segmented,
  Select,
  Space,
  Spin,
  Switch,
  Tag,
  Tooltip,
  Typography,
  message,
} from 'antd'
import {
  AudioOutlined,
  ClearOutlined,
  DisconnectOutlined,
  LaptopOutlined,
  LoadingOutlined,
  QuestionCircleOutlined,
  ReloadOutlined,
  SoundOutlined,
  StopOutlined,
} from '@ant-design/icons'
import {
  RealtimeVoiceClient,
  getRealtimeVoiceConfig,
  listLocalAudioDevices,
  type LocalAudioDevice,
  type RealtimeServerEvent,
  type RealtimeSessionState,
  type RealtimeTurnMode,
  type RealtimeVoiceConfig,
} from '../../services/voiceRealtimeService'

const { Text, Title } = Typography
const { TextArea } = Input

interface TranscriptMessage {
  id: string
  role: 'user' | 'assistant'
  text: string
}

interface RealtimeVoicePanelProps {
  preferredVoice?: string | null
  outputSessionId?: string
}

const MODE_OPTIONS = [
  { label: '智能轮次', value: 'smart_turn' },
  { label: '快速检测', value: 'server_vad' },
  { label: '按住说话', value: 'manual' },
] satisfies Array<{ label: string; value: RealtimeTurnMode }>

const STATE_META: Record<RealtimeSessionState, { label: string; color: string }> = {
  idle: { label: '未连接', color: 'default' },
  connecting: { label: '连接中', color: 'processing' },
  listening: { label: '正在聆听', color: 'success' },
  speaking: { label: '正在回应', color: 'blue' },
  error: { label: '连接异常', color: 'error' },
}

export default function RealtimeVoicePanel({
  preferredVoice,
  outputSessionId,
}: RealtimeVoicePanelProps) {
  const [messageApi, contextHolder] = message.useMessage()
  const [config, setConfig] = useState<RealtimeVoiceConfig | null>(null)
  const [loading, setLoading] = useState(true)
  const [starting, setStarting] = useState(false)
  const [state, setState] = useState<RealtimeSessionState>('idle')
  const [voice, setVoice] = useState('')
  const [mode, setMode] = useState<RealtimeTurnMode>('smart_turn')
  const [instructions, setInstructions] = useState('')
  const [maxHistoryTurns, setMaxHistoryTurns] = useState(20)
  const [devices, setDevices] = useState<LocalAudioDevice[]>([])
  const [inputDeviceId, setInputDeviceId] = useState<string>()
  const [outputDeviceId, setOutputDeviceId] = useState<string>()
  const [playLocally, setPlayLocally] = useState(!outputSessionId)
  const [messages, setMessages] = useState<TranscriptMessage[]>([])
  const [assistantDraft, setAssistantDraft] = useState('')
  const [pushToTalk, setPushToTalk] = useState(false)
  const [guideOpen, setGuideOpen] = useState(false)
  const clientRef = useRef<RealtimeVoiceClient | null>(null)
  const assistantDraftRef = useRef('')
  const messageSequenceRef = useRef(0)

  const connected = state !== 'idle' && state !== 'error'

  const appendMessage = useCallback((role: TranscriptMessage['role'], text: string) => {
    const content = text.trim()
    if (!content) return
    messageSequenceRef.current += 1
    setMessages(current => [
      ...current,
      { id: `${role}-${messageSequenceRef.current}`, role, text: content },
    ].slice(-80))
  }, [])

  const commitAssistant = useCallback((text?: string) => {
    const content = (text || assistantDraftRef.current).trim()
    if (content) appendMessage('assistant', content)
    assistantDraftRef.current = ''
    setAssistantDraft('')
  }, [appendMessage])

  const handleServerEvent = useCallback((event: RealtimeServerEvent) => {
    if (event.type === 'conversation.item.input_audio_transcription.completed') {
      appendMessage('user', event.transcript || '')
      return
    }
    if (event.type === 'response.audio_transcript.delta') {
      const next = assistantDraftRef.current + (event.delta || '')
      assistantDraftRef.current = next
      setAssistantDraft(next)
      return
    }
    if (event.type === 'response.audio_transcript.done') {
      commitAssistant(event.transcript)
      return
    }
    if (event.type === 'response.done' && assistantDraftRef.current) {
      commitAssistant()
    }
  }, [appendMessage, commitAssistant])

  const loadConfig = useCallback(async () => {
    setLoading(true)
    try {
      const next = await getRealtimeVoiceConfig()
      setConfig(next)
      const allVoices = [...next.system_voices, ...next.cloned_voices]
      const requested = preferredVoice && allVoices.some(item => item.voice_id === preferredVoice)
        ? preferredVoice
        : next.default_voice
      setVoice(requested)
      setMode(next.default_mode)
      setInstructions(next.default_instructions || '')
      setMaxHistoryTurns(next.max_history_turns)
    } catch (error) {
      messageApi.error(error instanceof Error ? error.message : '全双工语音配置加载失败')
    } finally {
      setLoading(false)
    }
  }, [messageApi, preferredVoice])

  const loadDevices = useCallback(async () => {
    try {
      setDevices(await listLocalAudioDevices())
    } catch {
      setDevices([])
    }
  }, [])

  useEffect(() => {
    void loadConfig()
    void loadDevices()
  }, [loadConfig, loadDevices])

  useEffect(() => () => {
    const client = clientRef.current
    clientRef.current = null
    if (client) void client.stop()
  }, [])

  const stopSession = useCallback(async () => {
    const client = clientRef.current
    clientRef.current = null
    setPushToTalk(false)
    if (assistantDraftRef.current) commitAssistant()
    if (client) await client.stop()
    setState('idle')
  }, [commitAssistant])

  const startSession = async () => {
    if (!config || !voice) return
    setStarting(true)
    const client = new RealtimeVoiceClient({
      onState: setState,
      onEvent: handleServerEvent,
      onError: error => {
        messageApi.error(error.message)
        if (clientRef.current === client) {
          clientRef.current = null
          void client.stop()
        }
      },
    })
    clientRef.current = client
    try {
      await client.start({
        model: config.model,
        voice,
        mode,
        instructions,
        maxHistoryTurns,
        inputDeviceId,
        outputDeviceId,
        outputSessionId,
        playLocally,
      })
      await loadDevices()
    } catch (error) {
      if (clientRef.current === client) clientRef.current = null
      messageApi.error(error instanceof Error ? error.message : '全双工语音启动失败')
      setState('idle')
    } finally {
      setStarting(false)
    }
  }

  const setManualCapture = (active: boolean) => {
    if (mode !== 'manual' || !connected) return
    setPushToTalk(active)
    clientRef.current?.setPushToTalk(active)
  }

  const voiceOptions = useMemo(() => {
    if (!config) return []
    return [
      {
        label: '系统音色',
        options: config.system_voices.map(item => ({
          value: item.voice_id,
          label: item.label,
        })),
      },
      ...(config.cloned_voices.length ? [{
        label: '复刻音色',
        options: config.cloned_voices.map(item => ({
          value: item.voice_id,
          label: `${item.label} (${item.voice_id.slice(-8)})`,
        })),
      }] : []),
    ]
  }, [config])

  const inputDevices = devices.filter(device => device.kind === 'audioinput')
  const outputDevices = devices.filter(device => device.kind === 'audiooutput')
  const stateMeta = STATE_META[state]

  if (loading) {
    return <div className="vc-realtime-loading"><Spin /></div>
  }

  return (
    <div className="vc-realtime-panel">
      {contextHolder}
      <div className="vc-realtime-toolbar">
        <Space wrap>
          <Title level={5}><AudioOutlined /> 全双工语音</Title>
          {config?.model && <Tag color="blue">{config.model}</Tag>}
          {outputSessionId && <Tag color="cyan">OBS 音频已绑定</Tag>}
          <Tag color={stateMeta.color}>{stateMeta.label}</Tag>
        </Space>
        <Space>
          <Tooltip title="本机接入说明">
            <Button icon={<QuestionCircleOutlined />} onClick={() => setGuideOpen(true)} />
          </Tooltip>
          <Tooltip title="刷新模型与音色">
            <Button icon={<ReloadOutlined />} onClick={() => void loadConfig()} />
          </Tooltip>
        </Space>
      </div>

      <div className="vc-realtime-layout">
        <section className="vc-conversation-view">
          <div className="vc-conversation-head">
            <Text strong>实时转写</Text>
            <Tooltip title="仅清空当前页面显示，不重置模型会话">
              <Button
                type="text"
                size="small"
                icon={<ClearOutlined />}
                disabled={!messages.length && !assistantDraft}
                onClick={() => {
                  setMessages([])
                  assistantDraftRef.current = ''
                  setAssistantDraft('')
                }}
              />
            </Tooltip>
          </div>
          <div className="vc-transcript-list" aria-live="polite">
            {!messages.length && !assistantDraft ? (
              <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="会话尚未开始" />
            ) : (
              <>
                {messages.map(item => (
                  <div key={item.id} className={`vc-transcript vc-transcript-${item.role}`}>
                    <span>{item.role === 'user' ? '我' : 'AI'}</span>
                    <p>{item.text}</p>
                  </div>
                ))}
                {assistantDraft && (
                  <div className="vc-transcript vc-transcript-assistant is-streaming">
                    <span>AI</span>
                    <p>{assistantDraft}<LoadingOutlined /></p>
                  </div>
                )}
              </>
            )}
          </div>
        </section>

        <aside className="vc-realtime-controls">
          <label>
            <Text strong>输出音色</Text>
            <Select
              value={voice || undefined}
              options={voiceOptions}
              onChange={setVoice}
              disabled={connected || starting}
              showSearch
              optionFilterProp="label"
              placeholder="选择音色"
            />
          </label>
          <label>
            <Text strong>轮次模式</Text>
            <Segmented<RealtimeTurnMode>
              block
              value={mode}
              options={MODE_OPTIONS}
              onChange={setMode}
              disabled={connected || starting}
            />
          </label>

          {mode === 'manual' && connected ? (
            <Button
              type="primary"
              danger={pushToTalk}
              icon={<AudioOutlined />}
              className={`vc-push-to-talk ${pushToTalk ? 'is-active' : ''}`}
              onPointerDown={() => setManualCapture(true)}
              onPointerUp={() => setManualCapture(false)}
              onPointerCancel={() => setManualCapture(false)}
              onPointerLeave={() => pushToTalk && setManualCapture(false)}
            >
              {pushToTalk ? '松开发送' : '按住说话'}
            </Button>
          ) : connected ? (
            <div className={`vc-live-indicator vc-live-${state}`}>
              {state === 'speaking' ? <SoundOutlined /> : <AudioOutlined />}
              <span>{state === 'speaking' ? 'AI 正在回应' : '麦克风已开启'}</span>
            </div>
          ) : null}

          <Space className="vc-session-actions" wrap>
            {connected ? (
              <Button danger icon={<DisconnectOutlined />} onClick={() => void stopSession()}>
                结束会话
              </Button>
            ) : (
              <Button
                type="primary"
                icon={<AudioOutlined />}
                loading={starting}
                disabled={!config || !voice}
                onClick={() => void startSession()}
              >
                开始对话
              </Button>
            )}
            {state === 'speaking' && (
              <Button icon={<StopOutlined />} onClick={() => clientRef.current?.cancelResponse()}>
                停止回应
              </Button>
            )}
          </Space>

          <Collapse
            ghost
            size="small"
            items={[{
              key: 'settings',
              label: '会话与本机设备',
              children: (
                <div className="vc-realtime-settings">
                  <label>
                    <Text type="secondary">会话指令</Text>
                    <TextArea
                      value={instructions}
                      onChange={event => setInstructions(event.target.value)}
                      maxLength={4000}
                      autoSize={{ minRows: 2, maxRows: 5 }}
                      disabled={connected || starting}
                    />
                  </label>
                  <label>
                    <Text type="secondary">历史轮次</Text>
                    <InputNumber
                      value={maxHistoryTurns}
                      min={1}
                      max={50}
                      onChange={value => setMaxHistoryTurns(value || 20)}
                      disabled={connected || starting}
                    />
                  </label>
                  <label>
                    <Text type="secondary">麦克风</Text>
                    <Select
                      allowClear
                      value={inputDeviceId}
                      onChange={setInputDeviceId}
                      options={inputDevices.map(device => ({
                        value: device.deviceId,
                        label: device.label,
                      }))}
                      placeholder="系统默认"
                      disabled={connected || starting}
                    />
                  </label>
                  <label>
                    <Text type="secondary">扬声器</Text>
                    <Select
                      allowClear
                      value={outputDeviceId}
                      onChange={setOutputDeviceId}
                      options={outputDevices.map(device => ({
                        value: device.deviceId,
                        label: device.label,
                      }))}
                      placeholder="系统默认"
                      disabled={connected || starting}
                    />
                  </label>
                  <label className="vc-inline-setting">
                    <Text type="secondary">本地监听</Text>
                    <Switch
                      checked={playLocally}
                      onChange={setPlayLocally}
                      disabled={connected || starting}
                    />
                  </label>
                </div>
              ),
            }]}
          />
        </aside>
      </div>

      <Modal
        title={<Space><LaptopOutlined />本机语音接入</Space>}
        open={guideOpen}
        onCancel={() => setGuideOpen(false)}
        footer={<Button type="primary" onClick={() => setGuideOpen(false)}>知道了</Button>}
        width={560}
      >
        <ol className="vc-guide-list">
          <li>使用 Chrome 打开当前 HTTPS 页面，在系统提示中允许麦克风权限。</li>
          <li>在“会话与本机设备”中选择 Mac/PC 的麦克风和扬声器；不选择时使用系统默认设备。</li>
          <li>“智能轮次”适合自然对话，“按住说话”适合扬声器外放或环境噪声较大的场景。</li>
          <li>需要允许语音打断时建议佩戴耳机，避免扬声器声音被麦克风再次采集。</li>
        </ol>
      </Modal>
    </div>
  )
}
