import type { CSSProperties, MouseEvent, ReactNode } from 'react'
import Button from 'antd/es/button'
import Tooltip from 'antd/es/tooltip'
import message from 'antd/es/message'
import { CopyOutlined } from '@ant-design/icons'

async function copyText(value: string): Promise<void> {
  if (navigator.clipboard?.writeText) {
    await navigator.clipboard.writeText(value)
    return
  }

  const textarea = document.createElement('textarea')
  textarea.value = value
  textarea.style.position = 'fixed'
  textarea.style.opacity = '0'
  document.body.appendChild(textarea)
  textarea.select()
  const copied = document.execCommand('copy')
  textarea.remove()
  if (!copied) throw new Error('浏览器拒绝复制')
}

export interface CopyLinkButtonProps {
  value?: string | null
  label?: string
  className?: string
}

export default function CopyLinkButton({
  value,
  label = '链接',
  className,
}: CopyLinkButtonProps) {
  const normalized = String(value || '').trim()

  const handleCopy = async (event: MouseEvent<HTMLElement>) => {
    event.preventDefault()
    event.stopPropagation()
    if (!normalized) return
    try {
      await copyText(normalized)
      message.success(`${label}已复制`)
    } catch (error) {
      message.error(error instanceof Error ? error.message : '复制失败')
    }
  }

  return (
    <Tooltip title={`复制${label}`}>
      <Button
        type="text"
        size="small"
        className={className}
        aria-label={`复制${label}`}
        icon={<CopyOutlined />}
        disabled={!normalized}
        onClick={handleCopy}
        style={{ width: 24, minWidth: 24, height: 24, minHeight: 24, padding: 0, flex: '0 0 24px' }}
      />
    </Tooltip>
  )
}

export interface CopyableLinkProps {
  href: string
  children?: ReactNode
  className?: string
  title?: string
  style?: CSSProperties
  copyLabel?: string
}

export function CopyableLink({
  href,
  children,
  className,
  title,
  style,
  copyLabel = '链接',
}: CopyableLinkProps) {
  return (
    <span style={{ display: 'inline-flex', alignItems: 'center', gap: 2, minWidth: 0, maxWidth: '100%' }}>
      <a
        href={href}
        target="_blank"
        rel="noopener noreferrer"
        className={className}
        title={title}
        style={{ minWidth: 0, ...style }}
      >
        {children ?? href}
      </a>
      <CopyLinkButton value={href} label={copyLabel} />
    </span>
  )
}
