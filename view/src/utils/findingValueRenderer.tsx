import React from 'react'
import { Tag, Tooltip, Image } from 'antd'
import { CopyOutlined } from '@ant-design/icons'

/**
 * Finding value 智能渲染
 * 根据 value 内容自动判断渲染方式
 */

const IMAGE_EXTS = /\.(png|jpe?g|gif|webp)(\?.*)?$/i
const URL_PATTERN = /^https?:\/\//i
const EMAIL_PATTERN = /@/
const PHONE_PATTERN = /^[\d\-+\s()]+$/

export function renderFindingValue(
  value: string | null | undefined,
  options?: { copyable?: boolean; maxWidth?: number }
): React.ReactNode {
  if (value == null || value === '') {
    return <Tag color="default">入口型发现</Tag>
  }

  const style: React.CSSProperties = options?.maxWidth
    ? { maxWidth: options.maxWidth, display: 'inline-block', verticalAlign: 'middle' }
    : {}

  // 图片 URL
  if (IMAGE_EXTS.test(value)) {
    return (
      <Image
        src={value}
        alt="finding"
        width={80}
        style={{ borderRadius: 4, cursor: 'pointer' }}
        fallback="data:image/svg+xml;base64,PHN2ZyB3aWR0aD0iODAiIGhlaWdodD0iODAiIHhtbG5zPSJodHRwOi8vd3d3LnczLm9yZy8yMDAwL3N2ZyI+PHJlY3Qgd2lkdGg9IjgwIiBoZWlnaHQ9IjgwIiBmaWxsPSIjZjBmMGYwIi8+PHRleHQgeD0iNDAiIHk9IjQwIiB0ZXh0LWFuY2hvcj0ibWlkZGxlIiBkeT0iLjNlbSIgZmlsbD0iI2JmYmZiZiIgZm9udC1zaXplPSIxMiI+5Zu+54mHPC90ZXh0Pjwvc3ZnPg=="
      />
    )
  }

  // 网页链接
  if (URL_PATTERN.test(value)) {
    return (
      <Tooltip title={value}>
        <a href={value} target="_blank" rel="noopener noreferrer" style={{ ...style, wordBreak: 'break-all' }}>
          {value.length > 40 ? value.slice(0, 40) + '…' : value}
        </a>
      </Tooltip>
    )
  }

  // 邮箱
  if (EMAIL_PATTERN.test(value)) {
    return renderCopyable(
      <a href={`mailto:${value}`}>{value}</a>,
      value,
      options?.copyable
    )
  }

  // 电话号码
  if (PHONE_PATTERN.test(value) && value.replace(/[\s\-+()]/g, '').length >= 5) {
    return renderCopyable(
      <a href={`tel:${value}`}>{value}</a>,
      value,
      options?.copyable
    )
  }

  // 普通文本
  return renderCopyable(
    <span style={style}>{value}</span>,
    value,
    options?.copyable
  )
}

function renderCopyable(
  node: React.ReactNode,
  text: string,
  copyable?: boolean
): React.ReactNode {
  if (!copyable) return node
  return (
    <span style={{ display: 'inline-flex', alignItems: 'center', gap: 4 }}>
      {node}
      <CopyOutlined
        style={{ color: '#999', cursor: 'pointer', fontSize: 12 }}
        onClick={(e) => {
          e.stopPropagation()
          navigator.clipboard.writeText(text)
        }}
      />
    </span>
  )
}
