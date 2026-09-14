import { useEffect, useState } from 'react'
import { Alert, Breadcrumb, Button, Card, Select, Skeleton, Space, Tag, Typography } from 'antd'
import { ArrowLeftOutlined, HistoryOutlined, ReloadOutlined } from '@ant-design/icons'
import { Link, useLocation, useNavigate, useParams, useSearchParams } from 'react-router-dom'
import ProjectDetail from '../ProjectDetail/ProjectDetail'
import { loadTargetProjectContext, type TargetProjectContext } from '../../services/targetDetailService'
import { targetListReturnPath, projectTargetPath, projectTargetTab, targetLibraryPath } from '../../utils/targetRoutes'
import './TargetDetail.css'

export default function ProjectTargetDetail() {
  const location = useLocation()
  const returnPath = targetListReturnPath(location.state)
  const { projectId = '', targetId = '' } = useParams()
  const [params] = useSearchParams()
  const navigate = useNavigate()
  const [context, setContext] = useState<TargetProjectContext | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [revision, setRevision] = useState(0)
  const tab = projectTargetTab(params.get('tab'))
  useEffect(() => {
    let active = true
    setLoading(true); setError(''); setContext(null)
    void loadTargetProjectContext(projectId, targetId)
      .then(result => { if (active) setContext(result) })
      .catch(err => { if (active) setError(err instanceof Error ? err.message : 'Target 加载失败') })
      .finally(() => { if (active) setLoading(false) })
    return () => { active = false }
  }, [projectId, targetId, revision])
  const target = context?.dashboard?.target
  return <div className="target-detail-page">
    <Breadcrumb items={[
      { title: <Link to={returnPath}>Target 目标库</Link> },
      { title: <Link to={`/projects/${encodeURIComponent(projectId)}`}>{context?.project.name || '所属项目'}</Link> },
      { title: target?.target_name || context?.library.target_name || 'Target 详情' },
    ]} />
    <div className="target-detail-heading">
      <div><Typography.Title level={2}>{target?.target_name || context?.library.target_name || 'Target 详情'}</Typography.Title>
        <Space wrap><Tag color="blue">项目内资料</Tag><Typography.Text type="secondary">{context?.project.name}</Typography.Text>{target?.root_domain && <Typography.Text>{target.root_domain}</Typography.Text>}</Space>
      </div>
      <Space wrap>
        <Link to={returnPath}><Button icon={<ArrowLeftOutlined />}>目标库</Button></Link>
        <Link to={`/projects/${encodeURIComponent(projectId)}`}><Button>返回项目</Button></Link>
        <Link state={location.state} to={targetLibraryPath(targetId)}><Button icon={<HistoryOutlined />}>全部历史</Button></Link>
      </Space>
    </div>
    {context && context.library.projects.length > 1 && <Space wrap className="target-detail-project-picker">
      <Typography.Text>所属项目</Typography.Text>
      <Select aria-label="切换 Target 所属项目" value={projectId} popupMatchSelectWidth={false} options={context.library.projects.map(p => ({ value: p.project_id, label: `${p.project_name}${p.active ? '' : '（历史关联）'}` }))}
        onChange={id => navigate(projectTargetPath(id, context.library.target_id, tab), { state: location.state })} />
    </Space>}
    {!!context && context.identities.length > 1 && <Space wrap className="target-detail-project-picker">
      <Typography.Text>项目内身份</Typography.Text>
      <Select aria-label="切换项目内 Target 身份" value={target?.target_id} options={context.identities.map(item => ({ value: item.target_id, label: `${item.target_name} · ${item.target_id}` }))}
        onChange={id => navigate(projectTargetPath(projectId, id, tab), { state: location.state })} />
    </Space>}
    {loading ? <Card><Skeleton active /></Card> : error ? <Alert type="error" showIcon title="Target 详情加载失败" description={error}
      action={<Button icon={<ReloadOutlined />} onClick={() => setRevision(v => v + 1)}>重试</Button>} />
      : context?.dashboard ? <ProjectDetail key={`${projectId}:${context.dashboard.target.target_id}`} targetView={{ project: context.project, dashboard: context.dashboard, tab,
        onTabChange: next => navigate(projectTargetPath(projectId, targetId, next), { state: location.state }),
      }} /> : <Alert type="info" showIcon title="该项目关联已停用" description="历史资料仍保留在目标库，可以通过“全部历史”查看，或切换到其他所属项目。" />}
  </div>
}
