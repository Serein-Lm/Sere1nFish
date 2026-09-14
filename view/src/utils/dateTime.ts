const beijingTimestamp = new Intl.DateTimeFormat('zh-CN', {
  timeZone: 'Asia/Shanghai',
  year: 'numeric', month: '2-digit', day: '2-digit',
  hour: '2-digit', minute: '2-digit', second: '2-digit', hourCycle: 'h23',
})

/** Mongo's legacy timestamps without a suffix represent UTC, not browser local time. */
export function formatBeijingTimestamp(value?: string | null, emptyText = '-'): string {
  if (!value) return emptyText
  const timestamp = /(Z|[+-]\d{2}:\d{2})$/i.test(value) ? value : `${value}Z`
  const date = new Date(timestamp)
  if (Number.isNaN(date.getTime())) return value
  const parts = Object.fromEntries(beijingTimestamp.formatToParts(date).map(({ type, value: part }) => [type, part]))
  return `${parts.year}-${parts.month}-${parts.day} ${parts.hour}:${parts.minute}:${parts.second}`
}
