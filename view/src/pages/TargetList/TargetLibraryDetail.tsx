import { lazy, Suspense, useEffect, useState } from 'react'
import { Alert, Button, Descriptions, Drawer, Pagination, Space, Spin, Table, Tabs, Tag, Typography } from 'antd'
import { Link, useLocation } from 'react-router-dom'
import { projectTargetPath, type LibraryDetailTab } from '../../utils/targetRoutes'
import type { CollectRecord } from '../../services/mobileCollectService'
import { getLibraryHistory, libraryRunLabel, type HistoryItem, type HistoryKind, type LibraryTarget, type LibraryDocument, type LibraryVersion, type LibraryRun } from '../../services/targetLibraryService'
import { formatBeijingTimestamp as time } from '../../utils/dateTime'
import SourceVersionPreview from '../../components/SourceVersionPreview'
import { compareLibraryVersion, type VersionComparison } from '../../services/targetLibraryService'

const CollectRecordsView = lazy(() => import('../../components/CollectRecordsView/CollectRecordsView'))
const { Text } = Typography
const relationLabel = (value: string) => ({ parent_organization: '主管单位', service_unit: '直属联系单位', subsidiary: '下属单位', affiliated_unit: '附属单位', controlled_entity: '受控单位', controlled_subsidiary: '控股子单位', wholly_owned_direct_investment: '直接全资', controlled_direct_investment: '直接控股', partner: '合作单位', vendor: '供应商' } as Record<string, string>)[value] || value || '已保存关系'

