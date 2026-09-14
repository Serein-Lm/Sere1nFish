import { useEffect, useState } from 'react'
import { Alert, Button, Card, Collapse, Empty, Form, InputNumber, Modal, Select, Space, Switch, Tag, Typography, message } from 'antd'
import { SearchOutlined, ReloadOutlined } from '@ant-design/icons'
import { createTargetResearch, getTargetResearch, type PortalResearchOptions, type TargetResearchResult } from '../../services/sourceDocumentService'
import { formatBeijingTimestamp } from '../../utils/dateTime'

const categories = { business: '业务与职责', recruitment: '招聘与人才', procurement: '招标与采购', investment: '招商与合作', feedback: '反馈与服务', organization: '组织与直属关系' }
const status = { covered: '已收集', partial: '部分覆盖', not_found: '未找到', blocked: '访问受限' }
const defaults = { include_subordinates: true, include_parent: false, max_runtime_seconds: 3600, max_pages: 50, max_related_targets: 8, archive_portal: true }

export function PortalResearchDialog({ projectId, target, onClose, onStarted }: {
  projectId: string
  target: { target_id: string; target_name: string } | null
  onClose: () => void
  onStarted?: () => void
}) {
  const [form] = Form.useForm()
  const [messageApi, messageContext] = message.useMessage()
  const [busy, setBusy] = useState(false)
  useEffect(() => { if (target) form.setFieldsValue(defaults) }, [target, form])
  const start = async (dryRun = false) => {
    if (!target || busy) return
    setBusy(true)
    try {
      const values = await form.validateFields()
      const options: PortalResearchOptions = {
        include_subordinates: values.include_subordinates, include_parent: values.include_parent,
        max_runtime_seconds: values.max_runtime_seconds, max_pages: values.max_pages,
        max_tool_calls: Math.min(240, values.max_pages * 2 + 20),
        context_tokens: values.max_runtime_seconds >= 3600 ? 24000 : 16000, dry_run: dryRun,
      }
      const result = await createTargetResearch(projectId, target.target_id, {
        portal_options: options, max_related_targets: values.max_related_targets,
        scan_discovered_targets: !dryRun, rescan_root: !dryRun && values.archive_portal, force_refresh: true,
      })
      messageApi.success(result.deduplicated ? '该单位已有研究任务，可在任务页查看' : dryRun ? '门户试跑已启动，预览结果将在任务详情中显示' : '官网门户深研已启动，可在任务页查看进度')
      onStarted?.(); onClose()
    } catch (error) {
      if (typeof error === 'object' && error !== null && 'errorFields' in error) return
      messageApi.error(error instanceof Error ? error.message : '启动失败')
    }
    finally { setBusy(false) }
  }
  return <>{messageContext}<Modal title={`官网门户深研 · ${target?.target_name || ''}`} open={!!target} onCancel={onClose} width={680} destroyOnHidden footer={[
    <Button key="cancel" onClick={onClose}>取消</Button>,
    <Button key="preview" disabled={busy} onClick={() => void start(true)}>试跑预览</Button>,
    <Button key="start" type="primary" loading={busy} onClick={() => void start()}>开始深研</Button>,
  ]}>
    <p>深入阅读业务介绍、招聘、招标、招商、反馈及组织栏目，整理正文、公开联系方式和来源证据，补充 Target 业务档案。</p>
    <Alert type="info" showIcon title="友情链接不进入扩展；上下级关系只按官网明确的直接关系收集。" style={{ marginBottom: 16 }} />
    <Form form={form} layout="vertical" initialValues={defaults}>
      <Space wrap align="start" size={32}>
        <Form.Item name="include_subordinates" label="收集直属下级" valuePropName="checked"><Switch /></Form.Item>
        <Form.Item name="include_parent" label="收集一层直接上级" valuePropName="checked"><Switch /></Form.Item>
        <Form.Item name="archive_portal" label="继续深采门户文档和附件" valuePropName="checked"><Switch /></Form.Item>
      </Space>
      <Form.Item name="max_runtime_seconds" label="研究时间预算">
        <Select options={[{ value: 1800, label: '30 分钟 · 16K 上下文' }, { value: 3600, label: '60 分钟 · 24K 上下文' }, { value: 7200, label: '120 分钟 · 24K 上下文' }]} />
      </Form.Item>
      <Space wrap align="start" size={32}>
        <Form.Item name="max_pages" label="本轮页面上限" rules={[{ required: true }]}><InputNumber min={10} max={100} /></Form.Item>
        <Form.Item name="max_related_targets" label="本轮单位扩展上限" rules={[{ required: true }]}><InputNumber min={0} max={12} /></Form.Item>
      </Space>
      <Typography.Text type="secondary" style={{ display: 'block', marginTop: 8 }}>阅读记录可从检查点恢复。报告会列出未覆盖的栏目；试跑只生成预览，不写入机构档案、不扩展单位、不发送通知。</Typography.Text>
    </Form>
  </Modal></>
}

