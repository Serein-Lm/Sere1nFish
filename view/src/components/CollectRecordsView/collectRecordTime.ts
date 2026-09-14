import type { CollectRecord } from '../../services/mobileCollectService'
import { formatBeijingTimestamp } from '../../utils/dateTime'

export const PUBLICATION_TIME_KEYS = ['publish_time', '发布时间', 'published_at', 'publish_date', 'published_time', 'date']

function publicationTime(record: CollectRecord): { text: string; source: string } {
  const raw = PUBLICATION_TIME_KEYS.map((key) => String(record.fields?.[key] || '').trim()).find(Boolean) || ''
  const normalized = formatBeijingTimestamp(record.published_at, '')
  const relative = /前|刚刚|现在|今天|昨天|前天/.test(raw)
  if (!normalized) return { text: relative ? '时间待核实' : raw, source: raw }
  if (relative) {
    return { text: `${normalized.slice(0, 10)}（估算日期）`, source: `采集时原文：${raw}；按该次采集时间换算` }
  }
  // 发布日期不能补成精确到秒的事实；采集时间另行完整展示。
  const hasClock = /\d{1,2}[:：时]\d{1,2}/.test(raw)
  const hasSeconds = /\d{1,2}:\d{2}:\d{2}|\d{1,2}分\d{1,2}秒/.test(raw)
  const text = raw && !hasClock && !/^\d{10,13}$/.test(raw)
    ? normalized.slice(0, 10)
    : raw && hasClock && !hasSeconds ? normalized.slice(0, 16) : normalized
  return { text, source: raw ? `原文发布时间：${raw}` : '归档发布时间（北京时间）' }
}

/** 优先使用持久化发布时间，避免每次打开历史记录都按当前时间重新换算。 */
export function collectRecordTimes(records: CollectRecord[], primary: CollectRecord) {
  const dated = primary.published_at ? primary : records.find((record) => record.published_at) || primary
  const publication = publicationTime(dated)
  const firstSeen = records.map((record) => formatBeijingTimestamp(record.first_seen, '')).filter(Boolean).sort()[0] || ''
  const lastSeen = records.map((record) => formatBeijingTimestamp(record.last_seen || record.first_seen, '')).filter(Boolean).sort().at(-1) || ''
  return { publishTime: publication.text, publishTimeSource: publication.source, firstSeenTime: firstSeen, lastSeenTime: lastSeen }
}
