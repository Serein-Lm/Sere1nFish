import { useCallback, useEffect, useState } from 'react'
import {
  Alert,
  Button,
  Drawer,
  Empty,
  List,
  Progress,
  Segmented,
  Space,
  Tag,
  Typography,
  Upload,
  message,
  type UploadProps,
} from 'antd'
import {
  AppstoreOutlined,
  AudioOutlined,
  CloudUploadOutlined,
  PaperClipOutlined,
  PictureOutlined,
  ReloadOutlined,
} from '@ant-design/icons'
import {
  listMobileTransfers,
  retryMobileTransfer,
  uploadMobileTransfer,
  type MobileTransfer,
  type MobileTransferCategory,
} from '../../services/mobileService'

const { Dragger } = Upload

interface Props {
  deviceId: string
  open: boolean
  onClose: () => void
}

const CATEGORY_OPTIONS = [
  { label: '自动识别', value: 'auto', icon: <AppstoreOutlined /> },
  { label: '图片', value: 'image', icon: <PictureOutlined /> },
  { label: '音频', value: 'audio', icon: <AudioOutlined /> },
  { label: '附件', value: 'attachment', icon: <PaperClipOutlined /> },
]

const STATUS_META: Record<MobileTransfer['status'], { text: string; color: string }> = {
  archiving: { text: '归档中', color: 'processing' },
  pushing: { text: '发送中', color: 'processing' },
  completed: { text: '已送达', color: 'success' },
  failed: { text: '失败', color: 'error' },
}

function formatBytes(size: number): string {
  if (size < 1024) return `${size} B`
  if (size < 1024 * 1024) return `${(size / 1024).toFixed(1)} KB`
  return `${(size / 1024 / 1024).toFixed(1)} MB`
}

export default function DeviceTransferDrawer({ deviceId, open, onClose }: Props) {
  const [category, setCategory] = useState<MobileTransferCategory>('auto')
  const [items, setItems] = useState<MobileTransfer[]>([])
  const [loading, setLoading] = useState(false)
  const [retrying, setRetrying] = useState<string>('')
  const [uploads, setUploads] = useState<Record<string, number>>({})

  const loadHistory = useCallback(async () => {
    setLoading(true)
    try {
      const result = await listMobileTransfers(deviceId)
      setItems(result.items)
    } catch (error) {
      message.error(error instanceof Error ? error.message : '传输记录加载失败')
    } finally {
      setLoading(false)
    }
  }, [deviceId])

  useEffect(() => {
    if (open) void loadHistory()
  }, [loadHistory, open])

  const uploadProps: UploadProps = {
    multiple: true,
    showUploadList: false,
    customRequest: async ({ file, onError, onProgress, onSuccess }) => {
      const source = file as File
      const uploadKey = `${source.name}-${source.size}-${Date.now()}`
      setUploads((current) => ({ ...current, [uploadKey]: 0 }))
      try {
        const result = await uploadMobileTransfer(deviceId, source, category, (percent) => {
          setUploads((current) => ({ ...current, [uploadKey]: Math.min(percent, 99) }))
          onProgress?.({ percent })
        })
        setUploads((current) => ({ ...current, [uploadKey]: 100 }))
        onSuccess?.(result)
        message.success(`${source.name} 已发送到手机`)
        await loadHistory()
      } catch (error) {
        const reason = error instanceof Error ? error : new Error('文件发送失败')
        onError?.(reason)
        message.error(reason.message)
        await loadHistory()
      } finally {
        window.setTimeout(() => {
          setUploads((current) => {
            const next = { ...current }
            delete next[uploadKey]
            return next
          })
        }, 800)
      }
    },
  }

  const handleRetry = async (item: MobileTransfer) => {
    setRetrying(item.transfer_id)
    try {
      await retryMobileTransfer(deviceId, item.transfer_id)
      message.success(`${item.filename} 已重新发送`)
      await loadHistory()
    } catch (error) {
      message.error(error instanceof Error ? error.message : '重试失败')
      await loadHistory()
    } finally {
      setRetrying('')
    }
  }

  return (
    <Drawer
      title="传文件到手机"
      open={open}
      onClose={onClose}
      width="min(560px, 100vw)"
      className="device-transfer-drawer"
      extra={(
        <Button
          type="text"
          icon={<ReloadOutlined />}
          loading={loading}
          onClick={() => void loadHistory()}
          aria-label="刷新传输记录"
        />
      )}
    >
      <Space direction="vertical" size={16} className="device-transfer-content">
        <Segmented
          block
          value={category}
          options={CATEGORY_OPTIONS}
          onChange={(value) => setCategory(value as MobileTransferCategory)}
        />
        <Dragger {...uploadProps} className="device-transfer-dropzone">
          <p className="ant-upload-drag-icon"><CloudUploadOutlined /></p>
          <p className="ant-upload-text">点击或拖入图片、音频和附件</p>
          <p className="ant-upload-hint">支持多文件；上传后自动归档并发送到当前手机</p>
        </Dragger>
        {Object.entries(uploads).map(([key, percent]) => (
          <div className="device-transfer-progress" key={key}>
            <Typography.Text ellipsis>{key.split('-').slice(0, -2).join('-')}</Typography.Text>
            <Progress percent={percent} size="small" status={percent === 100 ? 'success' : 'active'} />
          </div>
        ))}
        <Alert
          type="info"
          showIcon
          message="图片进入相册，音频进入音乐目录，其他附件进入下载目录，可直接从微信等应用的文件选择器中选用。"
        />

        <div className="device-transfer-history-title">
          <Typography.Title level={5}>最近传输</Typography.Title>
          <Typography.Text type="secondary">{items.length} 条</Typography.Text>
        </div>
        <List
          loading={loading}
          dataSource={items}
          locale={{ emptyText: <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="暂无传输记录" /> }}
          renderItem={(item) => {
            const status = STATUS_META[item.status]
            return (
              <List.Item
                className="device-transfer-item"
                actions={item.status === 'failed' ? [
                  <Button
                    key="retry"
                    type="link"
                    icon={<ReloadOutlined />}
                    loading={retrying === item.transfer_id}
                    onClick={() => void handleRetry(item)}
                  >
                    重试
                  </Button>,
                ] : undefined}
              >
                <List.Item.Meta
                  avatar={item.category === 'image' ? <PictureOutlined /> : item.category === 'audio' ? <AudioOutlined /> : <PaperClipOutlined />}
                  title={(
                    <Space size={8} className="device-transfer-item-title">
                      <Typography.Text ellipsis title={item.filename}>{item.filename}</Typography.Text>
                      <Tag color={status.color}>{status.text}</Tag>
                    </Space>
                  )}
                  description={(
                    <Space direction="vertical" size={2}>
                      <Typography.Text type="secondary">
                        {formatBytes(item.size)} · {new Date(item.created_at).toLocaleString('zh-CN')}
                      </Typography.Text>
                      {item.remote_path && <Typography.Text copyable ellipsis>{item.remote_path}</Typography.Text>}
                      {item.last_error && <Typography.Text type="danger">{item.last_error}</Typography.Text>}
                    </Space>
                  )}
                />
              </List.Item>
            )
          }}
        />
      </Space>
    </Drawer>
  )
}
