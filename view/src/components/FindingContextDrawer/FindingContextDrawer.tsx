import { useEffect, useMemo, useRef, useState } from 'react'
import {
  Alert,
  Button,
  Col,
  Descriptions,
  Drawer,
  Empty,
  Progress,
  Row,
  Skeleton,
  Space,
  Tag,
  Timeline,
  Tooltip,
  Typography,
} from 'antd'
import {
  CheckCircleOutlined,
  ClockCircleOutlined,
  EyeOutlined,
  LinkOutlined,
  ReloadOutlined,
} from '@ant-design/icons'

import {
  getFindingContext,
  organizeFindingContext,
  type FindingContext,
  type FindingContextNarrative,
  type FindingContextStatus,
} from '../../services/taskService'
import AuthenticatedImage from '../AuthenticatedImage'
import './FindingContextDrawer.css'

const { Paragraph, Text, Title } = Typography

const STATUS_VIEW: Record<FindingContextStatus, { label: string; color: string }> = {
  pending: { label: '等待整理', color: 'default' },
  running: { label: '正在整理', color: 'processing' },
  completed: { label: '整理完成', color: 'success' },
  error: { label: '整理失败', color: 'error' },
}

interface FindingContextDrawerProps {
  open: boolean
  findingId: string
  title?: string
  onClose: () => void
}

function EvidenceRefs({ values }: { values?: string[] }) {
  if (!values?.length) return null
  return (
    <Text type="secondary" className="finding-context-evidence-refs">
      证据：{values.join(' · ')}
    </Text>
  )
}

function Narrative({ value, fallback = '-' }: { value?: FindingContextNarrative; fallback?: string }) {
  if (!value?.text) return <Text type="secondary">{fallback}</Text>
  return (
    <div className="finding-context-narrative">
      <Text>{value.text}</Text>
      <Space size={4} wrap>
        <Tag color={value.kind === 'fact' ? 'green' : 'gold'}>
          {value.kind === 'fact' ? '事实' : '推断'}
        </Tag>
        <Tag>{value.confidence}%</Tag>
      </Space>
      <EvidenceRefs values={value.evidence_refs} />
    </div>
  )
}

