import { useEffect, useState } from 'react'
import { Alert, Button, Drawer, Space, Table, Typography } from 'antd'
import { getPersonVersions, type PersonaVersion as Version } from '../../services/personaService'
import { formatBeijingTimestamp as time } from '../../utils/dateTime'

export default function PersonaVersionHistory({ personId }: { personId: string }) {
  const [open, setOpen] = useState(false)
  const [rows, setRows] = useState<Version[]>([])
  const [total, setTotal] = useState(0)
  const [page, setPage] = useState(1)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  useEffect(() => {
    let active = true
    if (!open) return
    setLoading(true); setError('')
    getPersonVersions(personId, page)
      .then((value) => { if (active) { setRows(value.items); setTotal(value.total) } }).catch((err) => { if (active) setError(String(err)) }).finally(() => { if (active) setLoading(false) })
    return () => { active = false }
  }, [personId, open, page])
  return <><Button onClick={() => { setOpen(true); setPage(1) }}>查看档案版本</Button>
    <Drawer title="人设历史档案" open={open} onClose={() => setOpen(false)} size={1000} rootClassName="target-records-drawer">
      <Alert type="info" title="每个版本保留当时保存的完整档案。历史回填只包含此前仍存储的版本。" style={{ marginBottom: 16 }} />
      {error && <Alert type="error" title={error} />}
      <Table<Version> rowKey="profile_version" dataSource={rows} loading={loading} pagination={{ current: page, pageSize: 20, total, showSizeChanger: false, onChange: setPage }} expandable={{ expandedRowRender: (row) => <pre style={{ whiteSpace: 'pre-wrap', overflowWrap: 'anywhere', maxHeight: '65vh', overflowY: 'auto' }}>{JSON.stringify(row.profile, null, 2)}</pre> }} columns={[
        { title: '版本', dataIndex: 'profile_version', width: 70 }, { title: '保存时间（北京时间）', dataIndex: 'effective_at', width: 200, render: (value) => time(value) },
        { title: '当时的档案', key: 'profile', render: (_, row) => <Space orientation="vertical" size={0}><Typography.Text>{row.profile.name} · {row.profile.industry} · {row.profile.position}</Typography.Text><Typography.Text type="secondary">{row.profile.summary}</Typography.Text></Space> },
      ]} />
    </Drawer></>
}
