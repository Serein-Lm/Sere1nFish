import { createContext, useCallback, useContext, useEffect, useRef, useState, type ReactNode } from 'react'
import { Badge, Button, Drawer, Empty, Space, Tag } from 'antd'
import { BellOutlined, ArrowRightOutlined, CheckOutlined, ReloadOutlined } from '@ant-design/icons'
import { useLocation, useNavigate } from 'react-router-dom'
import { getIncrementalFeed, type IncrementalFeed, type IncrementalNotice } from '../../services/incrementalNoticeService'
import { formatBeijingTimestamp } from '../../utils/dateTime'
import './IncrementalNotices.css'

interface NoticeState {
  feed: IncrementalFeed | null
  projectFeed: IncrementalFeed | null
  open: (project?: boolean) => void
  error: boolean
}

const Context = createContext<NoticeState | null>(null)

function safeRead(key: string) {
  try {
    const value = localStorage.getItem(key) || ''
    return Number.isFinite(Date.parse(value)) ? value : ''
  } catch { return '' }
}

function Counts({ feed }: { feed: IncrementalFeed }) {
  return <div className="incremental-counts">
    <span className="incremental-count-new">新增 <strong>{feed.new_count}</strong> 条</span>
    <span className="incremental-count-changed">变化 <strong>{feed.changed_count}</strong> 条</span>
  </div>
}

function NoticeItem({ item, onProject }: { item: IncrementalNotice; onProject: () => void }) {
  return <article className={`incremental-item incremental-item-${item.kind}`}>
    <div className="incremental-item-heading">
      <Tag color={item.kind === 'new' ? 'green' : 'orange'}>{item.kind === 'new' ? '新增资料' : '内容变化'}</Tag>
      <strong>{item.target_name}</strong>
    </div>
    <div className="incremental-item-title">{item.title}</div>
    {item.summary && <p className="incremental-item-summary">{item.summary}</p>}
    <div className="incremental-item-time">发现时间：{formatBeijingTimestamp(item.detected_at)}（北京时间）</div>
    <div className="incremental-item-time">文章发布时间：{item.published_label || '来源未提供'}</div>
    {item.window_since && <div className="incremental-item-time">
      本轮时间范围：{formatBeijingTimestamp(item.window_since)} 至 {formatBeijingTimestamp(item.window_until)}
    </div>}
    <Space wrap className="incremental-item-actions">
      {item.source_url && <Button size="small" type="primary" href={item.source_url} target="_blank" rel="noopener noreferrer">查看原文</Button>}
      {item.project_id && <Button size="small" onClick={onProject}>进入项目</Button>}
    </Space>
  </article>
}

