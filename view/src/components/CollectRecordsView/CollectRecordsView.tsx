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
import type { ColumnsType } from 'antd/es/table'
import { EyeOutlined, PictureOutlined } from '@ant-design/icons'

import { fetchScreenshotObjectUrl, type CollectRecord } from '../../services/mobileCollectService'
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
    <Image src={src} alt="collect" width={width} height={height} className="collect-shot-thumb" preview={{ mask: <EyeOutlined /> }} />
  )
}

function renderContacts(record: CollectRecord) {
  const contacts = extractContactsFromFields((record.fields || {}) as Record<string, unknown>)
  if (contacts.length === 0) return <Text type="secondary">-</Text>
  return (
    <Space direction="vertical" size={2}>
      {contacts.slice(0, 4).map((c, i) => (
        <span key={`${c.channel}-${c.value}-${i}`}>{renderFindingValue(c.value, { copyable: true, maxWidth: 150 })}</span>
      ))}
    </Space>
  )
}

function CollectRecordDetail({ record }: { record: CollectRecord }) {
  const fields = (record.fields || {}) as Record<string, unknown>
  const entries = Object.entries(fields).filter(([, v]) => {
    if (Array.isArray(v)) return v.length > 0
    return v != null && v !== ''
  })
  const basicEntries = entries.filter(([k]) => classifyFieldKey(k) === 'basic')
  const bodyEntries = entries.filter(([k]) => classifyFieldKey(k) === 'body')
  const contacts = extractContactsFromFields(fields)
  const shots = record.screenshot_urls || []
  const renderVal = (v: unknown) => (Array.isArray(v) ? v.map((x) => String(x)).join('、') : String(v))
  return (
    <div className="collect-record-detail">
      <div className="collect-detail-header">
        <Space size={6} wrap>
          {record.score != null && <Tag color={scoreColor(record.score)}>相关性 {record.score}</Tag>}
          {record.subject_match != null && <Tag color={scoreColor(record.subject_match)}>主体对应 {record.subject_match}</Tag>}
          {record.keyword && <Tag>{record.keyword}</Tag>}
          {shots.length > 0 && <Tag icon={<PictureOutlined />}>{shots.length} 张截图</Tag>}
        </Space>
      </div>

      {contacts.length > 0 && (
        <div className="collect-detail-section">
          <div className="collect-detail-section-title">联系方式</div>
          <Space direction="vertical" size={4} style={{ width: '100%' }}>
            {contacts.map((c, i) => (
              <div key={`${c.channel}-${c.value}-${i}`} className="collect-contact-row">
                <Tag color="blue" className="collect-contact-channel">{c.channel}</Tag>
                {renderFindingValue(c.value, { copyable: true, maxWidth: 320 })}
              </div>
            ))}
          </Space>
        </div>
      )}

      {basicEntries.length > 0 && (
        <div className="collect-detail-section">
          <div className="collect-detail-section-title">基本信息</div>
          <Descriptions size="small" bordered column={{ xxl: 2, xl: 2, lg: 2, md: 1, sm: 1, xs: 1 }} className="collect-detail-descriptions">
            {basicEntries.map(([k, v]) => (
              <Descriptions.Item key={k} label={k}>{renderVal(v)}</Descriptions.Item>
            ))}
            {record.source_url && (
              <Descriptions.Item label="原文链接" span={2}>
                <a href={record.source_url} target="_blank" rel="noopener noreferrer">{record.source_url}</a>
              </Descriptions.Item>
            )}
          </Descriptions>
        </div>
      )}

      {bodyEntries.length > 0 && (
        <div className="collect-detail-section">
          <div className="collect-detail-section-title">正文 / 背景</div>
          <Descriptions size="small" bordered column={1} className="collect-detail-descriptions">
            {bodyEntries.map(([k, v]) => (
              <Descriptions.Item key={k} label={k}>{renderVal(v)}</Descriptions.Item>
            ))}
          </Descriptions>
        </div>
      )}

      {entries.length === 0 && <Empty description="无结构化字段" image={Empty.PRESENTED_IMAGE_SIMPLE} />}

      {shots.length > 0 && (
        <div className="collect-detail-section">
          <div className="collect-detail-section-title">截图 ({shots.length})</div>
          <Image.PreviewGroup>
            <div className="collect-shot-gallery">
              {shots.map((u, i) => (
                <CollectShotImage key={`${u}-${i}`} url={u} width={96} />
              ))}
            </div>
          </Image.PreviewGroup>
        </div>
      )}
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
        width={720}
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
        destroyOnClose
      >
        {detail && <CollectRecordDetail record={detail} />}
      </Modal>
    </>
  )
}
