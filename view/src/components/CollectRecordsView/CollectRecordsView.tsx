import { useEffect, useState } from 'react'
import Table from 'antd/es/table'
import Tag from 'antd/es/tag'
import Space from 'antd/es/space'
import Button from 'antd/es/button'
import Tooltip from 'antd/es/tooltip'
import Modal from 'antd/es/modal'
import Image from 'antd/es/image'
import Descriptions from 'antd/es/descriptions'
import Empty from 'antd/es/empty'
import Spin from 'antd/es/spin'
import Typography from 'antd/es/typography'
import Alert from 'antd/es/alert'
import Divider from 'antd/es/divider'
import type { ColumnsType } from 'antd/es/table'
import { CodeOutlined, DatabaseOutlined, EyeOutlined, FileTextOutlined, PictureOutlined } from '@ant-design/icons'

import { fetchScreenshotObjectUrl, type CollectRecord } from '../../services/mobileCollectService'
import {
  getSourceDocument,
  openAuthenticatedArtifact,
  type SourceContact,
  type SourceDocumentDetail,
} from '../../services/sourceDocumentService'
import { renderFindingValue } from '../../utils/findingValueRenderer'
import './CollectRecordsView.css'

const { Text } = Typography

const MOBILE_RE = /(?<!\d)(1[3-9]\d{9})(?!\d)/g
const TEL_KW_RE = /(?:联系电话|电话|联系方式|Tel|TEL|tel)\s*[:：]?\s*(\d{11}|(?:0\d{2,3}[-\s]?)?\d{7,8})(?!\d)/g
const EMAIL_RE = /([A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,})/g

export function scoreColor(n: number): string {
  if (n >= 80) return 'green'
  if (n >= 60) return 'blue'
  if (n >= 40) return 'orange'
  return 'default'
}

function fieldsToText(fields: Record<string, unknown>): string {
  const parts: string[] = []
  for (const v of Object.values(fields || {})) {
    if (Array.isArray(v)) parts.push(v.map((x) => String(x)).join(' '))
    else if (v != null && v !== '') parts.push(String(v))
  }
  return parts.join('\n')
}

export function extractContactsFromFields(fields: Record<string, unknown>): { channel: string; value: string }[] {
  const text = fieldsToText(fields)
  const out: { channel: string; value: string }[] = []
  const seen = new Set<string>()
  const add = (channel: string, value: string) => {
    const v = value.trim()
    if (!v) return
    const key = `${channel}:${v.toLowerCase()}`
    if (seen.has(key)) return
    seen.add(key)
    out.push({ channel, value: v })
  }
  for (const m of text.matchAll(EMAIL_RE)) add('email', m[1])
  for (const m of text.matchAll(MOBILE_RE)) add('phone', m[1])
  for (const m of text.matchAll(TEL_KW_RE)) add('phone', m[1].replace(/[\s-]/g, ''))
  return out
}

function classifyFieldKey(key: string): 'basic' | 'body' {
  const k = key.toLowerCase()
  const bodyHints = ['正文', '摘要', '内容', '背景', '简介', '详情', 'summary', 'content', 'background', 'desc', 'body']
  if (bodyHints.some((h) => k.includes(h))) return 'body'
  return 'basic'
}

export function CollectShotImage({
  url,
  width = 64,
  height,
  preview = true,
}: {
  url: string
  width?: number
  height?: number
  preview?: boolean
}) {
  const [src, setSrc] = useState<string>('')
  useEffect(() => {
    let objectUrl = ''
    let alive = true
    fetchScreenshotObjectUrl(url)
      .then((u) => {
        if (alive) {
          objectUrl = u
          setSrc(u)
        } else {
          URL.revokeObjectURL(u)
        }
      })
      .catch(() => undefined)
    return () => {
      alive = false
      if (objectUrl) URL.revokeObjectURL(objectUrl)
    }
  }, [url])
  if (!src) {
    return (
      <div className="collect-shot-loading" style={{ width, height: height ?? width }}>
        <Spin size="small" />
      </div>
    )
  }
  if (!preview) {
    return <img src={src} alt="collect" style={{ width, borderRadius: 4 }} />
  }
  return (
    <Image src={src} alt="collect" width={width} height={height} className="collect-shot-thumb" preview={{ cover: <EyeOutlined /> }} />
  )
}

