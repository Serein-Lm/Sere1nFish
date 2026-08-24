import type { CollectRecord } from '../../services/mobileCollectService'

const MOBILE_RE = /(?<![A-Za-z0-9._%+-])(1[3-9]\d{9})(?![\d@])/g
const TEL_KW_RE = /(?:联系电话|电话|联系方式|座机|Tel|TEL|tel)\s*[:：]?\s*(1[3-9]\d{9}|(?:0\d{2,3}[-\s]|\(0\d{2,3}\)[-\s]?)\d{7,8})(?!\d)/g
const LANDLINE_RE = /(?<!\d)((?:0\d{2,3}[-\s]|\(0\d{2,3}\)[-\s]?)\d{7,8})(?!\d)/g
const SERVICE_TEL_RE = /(?<!\d)((?:400|800)[-\s]?\d{3}[-\s]?\d{4})(?!\d)/g
const EMAIL_RE = /([A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,})/g
const TRACKING_PARAMS = new Set([
  'ascene',
  'clicktime',
  'enterid',
  'from',
  'isappinstalled',
  'scene',
  'sessionid',
  'subscene',
  'utm_campaign',
  'utm_content',
  'utm_medium',
  'utm_source',
  'utm_term',
])

export interface CollectRecordGroup {
  groupKey: string
  primary: CollectRecord
  records: CollectRecord[]
  sourceUrl: string
  sourceDocumentIds: string[]
  sourceDocumentVersionIds: string[]
  screenshotUrls: string[]
  browserScreenshotUrls: string[]
  discoveryScreenshotUrls: string[]
  targetIds: string[]
  targetNames: string[]
  keywords: string[]
  title: string
  account: string
  publishTime: string
  score: number | null
  subjectMatch: number | null
  isNew: boolean
  isChanged: boolean
}

function uniqueStrings(values: Array<string | null | undefined>): string[] {
  return [...new Set(values.map((value) => String(value || '').trim()).filter(Boolean))]
}

export function canonicalizeCollectSourceUrl(value?: string | null): string {
  const normalized = String(value || '').trim()
  if (!normalized) return ''
  try {
    const url = new URL(normalized)
    if (url.protocol !== 'http:' && url.protocol !== 'https:') return ''

    // 微信安全校验页携带真实文章地址，聚合时使用真实来源身份。
    if (url.hostname.toLowerCase() === 'mp.weixin.qq.com' && url.pathname === '/mp/wappoc_appmsgcaptcha') {
      const targetUrl = url.searchParams.get('target_url')
      if (targetUrl && targetUrl !== normalized) {
        return canonicalizeCollectSourceUrl(targetUrl) || normalized
      }
    }

    url.hash = ''
    for (const key of [...url.searchParams.keys()]) {
      if (TRACKING_PARAMS.has(key.toLowerCase()) || key.toLowerCase().startsWith('utm_')) {
        url.searchParams.delete(key)
      }
    }
    url.searchParams.sort()
    if (url.pathname.length > 1) url.pathname = url.pathname.replace(/\/+$/, '')
    return url.toString()
  } catch {
    return normalized
  }
}

function recordField(record: CollectRecord, keys: string[]): string {
  const fields = (record.fields || {}) as Record<string, unknown>
  for (const key of keys) {
    const value = fields[key]
    if (value != null && String(value).trim()) return String(value).trim()
  }
  return ''
}

function firstRecordValue(records: CollectRecord[], keys: string[]): string {
  for (const record of records) {
    const value = recordField(record, keys)
    if (value) return value
  }
  return ''
}

function recordTimestamp(record: CollectRecord): number {
  for (const value of [record.last_seen, record.sort_time, record.published_at, record.first_seen]) {
    if (!value) continue
    const timestamp = Date.parse(value)
    if (Number.isFinite(timestamp)) return timestamp
  }
  return 0
}

function selectPrimaryRecord(records: CollectRecord[]): CollectRecord {
  return [...records].sort((left, right) => {
    const archiveDelta = Number(Boolean(right.source_document_version_id)) - Number(Boolean(left.source_document_version_id))
    if (archiveDelta) return archiveDelta
    const timestampDelta = recordTimestamp(right) - recordTimestamp(left)
    if (timestampDelta) return timestampDelta
    return (right.score ?? -1) - (left.score ?? -1)
  })[0]
}

function maxNullable(values: Array<number | null | undefined>): number | null {
  const valid = values.filter((value): value is number => typeof value === 'number' && Number.isFinite(value))
  return valid.length ? Math.max(...valid) : null
}

