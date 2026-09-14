import { useEffect, useRef, useState, type Key } from 'react'
import { Alert, Button, Card, Input, Select, Space, Table, Tag, Typography } from 'antd'
import type { ColumnsType } from 'antd/es/table'
import { ReloadOutlined } from '@ant-design/icons'
import { Link, useSearchParams } from 'react-router-dom'
import { loadTargetListProjects } from '../../services/targetListService'
import { listTargetLibrary, libraryRunLabel, type LibraryTarget, type LibraryPage } from '../../services/targetLibraryService'
import { formatBeijingTimestamp as time } from '../../utils/dateTime'
import TargetLibraryDetail from './TargetLibraryDetail'
import './TargetList.css'

const { Text, Title } = Typography
interface TreeRow extends LibraryTarget { children?: TreeRow[]; placeholder?: boolean }
const treeNode = (row: LibraryTarget): TreeRow => ({ ...row, children: row.child_count ? [{ ...row, target_id: `${row.target_id}:loading`, target_name: '正在加载下级单位…', children: undefined, placeholder: true }] : undefined })

export default function TargetList() {
  const [params, setParams] = useSearchParams()
  const projectId = params.get('project_id') || ''
  const [projects, setProjects] = useState<Array<{ id: string; name: string }>>([])
  const [query, setQuery] = useState('')
  const [page, setPage] = useState(1)
  const [pageSize, setPageSize] = useState(25)
  const [revision, setRevision] = useState(0)
  const [data, setData] = useState<LibraryPage | null>(null)
  const [rows, setRows] = useState<TreeRow[]>([])
  const [expanded, setExpanded] = useState<Key[]>([])
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const [selected, setSelected] = useState<LibraryTarget | null>(null)
  const [detailTab, setDetailTab] = useState('overview')
  const generation = useRef(0)
  useEffect(() => { void loadTargetListProjects().then(setProjects).catch((err) => setError(String(err))) }, [])
  useEffect(() => {
    const request = ++generation.current
    setLoading(true); setError(''); setRows([]); setExpanded([])
    listTargetLibrary({ project_id: projectId, q: query, page, page_size: pageSize, refresh: revision > 0 }).then((result) => {
      if (request === generation.current) { setData(result); setRows(query ? result.items : result.items.map(treeNode)) }
    }).catch((err) => { if (request === generation.current) setError(String(err)) }).finally(() => { if (request === generation.current) setLoading(false) })
    return () => { generation.current += 1 }
  }, [projectId, query, page, pageSize, revision])
  const open = (row: LibraryTarget, tab = 'overview') => { setSelected(row); setDetailTab(tab) }
  const expand = async (row: TreeRow) => {
    if (!row.children?.some((item) => item.placeholder)) return
    const request = generation.current
    try {
      let result = await listTargetLibrary({ parent_id: row.target_id, project_id: projectId, page_size: 100 })
      const children = [...result.items]
      for (let next = 2; children.length < result.total; next += 1) {
        result = await listTargetLibrary({ parent_id: row.target_id, project_id: projectId, page_size: 100, page: next })
        children.push(...result.items)
        if (!result.items.length || request !== generation.current) return
      }
      const replace = (nodes: TreeRow[]): TreeRow[] => nodes.map((node) => node.target_id === row.target_id ? { ...node, children: children.map(treeNode) } : node.children ? { ...node, children: replace(node.children) } : node)
      if (request === generation.current) setRows(replace)
    } catch (err) { if (request === generation.current) setError(String(err)) }
  }
  const columns: ColumnsType<TreeRow> = [
    { title: '单位与归属', key: 'target', width: 340, render: (_, row) => row.placeholder ? <Text type="secondary">{row.target_name}</Text> : <div className="target-list-unit">
      <Button type="link" className="target-name-button" onClick={() => open(row)}>{row.target_name}</Button>
      {row.parent_target_name && <Text type="secondary">上级：{row.parent_target_name}</Text>}
      <Space wrap size={4}>{row.merged_count > 1 && <Tag>已归并 {row.merged_count} 个身份</Tag>}{row.relation_conflict && <Tag color="gold">多条归属记录</Tag>}</Space>
    </div> },
    { title: '历史所属项目', key: 'projects', width: 235, render: (_, row) => !row.placeholder && <Space orientation="vertical" size={2}>{row.projects.map((p) => <Link key={p.project_id} to={`/projects/${p.project_id}`}>{p.project_name}{!p.active && '（历史）'}</Link>)}</Space> },
    { title: '历史存储', key: 'assets', width: 150, render: (_, row) => !row.placeholder && <Space orientation="vertical" size={0}><Button type="link" onClick={() => open(row, 'documents')}>来源 {row.document_count} 篇</Button><Button type="link" onClick={() => open(row, 'mobile')}>手机 {row.mobile_count} 条</Button><Text type="secondary">归属招投标 {row.bidding_count}</Text></Space> },
    { title: '扫描时间（北京时间）', key: 'scans', width: 220, render: (_, row) => !row.placeholder && <Space orientation="vertical" size={0}><Text type="secondary">首次 {time(row.first_scan_at)}</Text><Text>最近 {time(row.last_scan_at)}</Text><Button type="link" onClick={() => open(row, 'scans')}>{row.scan_count} 次任务 · {libraryRunLabel(row.latest_run)}</Button></Space> },
    { title: '手机增量（北京时间）', key: 'incremental', width: 230, render: (_, row) => {
      if (row.placeholder) return null
      const cursor = row.incremental_scopes.map((s) => s.through_at).filter(Boolean).sort().at(-1)
      const baseline = row.incremental_scopes.map((s) => s.baseline_since).filter(Boolean).sort()[0]
      return <Space orientation="vertical" size={0}><Text>{libraryRunLabel(row.latest_incremental_run)}</Text><Text type="secondary">{cursor ? '最近范围已覆盖至' : '增量起点'}</Text><Text>{time(cursor || baseline, '尚无时间边界')}</Text>{row.incremental_scopes.length > 1 && <Button type="link" onClick={() => open(row)}>查看 {row.incremental_scopes.length} 个独立范围</Button>}</Space>
    } },
    { title: '内容变化', key: 'changes', width: 130, render: (_, row) => !row.placeholder && <Space orientation="vertical" size={0}><Button type="link" onClick={() => open(row, 'versions')}>{row.change_count} 次变化</Button><Text type="secondary">{row.version_count} 个版本</Text></Space> },
  ]
  return <div className="target-list-page">
    <div className="target-list-heading"><div><Title level={3}>Target 目标库</Title><Text type="secondary">汇总全部历史单位，按名称和直接归属合并；跨项目保留扫描、来源与版本历史。</Text></div><Button icon={<ReloadOutlined />} loading={loading} onClick={() => setRevision((value) => value + 1)}>刷新</Button></div>
    <Card><div className="target-list-filters target-library-filters">
      <Select aria-label="历史项目" value={projectId} showSearch optionFilterProp="label" onChange={(value) => { setPage(1); setParams(value ? { project_id: value } : {}, { replace: true }) }} options={[{ value: '', label: '全部历史项目' }, ...projects.map((p) => ({ value: p.id, label: p.name }))]} />
      <Input.Search allowClear placeholder="搜索全库单位或已确认别名" aria-label="搜索目标库" onSearch={(value) => { setPage(1); setQuery(value) }} />
    </div>
      {data && <Space wrap style={{ marginBottom: 16 }}><Tag color="blue">全库 {data.target_count} 个单位</Tag><Tag>原始身份 {data.original_target_count} 个</Tag><Text type="secondary">当前范围 {data.scoped_target_count} 个单位 · 更新于 {time(data.generated_at)}</Text></Space>}
      {error && <Alert type="error" showIcon title="目标库加载失败" description={error} />}
      <Table<TreeRow> className="target-list-table" rowKey="target_id" tableLayout="fixed" columns={columns} dataSource={rows} loading={loading} scroll={{ x: 1305 }}
        pagination={{ current: page, pageSize, total: data?.total || 0, showSizeChanger: true, pageSizeOptions: [25, 50, 100], onChange: (next, size) => { setPage(next); setPageSize(size) }, showTotal: (total) => `共 ${total} 个${query ? '匹配单位' : '主单位'}，可展开下级` }}
        expandable={{ expandedRowKeys: expanded, indentSize: 18, onExpandedRowsChange: (keys) => setExpanded([...keys]), rowExpandable: (row) => !row.placeholder, onExpand: (value, row) => { if (value) void expand(row) } }} />
    </Card>
    <TargetLibraryDetail target={selected} initialTab={detailTab} onClose={() => setSelected(null)} />
  </div>
}
