import { lazy, Suspense, useEffect, useRef, useState } from 'react'
import { Alert, Button, Card, Drawer, Empty, Input, Select, Space, Spin, Table, Tag, Typography } from 'antd'
import type { ColumnsType } from 'antd/es/table'
import { ReloadOutlined } from '@ant-design/icons'
import { Link, useSearchParams } from 'react-router-dom'
import { listProjectGroups, type Project, type ProjectGroup } from '../../services/projectService'
import { listProjectTargetBranch } from '../../services/sourceDocumentService'
import { loadTargetListProjects, loadTargetListRows, type TargetListRow } from '../../services/targetListService'
import { listRecords, type CollectRecord } from '../../services/mobileCollectService'
import { formatBeijingTimestamp } from '../../utils/dateTime'
import './TargetList.css'

const CollectRecordsView = lazy(() => import('../../components/CollectRecordsView/CollectRecordsView'))
const { Text, Title } = Typography

function timeLabel(value?: string) {
  return formatBeijingTimestamp(value, '尚未设置')
}

function IncrementalTime({ row }: { row: TargetListRow }) {
  const cursors = Object.values(row.mobile_incremental_cursors || {})
    .map((item) => item.through_at).sort()
  const through = cursors.at(-1)
  return <Space orientation="vertical" size={0}>
    <Text type="secondary">{through ? '最近成功增量' : '增量时间起点'}</Text>
    <span>{timeLabel(through || row.mobile_incremental_baseline?.since)}</span>
  </Space>
}