function buildRecordGroup(groupKey: string, records: CollectRecord[]): CollectRecordGroup {
  const primary = selectPrimaryRecord(records)
  const sourceUrl = records
    .map((record) => String(record.source_url || '').trim())
    .find(Boolean) || ''
  const title = firstRecordValue(records, ['title', '文章标题', '标题', 'article_title', 'name'])
    || primary.keyword
    || '无标题'
  const account = firstRecordValue(records, ['account', '公众号', '公众号名称', 'author', '来源'])
  const publishTime = firstRecordValue(records, ['publish_time', '发布时间', 'published_at', 'date'])
    || records.map((record) => record.published_at || '').find(Boolean)
    || ''

  return {
    groupKey,
    primary,
    records,
    sourceUrl,
    sourceDocumentIds: uniqueStrings(records.map((record) => record.source_document_id)),
    sourceDocumentVersionIds: uniqueStrings(records.map((record) => record.source_document_version_id)),
    screenshotUrls: uniqueStrings(records.flatMap((record) => record.screenshot_urls || [])),
    browserScreenshotUrls: uniqueStrings(records.flatMap((record) => record.browser_screenshot_urls || [])),
    discoveryScreenshotUrls: uniqueStrings(records.flatMap((record) => record.discovery_screenshot_urls || [])),
    targetIds: uniqueStrings(records.map((record) => record.target_id)),
    targetNames: uniqueStrings(records.map((record) => record.target_name)),
    keywords: uniqueStrings(records.map((record) => record.keyword)),
    title,
    account,
    publishTime,
    score: maxNullable(records.map((record) => record.score)),
    subjectMatch: maxNullable(records.map((record) => record.subject_match)),
    isNew: records.some((record) => record.is_new),
    isChanged: records.some((record) => record.is_changed),
  }
}

/**
 * 构建来源文档读模型。URL 和 source_document_id 都作为别名参与归并，
 * 因而同一来源的多次手机发现或历史迁移记录只生成一个文章展示单元。
 */
export function groupCollectRecordsBySource(records: CollectRecord[]): CollectRecordGroup[] {
  interface MutableGroup {
    records: CollectRecord[]
    firstIndex: number
  }

  const groups = new Set<MutableGroup>()
  const groupByAlias = new Map<string, MutableGroup>()

  records.forEach((record, index) => {
    const canonicalUrl = canonicalizeCollectSourceUrl(record.source_url)
    const aliases = uniqueStrings([
      canonicalUrl ? `url:${canonicalUrl}` : '',
      record.source_document_id ? `document:${record.source_document_id}` : '',
    ])
    const matched = [...new Set(
      aliases
        .map((alias) => groupByAlias.get(alias))
        .filter((group): group is MutableGroup => Boolean(group)),
    )]

    let group = matched[0]
    if (!group) {
      group = { records: [], firstIndex: index }
      groups.add(group)
    }

    for (const other of matched.slice(1)) {
      if (other === group) continue
      group.records.push(...other.records)
      group.firstIndex = Math.min(group.firstIndex, other.firstIndex)
      for (const [alias, linkedGroup] of groupByAlias.entries()) {
        if (linkedGroup === other) groupByAlias.set(alias, group)
      }
      groups.delete(other)
    }

    if (!group.records.some((item) => item.record_id === record.record_id)) group.records.push(record)
    for (const alias of aliases) groupByAlias.set(alias, group)
  })

  return [...groups]
    .sort((left, right) => left.firstIndex - right.firstIndex)
    .map((group) => {
      const first = group.records[0]
      const canonicalUrl = canonicalizeCollectSourceUrl(first.source_url)
      const groupKey = canonicalUrl
        ? `url:${canonicalUrl}`
        : first.source_document_id
          ? `document:${first.source_document_id}`
          : `record:${first.record_id}`
      return buildRecordGroup(groupKey, group.records)
    })
}

export function createIndividualCollectRecordGroups(records: CollectRecord[]): CollectRecordGroup[] {
  return records.map((record) => buildRecordGroup(`record:${record.record_id}`, [record]))
}

function normalizeTelephone(value: string): string {
  const compact = value.replace(/\s+/g, '')
  const parenthesized = compact.match(/^\((0\d{2,3})\)(\d{7,8})$/)
  if (parenthesized) return `${parenthesized[1]}-${parenthesized[2]}`
  return compact
}

function fieldsToText(fields: Record<string, unknown>): string {
  const values = Object.prototype.hasOwnProperty.call(fields, 'contact')
    ? [fields.contact]
    : Object.values(fields || {})
  const parts: string[] = []
  for (const value of values) {
    if (Array.isArray(value)) parts.push(value.map((item) => String(item)).join(' '))
    else if (value != null && value !== '') parts.push(String(value))
  }
  return parts.join('\n').normalize('NFKC')
}

export function scoreColor(score: number): string {
  if (score >= 80) return 'green'
  if (score >= 60) return 'blue'
  if (score >= 40) return 'orange'
  return 'default'
}

export function extractContactsFromFields(
  fields: Record<string, unknown>,
): { channel: string; value: string }[] {
  const text = fieldsToText(fields)
  const contacts: { channel: string; value: string }[] = []
  const seen = new Set<string>()
  const add = (channel: string, value: string) => {
    const normalized = value.trim()
    if (!normalized) return
    const key = `${channel}:${normalized.toLowerCase()}`
    if (seen.has(key)) return
    seen.add(key)
    contacts.push({ channel, value: normalized })
  }

  for (const match of text.matchAll(EMAIL_RE)) add('email', match[1])
  for (const match of text.matchAll(MOBILE_RE)) add('phone', match[1])
  for (const match of text.matchAll(TEL_KW_RE)) {
    const value = normalizeTelephone(match[1])
    add(/^1[3-9]\d{9}$/.test(value) ? 'phone' : 'telephone', value)
  }
  for (const match of text.matchAll(LANDLINE_RE)) add('telephone', normalizeTelephone(match[1]))
  for (const match of text.matchAll(SERVICE_TEL_RE)) add('telephone', normalizeTelephone(match[1]))
  return contacts
}
