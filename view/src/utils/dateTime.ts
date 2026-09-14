/** Mongo's legacy timestamps without a suffix represent UTC, not browser local time. */
export function formatBeijingTimestamp(value?: string | null, emptyText = '-'): string {
  if (!value) return emptyText
  const timestamp = /(Z|[+-]\d{2}:\d{2})$/i.test(value) ? value : `${value}Z`
  const date = new Date(timestamp)
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString('zh-CN', { timeZone: 'Asia/Shanghai' })
}