export default function TargetList() {
  const [params, setParams] = useSearchParams()
  const [groups, setGroups] = useState<ProjectGroup[]>([])
  const [groupId, setGroupId] = useState<string | undefined>(params.get('group_id') || undefined)
  const [projectId, setProjectId] = useState(params.get('project_id') || '')
  const [projects, setProjects] = useState<Project[]>([])
  const [rows, setRows] = useState<TargetListRow[]>([])
  const [branches, setBranches] = useState<Record<string, TargetListRow[]>>({})
  const [branchLoading, setBranchLoading] = useState<Record<string, boolean>>({})
  const [query, setQuery] = useState('')
  const [revision, setRevision] = useState(0)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [selected, setSelected] = useState<TargetListRow | null>(null)
  const [records, setRecords] = useState<CollectRecord[]>([])
  const [recordsLoading, setRecordsLoading] = useState(false)
  const [recordError, setRecordError] = useState('')
  const recordRequest = useRef(0)

  useEffect(() => {
    let active = true
    listProjectGroups().then((result) => {
      if (!active) return
      const sorted = [...result].sort((a, b) => a.sort_order - b.sort_order)
      setGroups(sorted)
      setGroupId((current) => current || sorted[0]?.group_id || '')
    }).catch((err) => { if (active) { setError(String(err)); setLoading(false) } })
    return () => { active = false }
  }, [])

  useEffect(() => {
    if (groupId === undefined) return
    let active = true
    setLoading(true)
    setError('')
    setRows([])
    setBranches({})
    loadTargetListProjects(groupId || undefined).then(async (result) => {
      if (!active) return
      setProjects(result)
      const chosen = projectId ? result.filter((project) => project.id === projectId) : result
      const data = await loadTargetListRows(chosen, query)
      if (active) { setRows(data.rows); setError(data.errors.join('；')) }
    }).catch((err) => { if (active) setError(String(err)) })
      .finally(() => { if (active) setLoading(false) })
    return () => { active = false }
  }, [groupId, projectId, query, revision])

  const changeScope = (group: string, project: string) => {
    setGroupId(group)
    setProjectId(project)
    setParams({ ...(group ? { group_id: group } : {}), ...(project ? { project_id: project } : {}) }, { replace: true })
  }

  const expand = async (row: TargetListRow) => {
    const key = row.project_target_id
    if (branches[key] || branchLoading[key]) return
    setBranchLoading((current) => ({ ...current, [key]: true }))
    try {
      const result = await listProjectTargetBranch(row.project_id, row.target_id)
      setBranches((current) => ({ ...current, [key]: result.items
        .filter((item) => item.target_id !== row.target_id)
        .map((item) => ({ ...item, project_name: row.project_name })) }))
    } catch (err) { setError(String(err)) }
    finally { setBranchLoading((current) => ({ ...current, [key]: false })) }
  }

  const openRecords = async (row: TargetListRow) => {
    const request = ++recordRequest.current
    setSelected(row)
    setRecords([])
    setRecordError('')
    setRecordsLoading(true)
    try {
      const result = await listRecords({ project_id: row.project_id, target_id: row.target_id, sort_by: 'time_desc', limit: 200 })
      if (request === recordRequest.current) setRecords(result.items)
    } catch (err) { if (request === recordRequest.current) setRecordError(String(err)) }
    finally { if (request === recordRequest.current) setRecordsLoading(false) }
  }

  const columns: ColumnsType<TargetListRow> = [
    { title: 'Target 单位', key: 'target', width: 290, render: (_, row) => <Space orientation="vertical" size={2}>
      <strong>{row.display_name || row.target_name}</strong>
      {row.display_name && row.display_name !== row.target_name && <Text type="secondary">{row.target_name}</Text>}
      {(row.hierarchy_parent_target_name || row.parent_target_name) && <Text type="secondary">上级：{row.hierarchy_parent_target_name || row.parent_target_name}</Text>}
      <Space size={4} wrap>{(row.batch_tags || []).map((tag) => <Tag key={tag}>{tag}</Tag>)}</Space>
    </Space> },
    { title: '所属项目', dataIndex: 'project_name', width: 230, render: (name, row) => <Link to={`/projects/${row.project_id}`}>{name}</Link> },
    { title: '公众号', key: 'wechat', width: 90, render: (_, row) => <Button type="link" onClick={() => void openRecords(row)}>{row.wechat_count || 0}</Button> },
    { title: '网站', dataIndex: 'website_count', width: 80 },
    { title: '招投标', dataIndex: 'bidding_count', width: 85 },
    { title: '手机增量时间（北京时间）', key: 'incremental', width: 210, render: (_, row) => <IncrementalTime row={row} /> },
  ]

  return <div className="target-list-page">
    <div className="target-list-heading"><div><Title level={3}>Target 列表</Title>
      <Text type="secondary">按项目查看单位、历史资料与手机增量时间；展开单位可查看已关联下级。</Text></div>
      <Button icon={<ReloadOutlined />} loading={loading} onClick={() => setRevision((value) => value + 1)}>刷新</Button>
    </div>
    <Card>
      <div className="target-list-filters">
        <Select aria-label="项目分组" value={groupId} onChange={(value) => changeScope(value, '')}
          options={[{ value: '', label: '全部分组' }, ...groups.map((group) => ({ value: group.group_id, label: group.name }))]} />
        <Select aria-label="项目" value={projectId} showSearch optionFilterProp="label" onChange={(value) => changeScope(groupId || '', value)}
          options={[{ value: '', label: '本组全部项目' }, ...projects.map((project) => ({ value: project.id, label: project.name }))]} />
        <Input.Search allowClear placeholder="搜索单位或别名" aria-label="搜索 Target" onSearch={setQuery} />
      </div>
      {error && <Alert type="error" showIcon title="部分数据加载失败" description={error} />}
      <Table rowKey="project_target_id" columns={columns} dataSource={rows} loading={loading} scroll={{ x: 1050 }}
        locale={{ emptyText: <Empty description="当前范围没有匹配的 Target" /> }}
        pagination={{ defaultPageSize: 20, showSizeChanger: true, showTotal: (total) => `共 ${total} 个${query ? '匹配单位' : '主单位'}` }}
        expandable={{ rowExpandable: (row) => Boolean(row.descendant_count || row.child_count),
          onExpand: (expanded, row) => { if (expanded) void expand(row) },
          expandedRowRender: (row) => <Table rowKey="project_target_id" size="small" columns={columns}
            dataSource={branches[row.project_target_id] || []} loading={branchLoading[row.project_target_id]} pagination={false} scroll={{ x: 1050 }} />,
        }} />
    </Card>
    <Drawer open={!!selected} onClose={() => { recordRequest.current += 1; setSelected(null) }} size={1120} rootClassName="target-records-drawer"
      title={`${selected?.target_name || ''} · 手机采集记录`}>
      <Text type="secondary">按发布时间展示最近 200 条已有记录，历史记录继续保留。</Text>
      {recordError && <Alert type="error" title={recordError} />}
      <Suspense fallback={<Spin />}><CollectRecordsView records={records} loading={recordsLoading} showBrowserArchive groupBySource /></Suspense>
    </Drawer>
  </div>
}
