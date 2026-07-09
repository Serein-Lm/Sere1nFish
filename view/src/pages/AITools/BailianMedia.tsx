import { useEffect, useMemo, useState } from 'react'
import {
  Alert,
  Button,
  Card,
  Col,
  Form,
  Image,
  Input,
  Row,
  Select,
  Space,
  Tag,
  Typography,
  message,
} from 'antd'
import { PictureOutlined, ReloadOutlined, VideoCameraOutlined } from '@ant-design/icons'
import {
  getBailianConfig,
  imageToVideo,
  queryBailianTask,
  qwenImageEdit,
  textToVideo,
  wanxImageEdit,
  type BailianConfigStatus,
  type BailianTaskResp,
} from '../../services/aigcService'

const { Text, Paragraph } = Typography

type Mode = 'image' | 'video'

function lines(value?: string): string[] {
  return (value || '')
    .split('\n')
    .map((item) => item.trim())
    .filter(Boolean)
}

function parseJson(value: string | undefined, label: string): unknown | undefined {
  const text = (value || '').trim()
  if (!text) return undefined
  try {
    return JSON.parse(text)
  } catch {
    throw new Error(`${label} 不是有效 JSON`)
  }
}

function parseJsonObject(value: string | undefined, label: string): Record<string, unknown> | undefined {
  const parsed = parseJson(value, label)
  if (parsed === undefined) return undefined
  if (parsed && typeof parsed === 'object' && !Array.isArray(parsed)) {
    return parsed as Record<string, unknown>
  }
  throw new Error(`${label} 必须是 JSON 对象`)
}

function parseMediaItems(value: string | undefined): Array<{ type: string; url: string }> | undefined {
  const parsed = parseJson(value, 'media JSON')
  if (parsed === undefined) return undefined
  if (!Array.isArray(parsed)) {
    throw new Error('media JSON 必须是数组')
  }
  return parsed.map((item, index) => {
    if (!item || typeof item !== 'object' || Array.isArray(item)) {
      throw new Error(`media JSON 第 ${index + 1} 项必须是对象`)
    }
    const media = item as Record<string, unknown>
    if (typeof media.type !== 'string' || typeof media.url !== 'string') {
      throw new Error(`media JSON 第 ${index + 1} 项需要 type 和 url`)
    }
    return { type: media.type, url: media.url }
  })
}

function ResultPanel({ result }: { result: BailianTaskResp | null }) {
  if (!result) return null
  const images = result.images?.length ? result.images : result.result_urls || []
  return (
    <div className="bailian-result">
      <Space size={6} wrap>
        {result.model && <Tag color="blue">{result.model}</Tag>}
        {result.mode && <Tag>{result.mode}</Tag>}
        {result.task_protocol && <Tag color="purple">{result.task_protocol}</Tag>}
        {result.payload_protocol && <Tag color="geekblue">{result.payload_protocol}</Tag>}
        {result.task_status && <Tag color="processing">{result.task_status}</Tag>}
      </Space>
      {result.task_id && (
        <Text copyable={{ text: result.task_id }} className="bailian-task-id">
          {result.task_id}
        </Text>
      )}
      {result.video_url && (
        <video className="bailian-video" src={result.video_url} controls />
      )}
      {images.length > 0 && (
        <Image.PreviewGroup>
          <div className="bailian-image-grid">
            {images.map((url) => (
              <Image key={url} src={url} alt="result" />
            ))}
          </div>
        </Image.PreviewGroup>
      )}
      <details className="bailian-raw">
        <summary>响应 JSON</summary>
        <pre>{JSON.stringify(result.response || result, null, 2)}</pre>
      </details>
    </div>
  )
}

