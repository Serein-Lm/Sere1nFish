import { useCallback, useEffect, useState } from 'react'
import { Alert, App, Button, Card, Drawer, Input, Select, Space, Table, Tag, Typography } from 'antd'
import { getIndustryOrganizations, getPersonaCoverage, startPersonaCoverage, type CoverageReport, type IndustryCoverage, type OrganizationFact } from '../../services/personaCoverageService'
import SourceVersionPreview from '../../components/SourceVersionPreview'
import { formatBeijingTimestamp as time } from '../../utils/dateTime'

const statusNames: Record<string, string> = { queued: '已排队', running: '联网研究中', retry: '等待自动重试', needs_sources: '等待新来源', completed: '已达标' }

export default function IndustryCoveragePanel({ onChanged }: { onChanged: () => void }) {
  const { message } = App.useApp()
  const [report, setReport] = useState<CoverageReport | null>(null)
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(false)
  const [starting, setStarting] = useState(false)
  const [sector, setSector] = useState('')
  const [query, setQuery] = useState('')
  const [open, setOpen] = useState(false)
  const [selected, setSelected] = useState<IndustryCoverage | null>(null)
  const [facts, setFacts] = useState<OrganizationFact[]>([])
  const [total, setTotal] = useState(0)
  const [page, setPage] = useState(1)
  const [factsLoading, setFactsLoading] = useState(false)
  const [factsError, setFactsError] = useState('')
  const [preview, setPreview] = useState<OrganizationFact | null>(null)
  const refresh = useCallback(async () => {
    setLoading(true)
    try { setReport(await getPersonaCoverage()); setError('') }
    catch (err) { setError(String(err)) }
    finally { setLoading(false) }
  }, [])
  useEffect(() => { void refresh(); const timer = window.setInterval(() => void refresh(), 30000); return () => window.clearInterval(timer) }, [refresh])
  useEffect(() => {
    let active = true
    setFacts([]); setFactsError('')
    if (!selected) return
    setFactsLoading(true)
    getIndustryOrganizations(selected.code, page).then((result) => { if (active) { setFacts(result.items); setTotal(result.total) } })
      .catch((err) => { if (active) setFactsError(String(err)) }).finally(() => { if (active) setFactsLoading(false) })
    return () => { active = false }
  }, [selected, page, report])
  const start = async (codes: string[] = []) => {
    setStarting(true)
    try {
      const result = await startPersonaCoverage(codes)
      message.success(`已安排 ${result.queued_industries} 个行业自动研究，无需填写背景资料`)
      await refresh(); onChanged()
    } catch (err) { message.error(String(err)) }
    finally { setStarting(false) }
  }
  const summary = report?.summary
  return <Card style={{ marginBottom: 20 }} title="行业覆盖与自动补采" extra={<Button onClick={() => setOpen(true)}>查看全部行业</Button>}>
    {error && <Alert type="error" title={error} />}
    <Space orientation="vertical" size={14} style={{ width: '100%' }}>
      <Space wrap><Tag color="blue">人设 {summary?.person_count || 0}</Tag><Tag>门类 {summary?.sector_count || 0} / 20</Tag><Tag>细分行业 {summary?.division_count || 0} / 97</Tag><Tag color="green">资料达标 {summary?.complete_count || 0} / 97</Tag><Tag>公开机构 {summary?.organization_count || 0}</Tag><Tag>办公电话证据 {summary?.phone_count || 0}</Tag></Space>
      <Typography.Text type="secondary">自动上网研究行业、岗位、机构及公开办公联系方式；人物身份保持虚构，真实机构资料单独保留来源证据。</Typography.Text>
      <Space wrap><Button type="primary" loading={starting} onClick={() => void start()}>自动补齐全部行业</Button><Button loading={loading} onClick={() => { void refresh(); onChanged() }}>刷新覆盖</Button><Typography.Text>研究中 {summary?.running || 0} · 排队 {summary?.queued || 0}</Typography.Text></Space>
      {!!summary?.unclassified_count && <Typography.Text type="secondary">{summary.unclassified_count} 条历史人设尚未明确归入细分行业，保留原资料，不重复计入大类覆盖。</Typography.Text>}
    </Space>
    <Drawer open={open} onClose={() => setOpen(false)} title="全部行业覆盖" size={1240} rootClassName="target-records-drawer">
      <Space wrap style={{ marginBottom: 16 }}><Select aria-label="行业门类" value={sector} onChange={setSector} style={{ width: 300 }} options={[{ value: '', label: '全部 20 个门类' }, ...(report?.sectors || []).map((item) => ({ value: item.code, label: item.name }))]} /><Input.Search allowClear placeholder="搜索行业" onSearch={setQuery} style={{ width: 250 }} /><a href={report?.source_url} target="_blank" rel="noopener noreferrer">国家统计局分类依据</a></Space>
      <Table<IndustryCoverage> rowKey="code" dataSource={(report?.items || []).filter((item) => (!sector || item.sector_code === sector) && (!query || item.name.includes(query) || item.code.includes(query)))} loading={loading} tableLayout="fixed" scroll={{ x: 1080 }} pagination={{ pageSize: 20 }} columns={[
        { title: '行业大类', key: 'industry', width: 240, render: (_, row) => <>{row.code} · {row.name}</> },
        { title: '完整人设', key: 'people', width: 90, render: (_, row) => `${row.person_count} / ${row.minimum_personas}` },
        { title: '机构 / 电话 / 来源', key: 'sources', width: 145, render: (_, row) => <Button type="link" onClick={() => { setSelected(row); setPage(1) }}>{row.organization_count} / {row.phone_count} / {row.source_count}</Button> },
        { title: '覆盖与缺口', key: 'gaps', width: 240, render: (_, row) => <Space orientation="vertical" size={0}><Tag color={row.complete ? 'green' : 'default'}>{row.complete ? '资料达标' : row.job ? statusNames[row.job.status] || row.job.status : '待补采'}</Tag><Typography.Text type="secondary">{row.gaps.join('；')}</Typography.Text></Space> },
        { title: '最近进度（北京时间）', key: 'progress', width: 235, render: (_, row) => <Space orientation="vertical" size={0}><Typography.Text>{time(row.job?.updated_at)}</Typography.Text><Typography.Text type="secondary">{row.job?.message}</Typography.Text>{row.job?.errors?.map((item, i) => <Typography.Text type="secondary" key={i}>{item}</Typography.Text>)}</Space> },
        { title: '操作', key: 'actions', width: 90, render: (_, row) => <Button loading={starting} disabled={row.job?.status === 'running'} onClick={() => void start([row.code])}>补采</Button> },
      ]} />
    </Drawer>
    <Drawer open={!!selected} onClose={() => setSelected(null)} title={`${selected?.name || ''} · 公开机构资料`} size={1060} rootClassName="target-records-drawer">
      <Alert type="info" title="以下为网上核验的真实机构资料，仅作为行业背景参考；不代表虚构人物与机构存在雇佣关系。" style={{ marginBottom: 16 }} />
      {factsError && <Alert type="error" title={factsError} />}
      <Table<OrganizationFact> rowKey="fact_id" dataSource={facts} loading={factsLoading} scroll={{ x: 950 }} pagination={{ current: page, total, pageSize: 20, showSizeChanger: false, onChange: setPage }} columns={[
        { title: '机构', dataIndex: 'organization_name', width: 235 }, { title: '公开办公电话', dataIndex: 'office_phone', width: 160, render: (value) => value || '尚未查证' },
        { title: '原文证据', dataIndex: 'excerpt', width: 330 }, { title: '采集时间', dataIndex: 'captured_at', width: 185, render: (value) => time(value) },
        { title: '来源', key: 'source', width: 100, render: (_, row) => <Button type="link" onClick={() => setPreview(row)}>归档原文</Button> },
      ]} />
    </Drawer>
    <SourceVersionPreview documentId={preview?.source_document_id || ''} versionId={preview?.source_document_version_id} onClose={() => setPreview(null)} />
  </Card>
}
