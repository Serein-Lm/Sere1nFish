const MOBILE_RE = /(?<![A-Za-z0-9._%+-])(1[3-9]\d{9})(?![\d@])/g
const TEL_KW_RE = /(?:联系电话|电话|联系方式|座机|Tel|TEL|tel)\s*[:：]?\s*(1[3-9]\d{9}|(?:0\d{2,3}[-\s]|\(0\d{2,3}\)[-\s]?)\d{7,8})(?!\d)/g
const LANDLINE_RE = /(?<!\d)((?:0\d{2,3}[-\s]|\(0\d{2,3}\)[-\s]?)\d{7,8})(?!\d)/g
const SERVICE_TEL_RE = /(?<!\d)((?:400|800)[-\s]?\d{3}[-\s]?\d{4})(?!\d)/g
const EMAIL_RE = /([A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,})/g

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