export function IncrementalNoticeProvider({ children, userKey, enabled }: { children: ReactNode; userKey: string; enabled: boolean }) {
  const location = useLocation()
  const navigate = useNavigate()
  const projectId = location.pathname.match(/^\/projects\/([^/]+)/)?.[1] || ''
  const storageKey = `incremental-notices-read:${userKey}`
  const [after, setAfter] = useState(() => safeRead(storageKey))
  const [feed, setFeed] = useState<IncrementalFeed | null>(null)
  const [projectFeed, setProjectFeed] = useState<IncrementalFeed | null>(null)
  const [error, setError] = useState(false)
  const [drawer, setDrawer] = useState<'all' | 'project' | null>(null)
  const reload = useRef<() => void>(() => {})

  useEffect(() => {
    setAfter(safeRead(storageKey))
  }, [storageKey])

  useEffect(() => {
    if (!enabled) return
    let active = true
    let inFlight = false
    const load = async () => {
      if (inFlight) return
      inFlight = true
      try {
        const results = await Promise.allSettled([
          getIncrementalFeed('', after),
          projectId ? getIncrementalFeed(projectId, after) : Promise.resolve(null),
        ])
        if (!active) return
        const [globalResult, projectResult] = results
        if (globalResult.status === 'fulfilled') setFeed(globalResult.value)
        if (projectResult.status === 'fulfilled') setProjectFeed(projectResult.value)
        setError(results.some(result => result.status === 'rejected'))
      } finally { inFlight = false }
    }
    setProjectFeed(null)
    reload.current = () => { void load() }
    void load()
    const timer = window.setInterval(() => { if (!document.hidden) void load() }, 15000)
    return () => { active = false; window.clearInterval(timer) }
  }, [projectId, after, enabled])

  const markRead = useCallback(() => {
    if (!feed) return
    try { localStorage.setItem(storageKey, feed.generated_at) } catch { /* current session still acknowledges */ }
    setAfter(feed.generated_at)
    setFeed({ ...feed, unread_count: 0 })
  }, [feed, storageKey])

  const selectedFeed = drawer === 'project' && projectId ? projectFeed : feed
  if (!enabled) return children
  return <Context.Provider value={{ feed, projectFeed, error, open: (project = false) => setDrawer(project ? 'project' : 'all') }}>
    {children}
    <Drawer title={drawer === 'project' ? '本项目增量通报' : '增量通报'} open={drawer !== null} onClose={() => setDrawer(null)} size={660}>
      <div className="incremental-drawer-toolbar">
        <span>最近 7 天 · 时间均为北京时间</span>
        <Space wrap>
          <Button size="small" icon={<ReloadOutlined />} onClick={() => reload.current()}>刷新</Button>
          {drawer === 'all' && <Button size="small" icon={<CheckOutlined />} onClick={markRead}>全部标为已读</Button>}
        </Space>
      </div>
      {error && <p role="status">通报刷新失败，当前保留上次结果，请重试。</p>}
      {selectedFeed && <Counts feed={selectedFeed} />}
      {!selectedFeed?.items.length && <Empty description="暂无已确认的增量通报；等待或采集中的任务将在资料归档后通报。" />}
      {selectedFeed?.items.map(item => <NoticeItem key={item.event_id} item={item} onProject={() => { setDrawer(null); navigate(`/projects/${item.project_id}`) }} />)}
      {!!selectedFeed && selectedFeed.new_count + selectedFeed.changed_count > selectedFeed.items.length &&
        <p>这里展示最近 {selectedFeed.items.length} 条，完整资料保留在对应项目和 Target 库中。</p>}
    </Drawer>
  </Context.Provider>
}

export function IncrementalNoticeBell() {
  const notices = useContext(Context)
  if (!notices) return null
  return <Badge count={notices.feed?.unread_count || 0} overflowCount={99} offset={[-4, 2]}>
    <Button className={`incremental-bell${notices.feed?.unread_count ? ' has-unread' : ''}`} icon={<BellOutlined />} onClick={() => notices.open()} aria-label={`增量通报，${notices.feed?.unread_count || 0} 条未读`}>
      <span className="incremental-bell-label">增量通报</span>
    </Button>
  </Badge>
}

export function IncrementalProjectNotice() {
  const notices = useContext(Context)
  const { pathname } = useLocation()
  if (!notices || !/^\/(projects|targets)(\/|$)/.test(pathname)) return null
  const isProject = /^\/projects\/[^/]+/.test(pathname)
  const feed = isProject ? notices.projectFeed : notices.feed
  return <aside className={`incremental-project-notice${feed?.new_count || feed?.changed_count ? ' has-increments' : ''}`} aria-label="最近增量通报">
    <BellOutlined className="incremental-project-icon" />
    <div className="incremental-project-body">
      <strong>{isProject ? '本项目增量通报' : '增量通报'} <span>最近 7 天 · 手机采集</span></strong>
      {notices.error ? <p>通报刷新失败，打开通报可重试</p> : !feed ? <p>正在加载增量通报…</p> : feed.new_count || feed.changed_count ? <Counts feed={feed} /> : <p>暂无已确认增量，采集结果归档后将在这里突出通报</p>}
      {feed?.items[0] && <div className="incremental-project-latest">最近：{feed.items[0].target_name} · {formatBeijingTimestamp(feed.items[0].detected_at)}（北京时间）</div>}
    </div>
    <Button type={feed?.new_count || feed?.changed_count ? 'primary' : 'default'} onClick={() => notices.open(isProject)} icon={<ArrowRightOutlined />}>查看增量</Button>
  </aside>
}
