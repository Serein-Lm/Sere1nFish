import { useEffect, useState } from 'react'
import { Alert, Button, Drawer, Space, Spin, Tag, Typography } from 'antd'
import { getSourceDocument, openAuthenticatedArtifact, type SourceDocumentDetail } from '../services/sourceDocumentService'
import { formatBeijingTimestamp } from '../utils/dateTime'
import AuthenticatedImage from './AuthenticatedImage'

export default function SourceVersionPreview({ documentId, versionId, onClose }: { documentId: string; versionId?: string; onClose: () => void }) {
  const [data, setData] = useState<SourceDocumentDetail | null>(null)
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(false)
  useEffect(() => {
    let active = true
    setData(null); setError('')
    if (documentId) {
      setLoading(true)
      getSourceDocument(documentId, undefined, versionId).then((value) => { if (active) setData(value) })
        .catch((err) => { if (active) setError(String(err)) }).finally(() => { if (active) setLoading(false) })
    }
    return () => { active = false }
  }, [documentId, versionId])
  const version = data?.version
  return <Drawer open={!!documentId} onClose={onClose} size={940} title={version?.identity?.title || data?.title || '来源归档'} rootClassName="target-records-drawer">
    {error && <Alert type="error" title={error} />}{loading && <Spin />}
    {data && <Space orientation="vertical" size={16} style={{ width: '100%' }}>
      <Space wrap><Tag>{version?.status || '未归档'}</Tag><Typography.Text>归档时间：{formatBeijingTimestamp(version?.captured_at)}</Typography.Text></Space>
      <Typography.Text copyable>{version?.version_id || '暂无内容版本'}</Typography.Text>
      <Typography.Link href={data.canonical_url} target="_blank" rel="noopener noreferrer">打开原文</Typography.Link>
      <Space wrap>{Object.entries(version?.artifacts || {}).filter(([, url]) => !!url).map(([key, url]) => <Button key={key} onClick={() => { void openAuthenticatedArtifact(url!).catch((err) => setError(String(err))) }}>{({ raw_html_url: '原始 HTML', rendered_html_url: '渲染页面', structured_url: '结构化证据' } as Record<string, string>)[key] || key}</Button>)}</Space>
      <Typography.Paragraph style={{ whiteSpace: 'pre-wrap', overflowWrap: 'anywhere' }}>{version?.content?.text || version?.content?.summary || '当前版本没有可读正文，请查看归档证据。'}</Typography.Paragraph>
      {(version?.images || []).filter((item) => item.url).map((item) => <AuthenticatedImage key={item.index} source={item.url!} alt={`归档图片 ${item.index}`} width="100%" height="auto" />)}
      {(version?.screenshots || []).map((item) => <AuthenticatedImage key={item.index} source={item.url} alt={`归档截图 ${item.index}`} width="100%" height="auto" />)}
    </Space>}
  </Drawer>
}