export default function BailianMedia({ mode }: { mode: Mode }) {
  const [config, setConfig] = useState<BailianConfigStatus | null>(null)
  const [loadingConfig, setLoadingConfig] = useState(false)
  const [submitting, setSubmitting] = useState<string | null>(null)
  const [result, setResult] = useState<BailianTaskResp | null>(null)
  const [taskResult, setTaskResult] = useState<BailianTaskResp | null>(null)
  const [qwenForm] = Form.useForm()
  const [wanxForm] = Form.useForm()
  const [t2vForm] = Form.useForm()
  const [i2vForm] = Form.useForm()
  const [taskForm] = Form.useForm()

  const isImage = mode === 'image'
  const title = useMemo(
    () => isImage ? '百炼图像工具' : '百炼视频工具',
    [isImage],
  )

  const loadConfig = async () => {
    setLoadingConfig(true)
    try {
      setConfig(await getBailianConfig())
    } catch (e) {
      message.error(e instanceof Error ? e.message : '读取百炼配置失败')
    } finally {
      setLoadingConfig(false)
    }
  }

  useEffect(() => {
    loadConfig()
  }, [])

  const runAction = async (key: string, action: () => Promise<BailianTaskResp>) => {
    setSubmitting(key)
    try {
      const data = await action()
      setResult(data)
      if (data.task_id) taskForm.setFieldsValue({ task_id: data.task_id, protocol: data.task_protocol || 'auto' })
      message.success(data.task_id ? '任务已创建' : '调用完成')
    } catch (e) {
      message.error(e instanceof Error ? e.message : '调用失败')
    } finally {
      setSubmitting(null)
    }
  }

  return (
    <div className="bailian-media">
      <div className="bailian-toolbar">
        <Space orientation="vertical" size={2}>
          <Text strong>{title}</Text>
          <Text type="secondary">
            {isImage ? '支持 Qwen 指令编辑和万相异步图像编辑' : '支持 Wan2.7 文生视频、图生视频和任务轮询'}
          </Text>
        </Space>
        <Button icon={<ReloadOutlined />} loading={loadingConfig} onClick={loadConfig}>
          配置状态
        </Button>
      </div>

      {config && (
        <Alert
          className="bailian-status"
          type={config.has_api_key ? 'success' : 'warning'}
          showIcon
          title={
            <Space size={6} wrap>
              <Tag color="blue">{config.region}</Tag>
              <Tag color={config.has_api_key ? 'green' : 'red'}>{config.has_api_key ? 'API Key 已配置' : '缺少 API Key'}</Tag>
              <Tag color={config.has_workspace_id ? 'green' : 'orange'}>{config.has_workspace_id ? 'Workspace 已配置' : 'Workspace 未配置'}</Tag>
              <Tag>{isImage ? config.qwen_image_edit_model : config.text_to_video_model}</Tag>
              <Tag>{isImage ? config.wanx_image_edit_model : config.image_to_video_model}</Tag>
            </Space>
          }
        />
      )}

      {isImage ? (
        <Row gutter={[16, 16]}>
          <Col xs={24} lg={12}>
            <Card size="small" title={<Space><PictureOutlined /> Qwen 图像编辑</Space>}>
              <Form form={qwenForm} layout="vertical" onFinish={(values) => {
                runAction('qwen', () => qwenImageEdit({
                  images: lines(values.images),
                  prompt: values.prompt,
                  model: values.model || undefined,
                  parameters: parseJsonObject(values.parameters, '参数 JSON'),
                }))
              }}>
                <Form.Item name="images" label="输入图片 URL，每行一个" rules={[{ required: true }]}>
                  <Input.TextArea rows={3} />
                </Form.Item>
                <Form.Item name="prompt" label="编辑指令" rules={[{ required: true }]}>
                  <Input.TextArea rows={3} />
                </Form.Item>
                <Form.Item name="model" label="模型">
                  <Input placeholder={config?.qwen_image_edit_model} />
                </Form.Item>
                <Form.Item name="parameters" label="参数 JSON">
                  <Input.TextArea rows={3} placeholder='{"n":1,"watermark":false,"prompt_extend":true}' />
                </Form.Item>
                <Button type="primary" htmlType="submit" loading={submitting === 'qwen'}>
                  提交编辑
                </Button>
              </Form>
            </Card>
          </Col>
          <Col xs={24} lg={12}>
            <Card size="small" title={<Space><PictureOutlined /> 万相图像编辑</Space>}>
              <Form form={wanxForm} layout="vertical" initialValues={{ function: 'description_edit' }} onFinish={(values) => {
                runAction('wanx', () => wanxImageEdit({
                  base_image_url: values.base_image_url,
                  prompt: values.prompt,
                  function: values.function,
                  mask_image_url: values.mask_image_url || undefined,
                  model: values.model || undefined,
                  parameters: parseJsonObject(values.parameters, '参数 JSON'),
                  extra_input: parseJsonObject(values.extra_input, '额外 input JSON'),
                }))
              }}>
                <Form.Item name="base_image_url" label="基础图片 URL" rules={[{ required: true }]}>
                  <Input />
                </Form.Item>
                <Form.Item name="function" label="编辑功能" htmlFor="wanx-image-edit-function">
                  <Select id="wanx-image-edit-function" options={[
                    { value: 'description_edit', label: '指令编辑' },
                    { value: 'remove_watermark', label: '去文字水印' },
                    { value: 'expand', label: '扩图' },
                    { value: 'super_resolution', label: '超分' },
                  ]} />
                </Form.Item>
                <Form.Item name="prompt" label="编辑指令" rules={[{ required: true }]}>
                  <Input.TextArea rows={3} />
                </Form.Item>
                <Form.Item name="mask_image_url" label="Mask URL">
                  <Input />
                </Form.Item>
                <Form.Item name="model" label="模型">
                  <Input placeholder={config?.wanx_image_edit_model} />
                </Form.Item>
                <Form.Item name="parameters" label="参数 JSON">
                  <Input.TextArea rows={3} placeholder='{"n":1}' />
                </Form.Item>
                <Form.Item name="extra_input" label="额外 input JSON">
                  <Input.TextArea rows={3} placeholder='{"mask_image_url":"https://..."}' />
                </Form.Item>
                <Button type="primary" htmlType="submit" loading={submitting === 'wanx'}>
                  创建异步任务
                </Button>
              </Form>
            </Card>
          </Col>
        </Row>
      ) : (
        <Row gutter={[16, 16]}>
          <Col xs={24} lg={12}>
            <Card size="small" title={<Space><VideoCameraOutlined /> 文生视频</Space>}>
              <Form form={t2vForm} layout="vertical" onFinish={(values) => {
                runAction('t2v', () => textToVideo({
                  prompt: values.prompt,
                  negative_prompt: values.negative_prompt || undefined,
                  audio_url: values.audio_url || undefined,
                  model: values.model || undefined,
                  parameters: parseJsonObject(values.parameters, '参数 JSON'),
                }))
              }}>
                <Form.Item name="prompt" label="视频提示词" rules={[{ required: true }]}>
                  <Input.TextArea rows={4} />
                </Form.Item>
                <Form.Item name="negative_prompt" label="反向提示词">
                  <Input />
                </Form.Item>
                <Form.Item name="audio_url" label="音频 URL">
                  <Input />
                </Form.Item>
                <Form.Item name="model" label="模型">
                  <Input placeholder={config?.text_to_video_model} />
                </Form.Item>
                <Form.Item name="parameters" label="参数 JSON">
                  <Input.TextArea rows={3} placeholder='{"resolution":"720P","ratio":"16:9","duration":5}' />
                </Form.Item>
                <Button type="primary" htmlType="submit" loading={submitting === 't2v'}>
                  创建视频任务
                </Button>
              </Form>
            </Card>
          </Col>
          <Col xs={24} lg={12}>
            <Card size="small" title={<Space><VideoCameraOutlined /> 图生视频</Space>}>
              <Form form={i2vForm} layout="vertical" initialValues={{ protocol: 'auto' }} onFinish={(values) => {
                runAction('i2v', () => imageToVideo({
                  img_url: values.img_url || undefined,
                  last_frame_url: values.last_frame_url || undefined,
                  first_clip_url: values.first_clip_url || undefined,
                  prompt: values.prompt || undefined,
                  negative_prompt: values.negative_prompt || undefined,
                  audio_url: values.audio_url || undefined,
                  template: values.template || undefined,
                  media: parseMediaItems(values.media),
                  protocol: values.protocol,
                  model: values.model || undefined,
                  parameters: parseJsonObject(values.parameters, '参数 JSON'),
                }))
              }}>
                <Form.Item name="img_url" label="首帧图片 URL">
                  <Input />
                </Form.Item>
                <Form.Item name="last_frame_url" label="尾帧图片 URL">
                  <Input />
                </Form.Item>
                <Form.Item name="first_clip_url" label="续写视频 URL">
                  <Input />
                </Form.Item>
                <Form.Item name="media" label="media JSON">
                  <Input.TextArea rows={3} placeholder='[{"type":"first_frame","url":"https://..."}]' />
                </Form.Item>
                <Form.Item name="prompt" label="视频提示词">
                  <Input.TextArea rows={3} />
                </Form.Item>
                <Form.Item name="negative_prompt" label="反向提示词">
                  <Input />
                </Form.Item>
                <Form.Item name="audio_url" label="音频 URL">
                  <Input />
                </Form.Item>
                <Form.Item name="template" label="模板">
                  <Input />
                </Form.Item>
                <Form.Item name="model" label="模型">
                  <Input placeholder={config?.image_to_video_model} />
                </Form.Item>
                <Form.Item name="parameters" label="参数 JSON">
                  <Input.TextArea rows={3} placeholder='{"resolution":"720P","duration":5}' />
                </Form.Item>
                <Form.Item name="protocol" label="任务协议" htmlFor="image-to-video-protocol">
                  <Select id="image-to-video-protocol" options={[
                    { value: 'auto', label: '自动' },
                    { value: 'workspace', label: 'Workspace' },
                    { value: 'legacy', label: 'Legacy' },
                  ]} />
                </Form.Item>
                <Button type="primary" htmlType="submit" loading={submitting === 'i2v'}>
                  创建图生视频任务
                </Button>
              </Form>
            </Card>
          </Col>
        </Row>
      )}

      <Card size="small" title="任务查询" className="bailian-query">
        <Form form={taskForm} layout="inline" initialValues={{ protocol: 'auto' }} onFinish={(values) => {
          setSubmitting('task')
          queryBailianTask(values.task_id, values.protocol)
            .then(setTaskResult)
            .catch((e) => message.error(e instanceof Error ? e.message : '查询失败'))
            .finally(() => setSubmitting(null))
        }}>
          <Form.Item name="task_id" rules={[{ required: true }]}>
            <Input aria-label="任务 ID" placeholder="task_id" className="bailian-task-input" />
          </Form.Item>
          <Form.Item name="protocol">
            <Select id="bailian-task-protocol" aria-label="任务查询协议" className="bailian-protocol-select" options={[
              { value: 'auto', label: '自动' },
              { value: 'workspace', label: 'Workspace' },
              { value: 'legacy', label: 'Legacy' },
            ]} />
          </Form.Item>
          <Button htmlType="submit" loading={submitting === 'task'}>
            查询
          </Button>
        </Form>
      </Card>

      <ResultPanel result={result} />
      <ResultPanel result={taskResult} />
      <Paragraph type="secondary" className="bailian-note">
        视频任务通常需要数分钟完成，任务结果链接有效期有限，拿到结果后应及时保存到长期存储。
      </Paragraph>
    </div>
  )
}