export default function TargetLibraryDetail({ target, tab, page, documentFilter, onNavigate }: {
  target: LibraryTarget; tab: LibraryDetailTab; page: number; documentFilter: string
  onNavigate: (tab: LibraryDetailTab, page?: number, documentId?: string) => void
}) {
  const detail = target
  const location = useLocation()
  const [items, setItems] = useState<HistoryItem[]>([])
  const [total, setTotal] = useState(0)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const [preview, setPreview] = useState({ documentId: '', versionId: '' })
  const [comparison, setComparison] = useState<VersionComparison | null>(null)
  useEffect(() => {
    let active = true
    setError(''); setItems([]); setTotal(0)
    if (tab === 'overview') { setLoading(false); return }
    setLoading(true)
    getLibraryHistory(target.target_id, tab as HistoryKind, page, documentFilter).then((result) => { if (active) { setItems(result.items); setTotal(result.total) } })
      .catch((err) => { if (active) setError(String(err)) }).finally(() => { if (active) setLoading(false) })
    return () => { active = false }
  }, [target, tab, page, documentFilter])
  const view = (documentId: string, versionId = '') => setPreview({ documentId, versionId })
  return <div className="target-library-history">
    {error && <Alert type="error" title={error} />}
    <Tabs activeKey={tab} onChange={key => onNavigate(key as LibraryDetailTab)} items={[
      { key: 'overview', label: '概览与归属' }, { key: 'documents', label: '来源资料' }, { key: 'mobile', label: '手机记录' }, { key: 'scans', label: '扫描历史' }, { key: 'versions', label: '内容版本与变化' },
    ]} />
    {tab === 'overview' && detail && <Space orientation="vertical" size={20} style={{ width: '100%' }}>
      <Descriptions column={1} bordered size="small" items={[
        { key: 'names', label: '名称与别名', children: detail.aliases.join('；') },
        { key: 'projects', label: '历史所属项目', children: <Space wrap>{detail.projects.map((p) => <Link state={location.state} key={p.project_id} to={projectTargetPath(p.project_id, target.target_id)}>{p.project_name}{p.active ? '' : '（历史关联）'}</Link>)}</Space> },
        { key: 'identity', label: '合并身份', children: <Text copyable>{detail.member_target_ids.join('、')}</Text> },
        { key: 'scans', label: '扫描时间', children: <>首次 {time(detail.first_scan_at)}<br />最近 {time(detail.last_scan_at)}<br />最近成功 {time(detail.last_success_at)}</> },
        { key: 'assets', label: '历史存储', children: `${detail.document_count} 篇来源，${detail.version_count} 个内容版本，${detail.change_count} 次内容变化，${detail.mobile_count} 条手机记录` },
      ]} />
      {detail.relation_conflict && <Alert type="warning" title="不同项目记录了多个直接上级，以下保留各条关系及来源。" />}
      <Table rowKey={(row) => `${row.project_id}:${row.source_id}:${row.parent_id}`} dataSource={detail.relationships} size="small" pagination={false} scroll={{ x: 720 }} columns={[
        { title: '直接上级', dataIndex: 'parent_name', width: 220 }, { title: '来源项目', dataIndex: 'project_name', width: 220 },
        { title: '关系', key: 'relation', render: (_, row) => <>{relationLabel(row.relation_type)}{row.ownership_percent != null && ` · ${row.ownership_percent}%`}{!row.active && <Tag>历史关系</Tag>}</> },
        { title: '证据', key: 'evidence', render: (_, row) => <Space wrap>{row.source_urls.map((url, i) => <a key={url} href={url} target="_blank" rel="noopener noreferrer">来源 {i + 1}</a>)}</Space> },
      ]} />
      {!!detail.related_units?.length && <Table rowKey={(row) => `${row.project_id}:${row.target_id}:${row.relation_type}`} dataSource={detail.related_units} size="small" scroll={{ x: 760 }} pagination={{ pageSize: 10 }} columns={[
        { title: '其他已保存关联', dataIndex: 'target_name', width: 240 }, { title: '关系', dataIndex: 'relation_type', width: 130, render: relationLabel },
        { title: '原始关系说明', dataIndex: 'summary', width: 300 }, { title: '证据', key: 'source', render: (_, row) => <Space wrap>{row.source_urls.map((url, i) => <a href={url} key={url} target="_blank" rel="noopener noreferrer">来源 {i + 1}</a>)}</Space> },
      ]} />}
      <Table rowKey={(row) => `${row.project_id}:${row.target_id}:${row.scope_key || ''}`} dataSource={detail.incremental_scopes} size="small" pagination={false} scroll={{ x: 860 }} columns={[
        { title: '手机增量范围', dataIndex: 'project_id', render: (id) => detail.projects.find((p) => p.project_id === id)?.project_name || id },
        { title: '时间起点（北京时间）', dataIndex: 'baseline_since', render: (value) => time(value) },
        { title: '已成功覆盖至（北京时间）', dataIndex: 'through_at', render: (value) => time(value, '尚未成功完成增量') },
      ]} />
    </Space>}
    {tab === 'documents' && <Table<LibraryDocument> rowKey="document_id" dataSource={items as LibraryDocument[]} loading={loading} pagination={false} scroll={{ x: 850 }} columns={[
      { title: '归档来源', key: 'title', width: 340, render: (_, row) => <Space orientation="vertical" size={0}><Button type="link" style={{ whiteSpace: 'normal', height: 'auto', textAlign: 'left', padding: 0 }} onClick={() => view(row.document_id, row.latest_version_id)}>{row.title || row.canonical_url}</Button><Text type="secondary">{row.source_type}</Text></Space> },
      { title: '首次归档', dataIndex: 'first_seen_at', width: 185, render: (value) => time(value) }, { title: '最近发现', dataIndex: 'last_seen_at', width: 185, render: (value) => time(value) },
      { title: '历史', key: 'versions', width: 100, render: (_, row) => <Button onClick={() => onNavigate('versions', 1, row.document_id)}>查看版本</Button> },
    ]} />}
    {tab === 'versions' && <><Alert type="info" title="每个版本保留当时的正文与证据；内容哈希相同的重复采集不会新增版本。" style={{ marginBottom: 16 }} />
      {documentFilter && <Button onClick={() => onNavigate('versions')}>查看本单位全部来源版本</Button>}
      <Table<LibraryVersion> rowKey="version_id" dataSource={items as LibraryVersion[]} loading={loading} pagination={false} scroll={{ x: 810 }} columns={[
        { title: '来源及版本', key: 'source', width: 350, render: (_, row) => <Space orientation="vertical" size={0}><Button type="link" onClick={() => view(row.document_id, row.version_id)} style={{ whiteSpace: 'normal', height: 'auto', textAlign: 'left', padding: 0 }}>{row.identity?.title || row.document_id}</Button><Text copyable type="secondary">{row.version_id}</Text></Space> },
        { title: '归档时间（北京时间）', dataIndex: 'captured_at', width: 200, render: (value) => time(value) },
        { title: '内容哈希', dataIndex: 'content_hash', width: 160, render: (value) => <Text copyable={{ text: value }}>{value?.slice(0, 16)}</Text> }, { title: '状态', dataIndex: 'status', width: 100 },
        { title: '变化', key: 'diff', width: 130, render: (_, row) => <Button onClick={() => { if (target) void compareLibraryVersion(target.target_id, row.version_id).then(setComparison).catch((err) => setError(String(err))) }}>对比上一版本</Button> },
      ]} /></>}
    {tab === 'scans' && <Table<LibraryRun> rowKey="task_id" dataSource={items as LibraryRun[]} loading={loading} pagination={false} scroll={{ x: 1020 }} columns={[
      { title: '扫描任务', key: 'task', width: 240, render: (_, row) => <Space orientation="vertical" size={0}><Text copyable>{row.task_id}</Text><Text type="secondary">{detail?.projects.find((p) => p.project_id === row.project_id)?.project_name || row.project_id}</Text><Tag>{row.incremental ? '增量扫描' : '历史扫描'}</Tag></Space> },
      { title: '任务开始', dataIndex: 'started_at', width: 180, render: (value) => time(value) }, { title: '任务完成', dataIndex: 'completed_at', width: 180, render: (value) => time(value) },
      { title: '本轮增量时间范围', key: 'window', width: 220, render: (_, row) => <>{Object.values(row.incremental_targets || {}).map((item, i) => <div key={i}>{time(item.since)} 至 {time(row.incremental_until)}</div>)}{!Object.keys(row.incremental_targets || {}).length && '—'}</> },
      { title: '状态', key: 'status', width: 180, render: (_, row) => <Space orientation="vertical" size={0}><Text>{libraryRunLabel(row)}</Text><Text type="secondary">{row.progress?.message}</Text></Space> },
    ]} />}
    {tab === 'mobile' && <Suspense fallback={<Spin />}><CollectRecordsView records={items as CollectRecord[]} loading={loading} showBrowserArchive groupBySource /></Suspense>}
    {tab !== 'overview' && <Pagination current={page} total={total} pageSize={20} showSizeChanger={false} showTotal={(value) => `共 ${value} 条历史记录`} onChange={next => onNavigate(tab, next, documentFilter)} style={{ marginTop: 20 }} />}
    <SourceVersionPreview documentId={preview.documentId} versionId={preview.versionId} onClose={() => view('')} />
    <Drawer open={!!comparison} onClose={() => setComparison(null)} title="正文变化对比" size={1000} rootClassName="target-records-drawer">
      {comparison && <><Alert type="info" title={comparison.message || (comparison.changed ? '绿色为新增行，红色为删除行。' : '正文相同，版本差异可能来自附件或其他来源内容。')} />
        {comparison.truncated && <Alert type="warning" title="内容较长，此处展示部分差异，完整内容请打开归档版本。" />}
        <Text type="secondary">{comparison.before_version_id} → {comparison.after_version_id}</Text>
        <pre style={{ whiteSpace: 'pre-wrap', overflowWrap: 'anywhere' }}>{comparison.lines.map((line, i) => <div key={i} style={{ color: line.startsWith('+') ? '#237804' : line.startsWith('-') ? '#a8071a' : undefined }}>{line}</div>)}</pre>
      </>}
    </Drawer>
  </div>
}
