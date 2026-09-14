import { useEffect, useState } from 'react'
import { Alert, Breadcrumb, Button, Card, Skeleton, Space, Typography } from 'antd'
import { ArrowLeftOutlined, ReloadOutlined } from '@ant-design/icons'
import { Link, useLocation, useParams, useSearchParams } from 'react-router-dom'
import { getLibraryTarget, type LibraryTarget } from '../../services/targetLibraryService'
import { libraryDetailTab, projectTargetPath, targetListReturnPath } from '../../utils/targetRoutes'
import TargetLibraryDetail from '../TargetList/TargetLibraryDetail'
import './TargetDetail.css'

export default function TargetLibraryPage() {
  const location = useLocation()
  const returnPath = targetListReturnPath(location.state)
  const { targetId = '' } = useParams()
  const [params, setParams] = useSearchParams()
  const [target, setTarget] = useState<LibraryTarget | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [revision, setRevision] = useState(0)
  const tab = libraryDetailTab(params.get('tab'))
  const page = Math.max(1, Number(params.get('page')) || 1)
  useEffect(() => {
    let active = true
    setLoading(true); setError(''); setTarget(null)
    void getLibraryTarget(targetId).then(result => { if (active) setTarget(result) })
      .catch(err => { if (active) setError(err instanceof Error ? err.message : '目标档案加载失败') })
      .finally(() => { if (active) setLoading(false) })
    return () => { active = false }
  }, [targetId, revision])
  return <div className="target-detail-page">
    <Breadcrumb items={[{ title: <Link to={returnPath}>Target 目标库</Link> }, { title: target?.target_name || '目标档案' }]} />
    <div className="target-detail-heading">
      <div><Typography.Title level={2}>{target?.target_name || '目标档案'}</Typography.Title><Typography.Text type="secondary">全部项目的归属、来源资料、扫描历史与内容版本</Typography.Text></div>
      <Link to={returnPath}><Button icon={<ArrowLeftOutlined />}>返回目标库</Button></Link>
    </div>
    {loading ? <Card><Skeleton active /></Card> : error ? <Alert type="error" showIcon title="目标档案加载失败" description={error}
      action={<Button icon={<ReloadOutlined />} onClick={() => setRevision(v => v + 1)}>重试</Button>} /> : target && <>
      <Card title="进入项目查看这个 Target" className="target-detail-projects">
        {target.projects.length ? <Space wrap>{target.projects.map(project => <Link key={project.project_id} state={location.state} to={projectTargetPath(project.project_id, target.target_id)}>
          <Button>{project.project_name}{!project.active && '（历史关联）'}</Button>
        </Link>)}</Space> : <Typography.Text type="secondary">暂无关联项目，已归档历史保留在下方。</Typography.Text>}
      </Card>
      <Card><TargetLibraryDetail key={target.target_id} target={target} tab={tab} page={page} documentFilter={params.get('document_id') || ''}
        onNavigate={(next, nextPage = 1, documentId = '') => {
          const search = new URLSearchParams()
          if (next !== 'overview') search.set('tab', next)
          if (nextPage > 1) search.set('page', String(nextPage))
          if (documentId) search.set('document_id', documentId)
          setParams(search, { state: location.state })
        }} /></Card>
    </>}
  </div>
}