export default function FindingContextDrawer({
  open,
  findingId,
  title,
  onClose,
}: FindingContextDrawerProps) {
  const [context, setContext] = useState<FindingContext | null>(null)
  const [loading, setLoading] = useState(false)
  const [refreshing, setRefreshing] = useState(false)
  const [error, setError] = useState('')
  const requestRef = useRef(0)
  const refreshTimerRef = useRef<number | undefined>(undefined)

  useEffect(() => {
    if (!open || !findingId) {
      requestRef.current += 1
      if (refreshTimerRef.current) window.clearTimeout(refreshTimerRef.current)
      refreshTimerRef.current = undefined
      setContext(null)
      setError('')
      return
    }
    const requestId = ++requestRef.current
    let timer: number | undefined
    let stopped = false

    const load = async (initial: boolean) => {
      if (initial) setLoading(true)
      try {
        const next = await getFindingContext(findingId)
        if (stopped || requestId !== requestRef.current) return
        setContext(next)
        setError('')
        if (next.status === 'pending' || next.status === 'running') {
          timer = window.setTimeout(() => void load(false), 2500)
        }
      } catch (reason) {
        if (stopped || requestId !== requestRef.current) return
        setError(reason instanceof Error ? reason.message : '读取 Finding 上下文失败')
      } finally {
        if (!stopped && requestId === requestRef.current && initial) setLoading(false)
      }
    }
    void load(true)
    return () => {
      stopped = true
      if (timer) window.clearTimeout(timer)
      if (refreshTimerRef.current) window.clearTimeout(refreshTimerRef.current)
      refreshTimerRef.current = undefined
    }
  }, [findingId, open])

  const visualByRef = useMemo(
    () => new Map(
      (context?.result?.visual_findings || []).map((item) => [item.evidence_ref, item]),
    ),
    [context?.result?.visual_findings],
  )

  const refresh = async () => {
    if (!findingId) return
    if (refreshTimerRef.current) window.clearTimeout(refreshTimerRef.current)
    refreshTimerRef.current = undefined
    setRefreshing(true)
    try {
      const next = await organizeFindingContext(findingId, true)
      setContext(next)
      setError('')
      requestRef.current += 1
      const currentRequest = requestRef.current
      const poll = async () => {
        if (currentRequest !== requestRef.current) return
        try {
          const latest = await getFindingContext(findingId)
          if (currentRequest !== requestRef.current) return
          setContext(latest)
          if (latest.status === 'pending' || latest.status === 'running') {
            refreshTimerRef.current = window.setTimeout(() => void poll(), 2500)
          } else {
            refreshTimerRef.current = undefined
          }
        } catch (reason) {
          if (currentRequest !== requestRef.current) return
          refreshTimerRef.current = undefined
          setError(reason instanceof Error ? reason.message : '读取整理进度失败')
        }
      }
      if (next.status === 'pending' || next.status === 'running') {
        refreshTimerRef.current = window.setTimeout(() => void poll(), 1200)
      }
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : '重新整理失败')
    } finally {
      setRefreshing(false)
    }
  }

  const result = context?.result
  const statusView = context ? STATUS_VIEW[context.status] : null
  const images = context?.evidence_manifest?.images || []

  return (
    <Drawer
      className="finding-context-drawer"
      title={(
        <Space size={8}>
          <EyeOutlined />
          <span>{title || result?.title || 'Finding 上下文'}</span>
          {statusView ? <Tag color={statusView.color}>{statusView.label}</Tag> : null}
        </Space>
      )}
      open={open}
      onClose={onClose}
      size={980}
      destroyOnHidden
      extra={(
        <Space size={4}>
          {context?.source_url ? (
            <Tooltip title="打开原始来源">
              <Button
                type="text"
                icon={<LinkOutlined />}
                aria-label="打开 Finding 原始来源"
                onClick={() => window.open(context.source_url, '_blank', 'noopener,noreferrer')}
              />
            </Tooltip>
          ) : null}
          <Tooltip title="按最新证据重新整理">
            <Button
              type="text"
              icon={<ReloadOutlined spin={refreshing} />}
              disabled={!findingId || refreshing}
              aria-label="重新整理 Finding 上下文"
              onClick={() => void refresh()}
            />
          </Tooltip>
        </Space>
      )}
    >
      {loading ? <Skeleton active paragraph={{ rows: 10 }} /> : null}
      {error ? <Alert type="error" showIcon title="上下文读取失败" description={error} /> : null}
      {!loading && !error && context?.status === 'error' ? (
        <Alert
          type="error"
          showIcon
          title="Agent 整理失败"
          description={context.error || '未返回具体错误'}
          action={<Button size="small" onClick={() => void refresh()}>重试</Button>}
        />
      ) : null}
      {!loading && !error && context && ['pending', 'running'].includes(context.status) ? (
        <div className="finding-context-progress">
          <Progress percent={context.status === 'running' ? 65 : 20} status="active" showInfo={false} />
          <Space size={8}>
            <ClockCircleOutlined />
            <Text>{context.status === 'running' ? '正在读取正文与视觉证据并整理上下文' : '已进入上下文整理队列'}</Text>
          </Space>
          <Text type="secondary">页面会自动刷新，关闭抽屉不会中断任务。</Text>
        </div>
      ) : null}
      {!loading && !error && context?.status === 'completed' && result ? (
        <div className="finding-context-content">
          <header className="finding-context-header">
            <Space size={8} wrap>
              <Tag icon={<CheckCircleOutlined />} color="success">证据化整理</Tag>
              {context.source ? <Tag>{context.source}</Tag> : null}
              {context.model ? <Tag color="blue">{context.model}</Tag> : null}
            </Space>
            <Title level={4}>{result.title || title || 'Finding 上下文'}</Title>
            <Paragraph>{result.overview?.text}</Paragraph>
          </header>

          <section className="finding-context-section">
            <Title level={5}>上下文总览</Title>
            <Descriptions
              size="small"
              bordered
              column={{ xxl: 2, xl: 2, lg: 2, md: 1, sm: 1, xs: 1 }}
            >
              <Descriptions.Item label="目标关系"><Narrative value={result.target_relationship} fallback="无法确认" /></Descriptions.Item>
              <Descriptions.Item label="Finding 解读"><Narrative value={result.finding_interpretation} /></Descriptions.Item>
              <Descriptions.Item label="来源概况"><Narrative value={result.source_overview} /></Descriptions.Item>
              <Descriptions.Item label="联系方式语境"><Narrative value={result.contact_context} /></Descriptions.Item>
              <Descriptions.Item label="业务背景"><Narrative value={result.business_background} /></Descriptions.Item>
              <Descriptions.Item label="事件背景"><Narrative value={result.event_context} /></Descriptions.Item>
            </Descriptions>
          </section>

          <Row gutter={[28, 20]}>
            <Col xs={24} lg={14}>
              <section className="finding-context-section">
                <Title level={5}>关键事实与推断</Title>
                {(result.key_facts || []).length ? (
                  <div className="finding-context-list">
                    {(result.key_facts || []).map((item, index) => (
                      <div className="finding-context-list-row" key={`${item.statement}-${index}`}>
                        <div className="finding-context-list-item">
                          <Space size={6} wrap>
                            <Tag color={item.kind === 'fact' ? 'green' : 'gold'}>{item.kind === 'fact' ? '事实' : '推断'}</Tag>
                            <Tag>{item.confidence}%</Tag>
                          </Space>
                          <Text>{item.statement}</Text>
                          <EvidenceRefs values={item.evidence_refs} />
                        </div>
                      </div>
                    ))}
                  </div>
                ) : <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="暂无可确认事实" />}
              </section>
            </Col>
            <Col xs={24} lg={10}>
              <section className="finding-context-section">
                <Title level={5}>涉及主体</Title>
                {(result.parties || []).length ? (
                  <div className="finding-context-list">
                    {(result.parties || []).map((item, index) => (
                      <div className="finding-context-list-row" key={`${item.name}-${item.role}-${index}`}>
                        <div className="finding-context-list-item">
                          <Text strong>{item.name || '未命名主体'}</Text>
                          <Text>{[item.role, item.relationship].filter(Boolean).join(' · ') || '-'}</Text>
                          <EvidenceRefs values={item.evidence_refs} />
                        </div>
                      </div>
                    ))}
                  </div>
                ) : <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="暂无明确主体" />}
              </section>
            </Col>
          </Row>

          {result.timeline?.length ? (
            <section className="finding-context-section">
              <Title level={5}>时间线</Title>
              <Timeline
                items={result.timeline.map((item) => ({
                  content: (
                    <div className="finding-context-list-item">
                      <Text strong>{item.time || '时间未明确'}</Text>
                      <Text>{item.event}</Text>
                      <EvidenceRefs values={item.evidence_refs} />
                    </div>
                  ),
                }))}
              />
            </section>
          ) : null}

          {images.length ? (
            <section className="finding-context-section">
              <Title level={5}>视觉证据</Title>
              <div className="finding-context-image-grid">
                {images.map((image) => {
                  const analysis = visualByRef.get(image.evidence_ref)
                  return (
                    <figure key={image.evidence_ref} className="finding-context-image">
                      <AuthenticatedImage
                        source={image.storage_object_id}
                        alt={analysis?.summary || image.description || 'Finding 视觉证据'}
                        width="100%"
                        height={190}
                      />
                      <figcaption>
                        <Text strong>{analysis?.summary || image.description || image.kind}</Text>
                        {(analysis?.relevance || image.relevance) ? (
                          <Text type="secondary">{analysis?.relevance || image.relevance}</Text>
                        ) : null}
                        {(analysis?.visible_text || image.visible_text) ? (
                          <Paragraph ellipsis={{ rows: 3, expandable: true, symbol: '展开文字' }}>
                            {analysis?.visible_text || image.visible_text}
                          </Paragraph>
                        ) : null}
                      </figcaption>
                    </figure>
                  )
                })}
              </div>
            </section>
          ) : null}

          <Row gutter={[28, 20]}>
            <Col xs={24} lg={12}>
              <section className="finding-context-section">
                <Title level={5}>建议阅读顺序</Title>
                {(result.reading_guide || []).length ? (
                  <div className="finding-context-list finding-context-reading-list">
                    {(result.reading_guide || []).map((item, index) => (
                      <div className="finding-context-list-row" key={`${item}-${index}`}>
                        <Text>{index + 1}. {item}</Text>
                      </div>
                    ))}
                  </div>
                ) : <Text type="secondary">暂无阅读建议</Text>}
              </section>
            </Col>
            <Col xs={24} lg={12}>
              <section className="finding-context-section">
                <Title level={5}>待确认项</Title>
                {result.uncertainties?.length ? (
                  <Alert
                    type="warning"
                    showIcon
                    title="以下内容缺少充分证据"
                    description={result.uncertainties.join('；')}
                  />
                ) : <Text type="secondary">当前没有额外待确认项。</Text>}
              </section>
            </Col>
          </Row>
        </div>
      ) : null}
      {!loading && !error && !context ? <Empty description="暂无上下文数据" /> : null}
    </Drawer>
  )
}
