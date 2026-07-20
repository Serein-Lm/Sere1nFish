import { useEffect, useState } from 'react'
import Image from 'antd/es/image'
import Spin from 'antd/es/spin'
import { EyeOutlined } from '@ant-design/icons'

import { resolveStorageImage } from '../services/storageService'

interface AuthenticatedImageProps {
  source: string
  alt?: string
  width?: number | string
  height?: number | string
  preview?: boolean
  className?: string
}

export default function AuthenticatedImage({
  source,
  alt = '图片',
  width = 120,
  height = 80,
  preview = true,
  className,
}: AuthenticatedImageProps) {
  const [src, setSrc] = useState('')
  const [failed, setFailed] = useState(false)

  useEffect(() => {
    const controller = new AbortController()
    let revoke: (() => void) | undefined
    setSrc('')
    setFailed(false)
    resolveStorageImage(source, controller.signal)
      .then((resolved) => {
        if (controller.signal.aborted) {
          resolved.revoke?.()
          return
        }
        revoke = resolved.revoke
        setSrc(resolved.url)
      })
      .catch((error) => {
        if (!controller.signal.aborted) {
          console.error('读取鉴权图片失败:', error)
          setFailed(true)
        }
      })
    return () => {
      controller.abort()
      revoke?.()
    }
  }, [source])

  const frameStyle = {
    width,
    height,
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'center',
    overflow: 'hidden',
    borderRadius: 4,
    background: 'var(--ant-color-fill-quaternary, rgba(0, 0, 0, 0.04))',
    color: 'var(--ant-color-text-tertiary, rgba(0, 0, 0, 0.45))',
    fontSize: 12,
  } as const
  if (failed) {
    return <div className={`authenticated-image-placeholder ${className || ''}`} style={frameStyle}>读取失败</div>
  }
  if (!src) {
    return <div className={`authenticated-image-placeholder ${className || ''}`} style={frameStyle}><Spin size="small" /></div>
  }
  if (!preview) {
    return <img className={className} src={src} alt={alt} style={{ ...frameStyle, objectFit: 'cover' }} />
  }
  return (
    <Image
      className={className}
      src={src}
      alt={alt}
      width={width}
      height={height}
      style={{ objectFit: 'cover' }}
      preview={{ cover: <EyeOutlined /> }}
    />
  )
}