function renderContacts(record: CollectRecord) {
  const contacts = extractContactsFromFields((record.fields || {}) as Record<string, unknown>)
  if (contacts.length === 0) return <Text type="secondary">-</Text>
  return (
    <Space orientation="vertical" size={2}>
      {contacts.slice(0, 4).map((c, i) => (
        <span key={`${c.channel}-${c.value}-${i}`}>{renderFindingValue(c.value, { copyable: true, maxWidth: 150 })}</span>
      ))}
    </Space>
  )
}

function renderDetailValue(value: unknown): string {
  if (Array.isArray(value)) {
    return value
      .map((item) => (typeof item === 'object' ? JSON.stringify(item, null, 2) : String(item)))
      .join('\n')
  }
  if (typeof value === 'object' && value !== null) return JSON.stringify(value, null, 2)
  return String(value)
}

function CollectRecordDetail({ record }: { record: CollectRecord }) {
  const fields = (record.fields || {}) as Record<string, unknown>
  const [sourceDetail, setSourceDetail] = useState<SourceDocumentDetail | null>(null)
  const [sourceLoading, setSourceLoading] = useState(false)
  const [sourceError, setSourceError] = useState('')

  useEffect(() => {
    let alive = true
    if (!record.source_document_id) {
      setSourceDetail(null)
      return () => { alive = false }
    }
    setSourceLoading(true)
    setSourceError('')
    getSourceDocument(
      record.source_document_id,
      record.project_id || undefined,
      record.source_document_version_id || undefined,
    )
      .then((detail) => { if (alive) setSourceDetail(detail) })
      .catch((error) => { if (alive) setSourceError((error as Error).message) })
      .finally(() => { if (alive) setSourceLoading(false) })
    return () => { alive = false }
  }, [record.project_id, record.source_document_id, record.source_document_version_id])

  const version = sourceDetail?.version
  const sourceContacts = (version?.contacts || []) as SourceContact[]
  const fallbackContacts = extractContactsFromFields(fields).map((item) => ({ ...item } as SourceContact))
  const contacts = sourceContacts.length ? sourceContacts : fallbackContacts
  const browserShots = version?.screenshots?.map((item) => item.url).filter(Boolean)
    || record.browser_screenshot_urls
    || []
  const browserShotSet = new Set(browserShots)
  const collectShots = record.discovery_screenshot_urls?.length
    ? record.discovery_screenshot_urls
    : (record.screenshot_urls || []).filter((url) => !browserShotSet.has(url))
  const images = version?.images || []
  const articleText = String(version?.content?.text || fields.content || fields.article_content || '')
  const excludedKeys = new Set(['content', 'article_content', 'image_context', 'contact'])
  const entries = Object.entries(fields).filter(([key, value]) => {
    if (excludedKeys.has(key)) return false
    if (Array.isArray(value)) return value.length > 0
    return value != null && value !== ''
  })
  const basicEntries = entries.filter(([key]) => classifyFieldKey(key) === 'basic')
  const bodyEntries = entries.filter(([key]) => classifyFieldKey(key) === 'body')
  const artifacts = version?.artifacts || {}

  const openArtifact = (path?: string) => {
    if (!path) return
    setSourceError('')
    openAuthenticatedArtifact(path).catch((error) => setSourceError((error as Error).message))
  }

  return (
    <div className="collect-record-detail">
      <div className="collect-detail-header">
        <Space size={6} wrap>
          {record.target_name && <Tag color="cyan">Target: {record.target_name}</Tag>}
          {record.score != null && <Tag color={scoreColor(record.score)}>相关性 {record.score}</Tag>}
          {record.subject_match != null && <Tag color={scoreColor(record.subject_match)}>主体对应 {record.subject_match}</Tag>}
          {record.keyword && <Tag>{record.keyword}</Tag>}
          {version?.version_id && <Tag>版本 {version.version_id.slice(-8)}</Tag>}
          {browserShots.length > 0 && <Tag icon={<PictureOutlined />}>浏览器截图 {browserShots.length}</Tag>}
        </Space>
      </div>

      {sourceLoading && (
        <Space size={8}>
          <Spin size="small" />
          <Text type="secondary">加载永久文章资产...</Text>
        </Space>
      )}
      {sourceError && <Alert type="warning" showIcon message="文章资产暂时无法读取" description={sourceError} />}

      {contacts.length > 0 && (
        <div className="collect-detail-section">
          <div className="collect-detail-section-title">联系方式与证据上下文</div>
          <div className="collect-contact-list">
            {contacts.map((contact, index) => (
              <div key={`${contact.channel}-${contact.value}-${index}`} className="collect-contact-evidence">
                <div className="collect-contact-row">
                  <Tag color="blue" className="collect-contact-channel">{contact.channel}</Tag>
                  {renderFindingValue(contact.value, { copyable: true, maxWidth: 420 })}
                  {contact.source === 'image' || contact.sources?.includes('image') ? <Tag>图片识别</Tag> : null}
                </div>
                {(contact.context || contact.contexts?.[0]) && (
                  <Text type="secondary" className="collect-contact-context">
                    {contact.context || contact.contexts?.[0]}
                  </Text>
                )}
              </div>
            ))}
          </div>
        </div>
      )}

      <div className="collect-detail-section">
        <div className="collect-detail-section-title">来源与结构化信息</div>
        <Descriptions
          size="small"
          bordered
          column={{ xxl: 2, xl: 2, lg: 2, md: 1, sm: 1, xs: 1 }}
          className="collect-detail-descriptions"
        >
          {record.target_name && <Descriptions.Item label="目标实体">{record.target_name}</Descriptions.Item>}
          {basicEntries.map(([key, value]) => (
            <Descriptions.Item key={key} label={key}>{renderDetailValue(value)}</Descriptions.Item>
          ))}
          {record.source_url && (
            <Descriptions.Item label="原文链接" span="filled">
              <a href={record.source_url} target="_blank" rel="noopener noreferrer">{record.source_url}</a>
            </Descriptions.Item>
          )}
        </Descriptions>
        {(artifacts.raw_html_url || artifacts.rendered_html_url || artifacts.structured_url) && (
          <Space wrap className="collect-artifact-actions">
            <Button size="small" icon={<FileTextOutlined />} onClick={() => openArtifact(artifacts.raw_html_url)}>
              原始响应 HTML
            </Button>
            <Button size="small" icon={<CodeOutlined />} onClick={() => openArtifact(artifacts.rendered_html_url)}>
              渲染后 DOM
            </Button>
            <Button size="small" icon={<DatabaseOutlined />} onClick={() => openArtifact(artifacts.structured_url)}>
              结构化 JSON
            </Button>
          </Space>
        )}
      </div>

      {bodyEntries.length > 0 && (
        <div className="collect-detail-section">
          <div className="collect-detail-section-title">分层结构化输出</div>
          <Descriptions size="small" bordered column={1} className="collect-detail-descriptions">
            {bodyEntries.map(([key, value]) => (
              <Descriptions.Item key={key} label={key}>
                <span className="collect-detail-prewrap">{renderDetailValue(value)}</span>
              </Descriptions.Item>
            ))}
          </Descriptions>
        </div>
      )}

      {articleText && (
        <div className="collect-detail-section">
          <div className="collect-detail-section-title">完整文章上下文</div>
          <div className="collect-article-context">{articleText}</div>
        </div>
      )}

      {images.length > 0 && (
        <div className="collect-detail-section">
          <div className="collect-detail-section-title">公众号原图与图片识别 ({images.length})</div>
          <Image.PreviewGroup>
            <div className="collect-source-image-list">
              {images.map((item) => (
                <div className="collect-source-image-item" key={`${item.storage_object_id}-${item.index}`}>
                  {item.url && <CollectShotImage url={item.url} width={132} />}
                  <div className="collect-source-image-analysis">
                    <Text strong>图片 {item.index + 1}</Text>
                    <Text>{item.analysis?.description || '已保存原图，暂无语义描述'}</Text>
                    {item.analysis?.visible_text && (
                      <Text type="secondary">可见文字：{item.analysis.visible_text}</Text>
                    )}
                  </div>
                </div>
              ))}
            </div>
          </Image.PreviewGroup>
        </div>
      )}

      {browserShots.length > 0 && (
        <div className="collect-detail-section">
          <div className="collect-detail-section-title">浏览器全文截图 ({browserShots.length})</div>
          <Image.PreviewGroup>
            <div className="collect-shot-gallery">
              {browserShots.map((url, index) => (
                <CollectShotImage key={`${url}-${index}`} url={url} width={112} />
              ))}
            </div>
          </Image.PreviewGroup>
        </div>
      )}

      {collectShots.length > 0 && (
        <div className="collect-detail-section">
          <div className="collect-detail-section-title">手机发现截图 ({collectShots.length})</div>
          <Image.PreviewGroup>
            <div className="collect-shot-gallery">
              {collectShots.map((url, index) => (
                <CollectShotImage key={`${url}-${index}`} url={url} width={96} />
              ))}
            </div>
          </Image.PreviewGroup>
        </div>
      )}

      {!entries.length && !articleText && !sourceLoading && (
        <Empty description="无结构化字段" image={Empty.PRESENTED_IMAGE_SIMPLE} />
      )}
      <Divider className="collect-detail-footer-divider" />
      <Text type="secondary">文档按规范 URL 去重；内容变化时生成新版本，历史原始产物不会覆盖。</Text>
    </div>
  )
}