export function PortalResearchPanel({ projectId, targetId, onResearch }: { projectId: string; targetId: string; onResearch: () => void }) {
  const [report, setReport] = useState<TargetResearchResult | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const [revision, setRevision] = useState(0)
  useEffect(() => { setReport(null) }, [projectId, targetId])
  useEffect(() => {
    let active = true
    setLoading(true); setError('')
    void getTargetResearch(projectId, targetId).then(({ item }) => { if (active) setReport(item) }).catch(err => { if (active) setError(String(err)) }).finally(() => { if (active) setLoading(false) })
    return () => { active = false }
  }, [projectId, targetId, revision])
  return <Card title="官网门户深研" style={{ marginTop: 16 }}>
    <Space wrap style={{ marginBottom: 12 }}><Button size="small" icon={<ReloadOutlined />} onClick={() => setRevision(v => v + 1)} loading={loading}>更新报告</Button><Button size="small" type="primary" icon={<SearchOutlined />} onClick={onResearch}>配置深研</Button></Space>
    {error && <Alert type="error" title="报告加载失败" description={error} />}
    {!loading && !report?.portal_sections?.length ? <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="尚无门户深研报告，可配置范围并开始收集" /> : null}
    {!!report?.portal_sections?.length && <>
      <Space wrap style={{ marginBottom: 12 }}><Tag color="blue">已读 {report.portal_page_count || 0} 页</Tag><Tag>排除友链 {report.portal_excluded_link_count || 0} 条</Tag><Typography.Text type="secondary">{formatBeijingTimestamp(report.researched_at)}</Typography.Text></Space>
      {!!report.portal_archive_pending && <Alert type="warning" title={`仍有 ${report.portal_archive_pending} 条来源待补齐归档，报告保留已读证据及缺口`} style={{ marginBottom: 12 }} />}
      <Typography.Paragraph style={{ whiteSpace: 'pre-wrap' }}>{report.summary}</Typography.Paragraph>
      <Collapse defaultActiveKey={['business']} items={report.portal_sections.map(section => ({ key: section.category,
        label: <Space wrap>{categories[section.category]}<Tag color={section.status === 'covered' ? 'green' : 'default'}>{status[section.status]}</Tag></Space>,
        children: <div style={{ overflowWrap: 'anywhere' }}><p style={{ whiteSpace: 'pre-wrap' }}>{section.summary || '本轮暂无可引用的栏目正文'}</p>{section.gaps.length > 0 && <p>待补充：{section.gaps.join('；')}</p>}<Space orientation="vertical">{section.source_urls.map((url, i) => <a key={url} href={url} target="_blank" rel="noopener noreferrer">{report.sources?.find(source => source.url === url)?.title || `来源 ${i + 1}`}</a>)}</Space></div>,
      }))} />
    </>}
  </Card>
}
