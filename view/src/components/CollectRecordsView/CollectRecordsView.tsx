import { useEffect, useMemo, useState } from 'react'
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

import { type CollectRecord } from '../../services/mobileCollectService'
import AuthenticatedImage from '../AuthenticatedImage'
import { CopyableText, OpenLinkButton } from '../CopyLinkButton'
import {
  getSourceDocument,
  openAuthenticatedArtifact,
  type SourceContact,
  type SourceDocumentDetail,
} from '../../services/sourceDocumentService'
import { renderFindingValue } from '../../utils/findingValueRenderer'
import {
  createIndividualCollectRecordGroups,
  extractContactsFromFields,
  groupCollectRecordsBySource,
  scoreColor,
  type CollectRecordGroup,
} from './collectRecordUtils'
import './CollectRecordsView.css'

const { Text } = Typography

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
  return (
    <AuthenticatedImage
      source={url}
      alt="采集截图"
      width={width}
      height={height ?? width}
      preview={preview}
      className="collect-shot-thumb"
    />
  )
}

function groupFieldContacts(group: CollectRecordGroup) {
  const seen = new Set<string>()
  return group.records.flatMap((record) => (
    extractContactsFromFields((record.fields || {}) as Record<string, unknown>)
  )).filter((contact) => {
    const key = `${contact.channel}:${contact.value.toLowerCase()}`
    if (seen.has(key)) return false
    seen.add(key)
    return true
  })
}

function renderContacts(group: CollectRecordGroup, limit = 4) {
  const contacts = groupFieldContacts(group)
  if (contacts.length === 0) return <Text type="secondary">-</Text>
  return (
    <Space orientation="vertical" size={2}>
      {contacts.slice(0, limit).map((c, i) => (
        <span key={`${c.channel}-${c.value}-${i}`}>{renderFindingValue(c.value, { copyable: true, maxWidth: 150, linkify: false })}</span>
      ))}
    </Space>
  )
}

function uniqueStrings(values: Array<string | null | undefined>): string[] {
  return [...new Set(values.map((value) => String(value || '').trim()).filter(Boolean))]
}