export interface CollectRecordsViewProps {
  records: CollectRecord[]
  loading?: boolean
  emptyText?: React.ReactNode
  pageSize?: number
  /** 额外展示「主体对应」列(默认展示) */
  showSubjectMatch?: boolean
}

/** 采集记录统一展示:紧凑列表(缩略图+标题+相关性+联系方式)+ 小眼睛预览详情(分层分级)。 */
export default function CollectRecordsView({
  records,
  loading,
  emptyText,
  pageSize = 10,
  showSubjectMatch = true,
}: CollectRecordsViewProps) {
  const [detail, setDetail] = useState<CollectRecord | null>(null)

  const columns: ColumnsType<CollectRecord> = [
    {
      title: '',
      key: 'shot',
      width: 52,
      render: (_, r) =>
        r.screenshot_urls?.length ? (
          <CollectShotImage url={r.screenshot_urls[0]} width={40} height={40} />
        ) : (
          <div className="collect-shot-empty sm">无图</div>
        ),
    },
    {
      title: '内容',
      key: 'content',
      render: (_, r) => {
        const f = (r.fields || {}) as Record<string, unknown>
        const title = String(f.title ?? f.name ?? r.keyword ?? '无标题')
        const account = f.account != null ? String(f.account) : ''
        const publishTime = f.publish_time != null ? String(f.publish_time) : ''
        const meta = [account, publishTime].filter(Boolean).join(' · ')
        return (
          <div className="collect-row-cell">
            <div className="collect-row-title">
              {title}
              {r.is_new ? (
                <Tag color="green" className="collect-row-tag">新</Tag>
              ) : r.is_changed ? (
                <Tag color="orange" className="collect-row-tag">改</Tag>
              ) : null}
            </div>
            {meta && <div className="collect-row-meta">{meta}</div>}
          </div>
        )
      },
    },
    {
      title: '相关性',
      key: 'score',
      width: 84,
      sorter: (a, b) => (a.score ?? -1) - (b.score ?? -1),
      render: (_, r) => (r.score != null ? <Tag color={scoreColor(r.score)}>{r.score}</Tag> : <Text type="secondary">-</Text>),
    },
    {
      title: 'Target',
      key: 'target',
      width: 150,
      ellipsis: true,
      render: (_, r) => r.target_name ? <Tag color="cyan">{r.target_name}</Tag> : <Text type="secondary">未关联</Text>,
    },
    ...(showSubjectMatch
      ? ([
          {
            title: '主体对应',
            key: 'subject_match',
            width: 90,
            sorter: (a: CollectRecord, b: CollectRecord) => (a.subject_match ?? -1) - (b.subject_match ?? -1),
            render: (_: unknown, r: CollectRecord) =>
              r.subject_match != null ? <Tag color={scoreColor(r.subject_match)}>{r.subject_match}</Tag> : <Text type="secondary">-</Text>,
          },
        ] as ColumnsType<CollectRecord>)
      : []),
    {
      title: '联系方式',
      key: 'contacts',
      width: 160,
      render: (_, r) => renderContacts(r),
    },
    {
      title: '',
      key: 'action',
      width: 48,
      render: (_, r) => (
        <Tooltip title="预览">
          <Button type="text" size="small" icon={<EyeOutlined />} onClick={() => setDetail(r)} />
        </Tooltip>
      ),
    },
  ]

  return (
    <>
      <Table<CollectRecord>
        className="collect-records-table"
        rowKey="record_id"
        size="small"
        loading={loading}
        columns={columns}
        dataSource={records}
        locale={{ emptyText: <Empty description={emptyText ?? '暂无采集记录'} /> }}
        pagination={{ pageSize, hideOnSinglePage: true, showTotal: (t) => `共 ${t} 条` }}
      />
      <Modal
        open={!!detail}
        onCancel={() => setDetail(null)}
        footer={null}
        width={960}
        title={
          detail
            ? String(
                (detail.fields as Record<string, unknown>)?.title ??
                  (detail.fields as Record<string, unknown>)?.name ??
                  detail.keyword ??
                  '采集详情',
              )
            : '采集详情'
        }
        destroyOnHidden
      >
        {detail && <CollectRecordDetail record={detail} />}
      </Modal>
    </>
  )
}