function mergeSourceContacts(contacts: SourceContact[]): SourceContact[] {
  const merged = new Map<string, SourceContact>()
  for (const contact of contacts) {
    const value = String(contact.value || '').trim()
    if (!value) continue
    const channel = String(contact.channel || 'contact').trim()
    const key = `${channel.toLowerCase()}:${value.toLowerCase()}`
    const existing = merged.get(key)
    if (!existing) {
      merged.set(key, {
        ...contact,
        channel,
        value,
        contexts: uniqueStrings([contact.context, ...(contact.contexts || [])]),
        sources: uniqueStrings([contact.source, ...(contact.sources || [])]),
      })
      continue
    }
    existing.contexts = uniqueStrings([
      existing.context,
      ...(existing.contexts || []),
      contact.context,
      ...(contact.contexts || []),
    ])
    existing.sources = uniqueStrings([
      existing.source,
      ...(existing.sources || []),
      contact.source,
      ...(contact.sources || []),
    ])
  }
  return [...merged.values()]
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

function CollectRecordDetail({ group }: { group: CollectRecordGroup }) {
  const record = group.primary
  const fields = Object.assign(
    {},
    ...group.records
      .filter((item) => item.record_id !== record.record_id)
      .map((item) => item.fields || {}),
    record.fields || {},
  ) as Record<string, unknown>
  const sourceDocumentId = record.source_document_id || group.sourceDocumentIds[0] || ''
  const sourceProjectId = record.project_id || ''
  const sourceVersionId = record.source_document_version_id || group.sourceDocumentVersionIds[0] || ''
  const sourceRequestKey = [sourceProjectId, sourceDocumentId, sourceVersionId].join(':')
  const [sourceResult, setSourceResult] = useState<{
    requestKey: string
    detail: SourceDocumentDetail | null
    error: string
  }>({ requestKey: '', detail: null, error: '' })
  const [artifactError, setArtifactError] = useState<{
    requestKey: string
    error: string
  }>({ requestKey: '', error: '' })
  const hasCurrentSourceResult = sourceResult.requestKey === sourceRequestKey
  const sourceDetail = hasCurrentSourceResult ? sourceResult.detail : null
  const sourceError = (
    (hasCurrentSourceResult ? sourceResult.error : '')
    || (artifactError.requestKey === sourceRequestKey ? artifactError.error : '')
  )
  const sourceLoading = Boolean(sourceDocumentId) && !hasCurrentSourceResult

  useEffect(() => {
    if (!sourceDocumentId) return undefined
    let alive = true
    getSourceDocument(
      sourceDocumentId,
      sourceProjectId || undefined,
      sourceVersionId || undefined,
    )
      .then((detail) => {
        if (alive) setSourceResult({ requestKey: sourceRequestKey, detail, error: '' })
      })
      .catch((error) => {
        if (alive) {
          setSourceResult({
            requestKey: sourceRequestKey,
            detail: null,
            error: (error as Error).message,
          })
        }
      })
    return () => { alive = false }
  }, [sourceDocumentId, sourceProjectId, sourceRequestKey, sourceVersionId])

  const version = sourceDetail?.version
  const sourceContacts = (version?.contacts || []) as SourceContact[]
  const fallbackContacts = groupFieldContacts(group).map((item) => ({ ...item } as SourceContact))
  const projectIds = new Set(group.records.map((item) => item.project_id).filter(Boolean))
  const targetIds = new Set(group.targetIds)
  const targetContacts = (sourceDetail?.links || [])
    .filter((link) => (
      (!projectIds.size || projectIds.has(link.project_id))
      && (!targetIds.size || !link.target_id || targetIds.has(link.target_id))
    ))
    .flatMap((link) => link.latest_analysis?.target_contacts || [])
  const contacts = mergeSourceContacts([...targetContacts, ...sourceContacts, ...fallbackContacts])
  const browserShots = uniqueStrings([
    ...(version?.screenshots?.map((item) => item.url).filter(Boolean) || []),
    ...group.browserScreenshotUrls,
  ])
  const browserShotSet = new Set(browserShots)
  const collectShots = uniqueStrings([
    ...group.discoveryScreenshotUrls,
    ...group.screenshotUrls.filter((url) => !browserShotSet.has(url)),
  ])
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
    setArtifactError({ requestKey: sourceRequestKey, error: '' })
    openAuthenticatedArtifact(path).catch((error) => {
      setArtifactError({
        requestKey: sourceRequestKey,
        error: (error as Error).message,
      })
    })
  }

  return (
    <div className="collect-record-detail">
      <div className="collect-detail-header">
        <Space size={6} wrap>
          {group.targetNames.map((targetName) => <Tag key={targetName} color="cyan">Target: {targetName}</Tag>)}
          {group.score != null && <Tag color={scoreColor(group.score)}>相关性 {group.score}</Tag>}
          {group.subjectMatch != null && <Tag color={scoreColor(group.subjectMatch)}>主体对应 {group.subjectMatch}</Tag>}
          {group.keywords.map((keyword) => <Tag key={keyword}>{keyword}</Tag>)}
          {group.records.length > 1 && <Tag color="geekblue">采集证据 {group.records.length}</Tag>}
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
                  {renderFindingValue(contact.value, { copyable: true, maxWidth: 420, linkify: false })}
                  {contact.source === 'image' || contact.sources?.includes('image') ? <Tag>图片识别</Tag> : null}
                </div>
                {uniqueStrings([contact.context, ...(contact.contexts || [])]).map((context) => (
                  <Text key={context} type="secondary" className="collect-contact-context">
                    {context}
                  </Text>
                ))}
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
          {group.targetNames.length > 0 && <Descriptions.Item label="目标实体">{group.targetNames.join('、')}</Descriptions.Item>}
          {basicEntries.map(([key, value]) => (
            <Descriptions.Item key={key} label={key}>{renderDetailValue(value)}</Descriptions.Item>
          ))}
          {group.sourceUrl && (
            <Descriptions.Item label="原文链接" span="filled">
              <span className="collect-source-link-actions">
                <CopyableText
                  value={group.sourceUrl}
                  copyLabel="原文链接"
                  style={{ minWidth: 0, flex: 1, wordBreak: 'break-all' }}
                />
                <OpenLinkButton value={group.sourceUrl} label="公众号原文" />
              </span>
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
  /** 展示手机发现后的浏览器全文归档状态 */
  showBrowserArchive?: boolean
  /** 将同一来源链接的多条发现合并为一篇文章，仅改变读模型，不修改原始记录 */
  groupBySource?: boolean
}

/** 采集记录统一展示:紧凑列表(缩略图+标题+相关性+联系方式)+ 小眼睛预览详情(分层分级)。 */
export default function CollectRecordsView({
  records,
  loading,
  emptyText,
  pageSize = 10,
  showSubjectMatch = true,
  showBrowserArchive = false,
  groupBySource = false,
}: CollectRecordsViewProps) {
  const [detail, setDetail] = useState<CollectRecordGroup | null>(null)
  const groups = useMemo(
    () => groupBySource
      ? groupCollectRecordsBySource(records)
      : createIndividualCollectRecordGroups(records),
    [groupBySource, records],
  )

  const compactColumns: ColumnsType<CollectRecordGroup> = [
    {
      title: '',
      key: 'shot',
      width: 52,
      render: (_, group) =>
        group.screenshotUrls.length ? (
          <CollectShotImage url={group.screenshotUrls[0]} width={40} height={40} />
        ) : (
          <div className="collect-shot-empty sm">无图</div>
        ),
    },
    {
      title: '内容',
      key: 'content',
      render: (_, group) => {
        const meta = [group.account, group.publishTime].filter(Boolean).join(' · ')
        return (
          <div className="collect-row-cell">
            <div className="collect-row-title">
              {group.title}
              {group.isNew ? (
                <Tag color="green" className="collect-row-tag">新</Tag>
              ) : group.isChanged ? (
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
      render: (_, group) => group.score != null
        ? <Tag color={scoreColor(group.score)}>{group.score}</Tag>
        : <Text type="secondary">-</Text>,
    },
    {
      title: 'Target',
      key: 'target',
      width: 150,
      ellipsis: true,
      render: (_, group) => group.targetNames.length
        ? <Tag color="cyan">{group.targetNames[0]}</Tag>
        : <Text type="secondary">未关联</Text>,
    },
    ...(showBrowserArchive
      ? ([
          {
            title: '浏览器池归档',
            key: 'browser_archive',
            width: 142,
            render: (_: unknown, group: CollectRecordGroup) => {
              const screenshotCount = group.browserScreenshotUrls.length
              if (group.sourceDocumentIds.length) {
                return (
                  <Space orientation="vertical" size={2}>
                    <Tag color="success" icon={<DatabaseOutlined />}>已归档</Tag>
                    {group.sourceDocumentVersionIds[0] && (
                      <Text type="secondary">版本 {group.sourceDocumentVersionIds[0].slice(-8)}</Text>
                    )}
                    {screenshotCount > 0 && <Text type="secondary">全文截图 {screenshotCount}</Text>}
                  </Space>
                )
              }
              return group.sourceUrl
                ? <Tag color="warning">尚未归档</Tag>
                : <Tag>无原文链接</Tag>
            },
          },
        ] as ColumnsType<CollectRecordGroup>)
      : []),
    ...(showSubjectMatch
      ? ([
          {
            title: '主体对应',
            key: 'subject_match',
            width: 90,
            sorter: (a: CollectRecordGroup, b: CollectRecordGroup) => (a.subjectMatch ?? -1) - (b.subjectMatch ?? -1),
            render: (_: unknown, group: CollectRecordGroup) =>
              group.subjectMatch != null
                ? <Tag color={scoreColor(group.subjectMatch)}>{group.subjectMatch}</Tag>
                : <Text type="secondary">-</Text>,
          },
        ] as ColumnsType<CollectRecordGroup>)
      : []),
    {
      title: '联系方式',
      key: 'contacts',
      width: 160,
      render: (_, group) => renderContacts(group),
    },
    {
      title: '',
      key: 'action',
      width: 76,
      render: (_, group) => (
        <Space size={0}>
          <Tooltip title={group.sourceDocumentIds.length ? '查看浏览器全文归档' : '查看采集详情'}>
            <Button
              type="text"
              size="small"
              aria-label={group.sourceDocumentIds.length ? '查看浏览器全文归档' : '查看采集详情'}
              icon={group.sourceDocumentIds.length ? <DatabaseOutlined /> : <EyeOutlined />}
              onClick={() => setDetail(group)}
            />
          </Tooltip>
          <OpenLinkButton value={group.sourceUrl} label="公众号原文" />
        </Space>
      ),
    },
  ]

  const groupedColumns: ColumnsType<CollectRecordGroup> = [
    {
      title: '',
      key: 'shot',
      width: 76,
      responsive: ['sm'],
      render: (_, group) => {
        const url = group.discoveryScreenshotUrls[0]
          || group.screenshotUrls[0]
          || group.browserScreenshotUrls[0]
        return url
          ? <CollectShotImage url={url} width={56} height={56} />
          : <div className="collect-shot-empty">无图</div>
      },
    },
    {
      title: '公众号文章',
      key: 'source_summary',
      render: (_, group) => {
        const contacts = groupFieldContacts(group)
        return (
          <div className="collect-source-summary">
            <div className="collect-source-title-row">
              <span className="collect-source-title">{group.title}</span>
              {group.isNew ? <Tag color="green">新</Tag> : group.isChanged ? <Tag color="orange">改</Tag> : null}
            </div>
            {(group.account || group.publishTime) && (
              <div className="collect-source-meta">
                {[group.account, group.publishTime].filter(Boolean).join(' · ')}
              </div>
            )}
            <div className="collect-source-facts">
              {group.score != null && <Tag color={scoreColor(group.score)}>相关性 {group.score}</Tag>}
              {showSubjectMatch && group.subjectMatch != null && (
                <Tag color={scoreColor(group.subjectMatch)}>主体对应 {group.subjectMatch}</Tag>
              )}
              {group.targetNames.map((targetName) => <Tag key={targetName} color="cyan">{targetName}</Tag>)}
              {group.records.length > 1 && <Tag color="geekblue">采集证据 {group.records.length}</Tag>}
              {showBrowserArchive && group.sourceDocumentIds.length > 0 && (
                <Tag color="success" icon={<DatabaseOutlined />}>浏览器已归档</Tag>
              )}
              {group.sourceDocumentVersionIds.length > 0 && (
                <Tag>内容版本 {group.sourceDocumentVersionIds.length}</Tag>
              )}
              {group.browserScreenshotUrls.length > 0 && (
                <Tag icon={<PictureOutlined />}>全文截图 {group.browserScreenshotUrls.length}</Tag>
              )}
            </div>
            {contacts.length > 0 && (
              <div className="collect-source-contacts">
                <Text type="secondary">联系方式</Text>
                <Space size={[8, 4]} wrap>
                  {contacts.slice(0, 8).map((contact) => (
                    <span key={`${contact.channel}-${contact.value}`}>
                      {renderFindingValue(contact.value, { copyable: true, maxWidth: 220, linkify: false })}
                    </span>
                  ))}
                  {contacts.length > 8 && <Tag>另有 {contacts.length - 8} 条</Tag>}
                </Space>
              </div>
            )}
          </div>
        )
      },
    },
    {
      title: '',
      key: 'action',
      width: 76,
      align: 'right',
      render: (_, group) => (
        <Space size={0}>
          <Tooltip title="查看文章归档与全部采集证据">
            <Button
              type="text"
              size="small"
              aria-label="查看文章归档与全部采集证据"
              icon={<EyeOutlined />}
              onClick={() => setDetail(group)}
            />
          </Tooltip>
          <OpenLinkButton value={group.sourceUrl} label="公众号原文" />
        </Space>
      ),
    },
  ]

  return (
    <>
      <Table<CollectRecordGroup>
        className={`collect-records-table${groupBySource ? ' collect-source-groups-table' : ''}`}
        rowKey="groupKey"
        size="small"
        loading={loading}
        columns={groupBySource ? groupedColumns : compactColumns}
        dataSource={groups}
        tableLayout={groupBySource ? 'fixed' : undefined}
        locale={{ emptyText: <Empty description={emptyText ?? '暂无采集记录'} /> }}
        pagination={{
          pageSize,
          hideOnSinglePage: true,
          showTotal: (total) => groupBySource ? `共 ${total} 篇文章` : `共 ${total} 条`,
        }}
      />
      <Modal
        open={!!detail}
        onCancel={() => setDetail(null)}
        footer={null}
        width={960}
        title={
          detail
            ? detail.title
            : '采集详情'
        }
        destroyOnHidden
      >
        {detail && <CollectRecordDetail group={detail} />}
      </Modal>
    </>
  )
}
