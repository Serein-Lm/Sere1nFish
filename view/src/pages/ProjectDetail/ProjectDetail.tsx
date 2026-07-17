import { useEffect, useMemo, useRef, useState } from 'react'
import { useNavigate, useParams, useSearchParams } from 'react-router-dom'
import { Button, Card, Descriptions, Skeleton, Tag, Typography, Table, Empty, Space, Tooltip, Modal, Form, Input, Select, Segmented, message, Tabs, Avatar, Progress, Collapse, Spin, Statistic, Row, Col, Drawer, Checkbox, InputNumber } from 'antd'
import type { ColumnsType } from 'antd/es/table'
import { ArrowLeftOutlined, GlobalOutlined, InfoCircleOutlined, LinkOutlined, WarningOutlined, FileTextOutlined, SearchOutlined, RocketOutlined, ExclamationCircleOutlined, CheckCircleOutlined, CopyOutlined, EditOutlined, DeleteOutlined, UserOutlined, EyeOutlined, EyeInvisibleOutlined, TeamOutlined, AimOutlined, PlusOutlined, ThunderboltOutlined, SyncOutlined, ClockCircleOutlined, BarChartOutlined, DollarOutlined, MobileOutlined, PictureOutlined, RobotOutlined } from '@ant-design/icons'
import {
  getProject,
  listProjectWebTaggingRecords,
  createWebTagging,
  createCompanyWebTagging,
  updateProject,
  deleteProject,
  getProjectDashboard,
  type Project,
  type WebTaggingRecord,
  type DashboardData,
} from '../../services/projectService'
import {
  listXhsNotes,
  listXhsProfiles,
  deleteXhsProfile,
  createXhsSearchTask,
  getXhsNoteDetail,
  type XhsNote,
  type XhsProfile,
  type XhsNoteDetail,
} from '../../services/xhsService'
import {
  listDouyinSearchResults,
  listDouyinTaggedResults,
  listDouyinProfiles,
  deleteDouyinProfile,
  type DouyinSearchResult,
  type DouyinTaggedResult,
  type DouyinProfile,
} from '../../services/douyinService'
import { stringToColor } from '../../utils/colorUtils'
import { mapWebTaggingEnum } from '../../utils/webTaggingMap'
import { renderFindingValue } from '../../utils/findingValueRenderer'
import ProfileDrawer from '../../components/ProfileDrawer'
import { listTasks, createTask, getProjectStats, deleteTask, batchDeleteTasks, getFindingCopywriting, getFindingProfile } from '../../services/taskService'
import type { Task, TaskType, TaskStatus, FindingCopywriting, ProjectStatsResponse, FindingProfile } from '../../services/taskService'
import {
  fetchMobileScreenshotBlob,
  listProjectAutoChatSessions,
  listProjectMobileOperations,
  listProjectMobileProfileObservations,
  listProjectMobileProfiles,
  listProjectMobileScreenshots,
  getPool,
  type AutoChatSession,
  type ContactProfile,
  type MobileOperationLog,
  type MobileProfileObservation,
  type MobileScreenshot,
} from '../../services/mobileService'
import CopywritingRenderer from '../../components/CopywritingRenderer/CopywritingRenderer'
import {
  listRecords as listCollectRecords,
  listTaskDefs as listMobileCollectTaskDefs,
  type CollectRecord,
} from '../../services/mobileCollectService'
import CollectRecordsView, { extractContactsFromFields } from '../../components/CollectRecordsView/CollectRecordsView'
import {
  listProjectTargets,
  type ProjectTargetSummary,
} from '../../services/sourceDocumentService'
import {
  listScholarContacts,
  type ScholarContact,
} from '../../services/scholarContactService'
import { getConfigSection } from '../../services/configService'
import './ProjectDetail.css'

const { Title, Paragraph, Text } = Typography

const TASK_TUNING_DEFAULTS = {
  asset_probe_concurrency: 96,
  url_probe_concurrency: 64,
  url_scan_concurrency: 10,
  copywriting_concurrency: 6,
  xhs_search_concurrency: 3,
}

const TASK_TUNING_FORM_DEFAULTS = {
  asset_probe_concurrency: TASK_TUNING_DEFAULTS.asset_probe_concurrency,
  probe_concurrency: TASK_TUNING_DEFAULTS.asset_probe_concurrency,
  url_probe_concurrency: TASK_TUNING_DEFAULTS.url_probe_concurrency,
  url_scan_concurrency: TASK_TUNING_DEFAULTS.url_scan_concurrency,
  copywriting_concurrency: TASK_TUNING_DEFAULTS.copywriting_concurrency,
  xhs_search_concurrency: TASK_TUNING_DEFAULTS.xhs_search_concurrency,
}

function boundedTaskTuning(value: unknown, fallback: number, maximum: number): number {
  const parsed = Number(value)
  return Number.isFinite(parsed) ? Math.max(1, Math.min(Math.trunc(parsed), maximum)) : fallback
}

type TabKey = 'website' | 'xiaohongshu' | 'douyin' | 'wechat' | 'mobile' | 'scholars' | 'tasks' | 'stats'

interface WechatDeviceOption {
  deviceId: string
  model: string
  online: boolean
}

/**
 * 抖音标签中文映射
 */
const douyinTagMap: Record<string, string> = {
  potential_employee: '潜在员工',
  marketing: '营销号',
  uncertain: '不确定',
}

/**
 * 置信度中文映射
 */
const confidenceMap: Record<string, string> = {
  high: '高',
  medium: '中',
  low: '低',
}

/**
 * 攻击面类型中文映射
 */
const attackSurfaceTypeMap: Record<string, string> = {
  employee_leak: '员工泄露',
  contact_info: '联系方式',
  insider_info: '内部信息',
  credential_leak: '凭证泄露',
  org_structure: '组织架构',
  business_process: '业务流程',
  technical_info: '技术信息',
  location_info: '位置信息',
  social_relation: '社交关系',
  other: '其他',
}

function mapAttackSurfaceType(type: string): string {
  return attackSurfaceTypeMap[type] || type
}

/**
 * 解析笔记内容中的话题标签
 * 格式: #xxx[话题]# 或 #xxx#
 */
function parseHashtags(content: string): React.ReactNode[] {
  if (!content) return []
  
  // 匹配 #xxx[话题]# 或 #xxx# 格式
  const hashtagRegex = /#([^#\[\]]+)(?:\[话题\])?#/g
  const parts: React.ReactNode[] = []
  let lastIndex = 0
  let match: RegExpExecArray | null
  let keyIndex = 0
  
  while ((match = hashtagRegex.exec(content)) !== null) {
    // 添加标签前的普通文本
    if (match.index > lastIndex) {
      parts.push(content.slice(lastIndex, match.index))
    }
    
    // 添加话题标签
    const tagText = match[1]
    parts.push(
      <Tag 
        key={`hashtag-${keyIndex++}`} 
        color="magenta" 
        style={{ 
          margin: '0 2px',
          cursor: 'pointer',
          borderRadius: 12,
        }}
      >
        #{tagText}
      </Tag>
    )
    
    lastIndex = match.index + match[0].length
  }
  
  // 添加剩余的普通文本
  if (lastIndex < content.length) {
    parts.push(content.slice(lastIndex))
  }
  
  return parts.length > 0 ? parts : [content]
}

function formatDate(value: string | undefined): string {
  if (!value) return '-'
  const d = new Date(value)
  if (Number.isNaN(d.getTime())) return value
  return d.toLocaleString()
}

function getRecordStatus(rec: WebTaggingRecord) {
  const findings = rec.data?.findings ?? []
  const findingCount = findings.length
  const maxScore = findings.reduce((max, f) => Math.max(max, f.attention_score), 0) ?? 0
  const hasFindings = Boolean(rec.data?.has_findings)

  let color: 'default' | 'success' | 'warning' | 'error' = 'default'
  let text = '未发现风险'
  let icon = <CheckCircleOutlined />

  if (hasFindings) {
    if (maxScore >= 70) {
      color = 'error'
      text = '高危风险'
      icon = <ExclamationCircleOutlined />
    } else if (maxScore >= 40) {
      color = 'warning'
      text = '中危风险'
      icon = <ExclamationCircleOutlined />
    } else {
      color = 'success'
      text = '低危风险'
      icon = <InfoCircleOutlined />
    }
  }

  return { color, text, icon, findingCount, maxScore, hasFindings }
}

const taggingColumns: ColumnsType<WebTaggingRecord> = [
  {
    title: 'URL',
    dataIndex: 'url',
    key: 'url',
    ellipsis: true,
    render: (url: string) => (
      <div className="url-cell">
        <GlobalOutlined className="url-icon" />
        <Tooltip title={url}>
          <span className="url-text">{url}</span>
        </Tooltip>
      </div>
    ),
  },
  {
    title: '站点名称',
    key: 'site_name',
    width: 140,
    render: (_, rec) => rec.data?.intro?.site_name || <Text type="secondary">-</Text>,
  },
  {
    title: '主体',
    key: 'entity',
    width: 140,
    render: (_, rec) => rec.data?.intro?.entity_name || <Text type="secondary">-</Text>,
  },
  {
    title: '风险状态',
    key: 'status',
    width: 130,
    render: (_, rec) => {
      const { color, text, icon, findingCount, hasFindings } = getRecordStatus(rec)
      return (
        <Tag color={color} icon={icon} className="status-tag">
          {text}{hasFindings && ` (${findingCount})`}
        </Tag>
      )
    },
  },
  {
    title: '创建时间',
    dataIndex: 'created_at',
    key: 'created_at',
    width: 170,
    render: (val: string) => <Text type="secondary">{formatDate(val)}</Text>,
  },
]

type WebTaggingFinding = WebTaggingRecord['data']['findings'][number]

const findingsColumns = (onViewCopywriting?: (finding: WebTaggingFinding) => void): ColumnsType<WebTaggingFinding> => [
  {
    title: '标签',
    dataIndex: 'label',
    key: 'label',
    width: 120,
    fixed: 'left',
    render: (val: string) => <Text strong>{val || '未命名入口'}</Text>,
  },
  {
    title: '角色',
    key: 'role',
    width: 90,
    render: (_, f) => <Tag color="purple">{mapWebTaggingEnum('role', f.role)}</Tag>,
  },
  {
    title: '社工攻击面',
    dataIndex: 'value',
    key: 'value',
    width: 200,
    render: (val: string | null) => renderFindingValue(val, { copyable: true, maxWidth: 180 }),
  },
  {
    title: '类型',
    key: 'type',
    width: 280,
    render: (_, f) => (
      <Space size={4}>
        <Tag color="volcano">{mapWebTaggingEnum('type', f.type)}</Tag>
        <Tag color="blue">{mapWebTaggingEnum('scope', f.scope)}</Tag>
        <Tag color="cyan">{mapWebTaggingEnum('channel', f.channel)}</Tag>
      </Space>
    ),
  },
  {
    title: '上下文',
    dataIndex: 'context',
    key: 'context',
    render: (val: string) => (
      <Tooltip title={val}>
        <span className="context-cell">{val}</span>
      </Tooltip>
    ),
  },
  {
    title: '关注原因',
    dataIndex: 'attention_reason',
    key: 'attention_reason',
    width: 280,
    render: (val: string) => (
      <Tooltip title={val}>
        <span className="reason-cell">{val || '-'}</span>
      </Tooltip>
    ),
  },
  {
    title: '关注度',
    dataIndex: 'attention_score',
    key: 'attention_score',
    width: 80,
    align: 'center',
    render: (score: number) => (
      <Tag color={score >= 70 ? 'error' : score >= 40 ? 'warning' : 'processing'}>
        {score}
      </Tag>
    ),
  },
  {
    title: '来源',
    dataIndex: 'source_url',
    key: 'source_url',
    width: 60,
    align: 'center',
    render: (url: string) => (
      <a href={url} target="_blank" rel="noreferrer" className="source-link">
        <LinkOutlined />
      </a>
    ),
  },
  ...(onViewCopywriting ? [{
    title: '话术',
    key: 'copywriting',
    width: 70,
    align: 'center' as const,
    render: (_: unknown, f: WebTaggingFinding) => (
      <Button type="link" size="small" icon={<EyeOutlined />} onClick={(e) => { e.stopPropagation(); onViewCopywriting(f) }}>
        话术
      </Button>
    ),
  }] : []),
]

function ExpandedRecordContent({ record, onViewCopywriting }: { record: WebTaggingRecord; onViewCopywriting?: (finding: WebTaggingFinding) => void }) {
  const findings = record.data?.findings ?? []
  const hasFindings = Boolean(record.data?.has_findings)

  return (
    <div className="expanded-record-content">
      <div className="expanded-intro">
        <Descriptions size="small" column={{ xxl: 3, xl: 2, lg: 2, md: 1, sm: 1, xs: 1 }} bordered className="intro-descriptions">
          <Descriptions.Item label="输入 URL">{record.data?.intro?.url || '-'}</Descriptions.Item>
          <Descriptions.Item label="最终 URL">{record.data?.intro?.final_url || '-'}</Descriptions.Item>
          <Descriptions.Item label="域名">{record.data?.intro?.domain || '-'}</Descriptions.Item>
          <Descriptions.Item label="站点名称">{record.data?.intro?.site_name || '-'}</Descriptions.Item>
          <Descriptions.Item label="主体名称">{record.data?.intro?.entity_name || '-'}</Descriptions.Item>
          <Descriptions.Item label="摘要" span={2}>{record.data?.intro?.summary || '-'}</Descriptions.Item>
        </Descriptions>
      </div>

      <div className="expanded-findings-section">
        <div className="findings-section-title">
          <WarningOutlined /> Findings ({findings.length})
        </div>
        {hasFindings && findings.length > 0 ? (
          <Table
            className="findings-table"
            dataSource={findings}
            columns={findingsColumns(onViewCopywriting)}
            rowKey={(_, idx) => `finding-${idx}`}
            pagination={false}
            size="small"
          />
        ) : (
          <div className="no-findings">
            <Text type="secondary">{record.data?.no_findings_reason || '无可用发现信息'}</Text>
          </div>
        )}
      </div>
    </div>
  )
}

function MobileScreenshotImage({ screenshot, variant = 'thumb' }: { screenshot: MobileScreenshot; variant?: 'thumb' | 'preview' }) {
  const [src, setSrc] = useState<string | null>(null)
  const [failed, setFailed] = useState(false)

  useEffect(() => {
    const controller = new AbortController()
    let objectUrl: string | null = null
    setSrc(null)
    setFailed(false)

    fetchMobileScreenshotBlob(screenshot.url, controller.signal)
      .then((blob) => {
        objectUrl = URL.createObjectURL(blob)
        setSrc(objectUrl)
      })
      .catch((err) => {
        if (!controller.signal.aborted) {
          console.error('加载手机截图失败:', err)
          setFailed(true)
        }
      })

    return () => {
      controller.abort()
      if (objectUrl) URL.revokeObjectURL(objectUrl)
    }
  }, [screenshot.url])

  if (failed) {
    return <div className={`mobile-shot-placeholder ${variant}`}>截图读取失败</div>
  }
  if (!src) {
    return <div className={`mobile-shot-placeholder ${variant}`}><Spin size="small" /></div>
  }
  return <img className={`mobile-shot-image ${variant}`} src={src} alt={screenshot.screenshot_id} />
}

export default function ProjectDetail() {
  const navigate = useNavigate()
  const { projectId } = useParams<{ projectId: string }>()

  const [loading, setLoading] = useState(true)
  const [project, setProject] = useState<Project | null>(null)
  const [taggingLoading, setTaggingLoading] = useState(false)
  const [taggingRecords, setTaggingRecords] = useState<WebTaggingRecord[]>([])
  const [taggingTotal, setTaggingTotal] = useState(0)
  const [error, setError] = useState<string | null>(null)

  // Tab 状态
  const [activeTab, setActiveTab] = useState<TabKey>('website')

  // 看板状态
  const [dashboardData, setDashboardData] = useState<DashboardData | null>(null)
  const [dashboardLoading, setDashboardLoading] = useState(false)
  // 小红书状态
  const [xhsNotes, setXhsNotes] = useState<XhsNote[]>([])
  const [xhsNotesTotal, setXhsNotesTotal] = useState(0)
  const [xhsProfiles, setXhsProfiles] = useState<XhsProfile[]>([])
  const [xhsProfilesTotal, setXhsProfilesTotal] = useState(0)
  const [xhsNotesLoading, setXhsNotesLoading] = useState(false)
  const [xhsProfilesLoading, setXhsProfilesLoading] = useState(false)
  const [isXhsSearchModalOpen, setIsXhsSearchModalOpen] = useState(false)
  const [xhsSearchForm] = Form.useForm()
  const [xhsSearchSubmitting, setXhsSearchSubmitting] = useState(false)
  const [isProfileDrawerOpen, setIsProfileDrawerOpen] = useState(false)

  // 笔记详情浮窗
  const [isNoteDetailModalOpen, setIsNoteDetailModalOpen] = useState(false)
  const [noteDetailLoading, setNoteDetailLoading] = useState(false)
  const [currentNoteDetail, setCurrentNoteDetail] = useState<XhsNoteDetail | null>(null)
  const [currentNote, setCurrentNote] = useState<XhsNote | null>(null)

  // reason 弹窗
  const [isReasonModalOpen, setIsReasonModalOpen] = useState(false)
  const [currentReasonNote, setCurrentReasonNote] = useState<XhsNote | null>(null)

  // 人物画像详情弹窗
  const [isProfileDetailModalOpen, setIsProfileDetailModalOpen] = useState(false)
  const [currentProfile, setCurrentProfile] = useState<XhsProfile | null>(null)

  // finding 引用深链：从 AI 中枢跳转过来，展示该 finding 的人物画像
  const [searchParams, setSearchParams] = useSearchParams()
  const [findingProfileOpen, setFindingProfileOpen] = useState(false)
  const [findingProfileLoading, setFindingProfileLoading] = useState(false)
  const [findingProfile, setFindingProfile] = useState<FindingProfile | null>(null)

  // 抖音状态
  const [douyinSearchResults, setDouyinSearchResults] = useState<DouyinSearchResult[]>([])
  const [douyinSearchTotal, setDouyinSearchTotal] = useState(0)
  const [douyinTaggedResults, setDouyinTaggedResults] = useState<DouyinTaggedResult[]>([])
  const [douyinTaggedTotal, setDouyinTaggedTotal] = useState(0)
  const [douyinProfiles, setDouyinProfiles] = useState<DouyinProfile[]>([])
  const [douyinProfilesTotal, setDouyinProfilesTotal] = useState(0)
  const [douyinSearchLoading, setDouyinSearchLoading] = useState(false)
  const [douyinTaggedLoading, setDouyinTaggedLoading] = useState(false)
  const [douyinProfilesLoading, setDouyinProfilesLoading] = useState(false)
  const [douyinTagFilter, setDouyinTagFilter] = useState<string | undefined>(undefined)
  const [douyinTaggedStats, setDouyinTaggedStats] = useState({ total: 0, potential_employee: 0, marketing: 0, uncertain: 0 })

  // 抖音详情弹窗
  const [isDouyinProfileModalOpen, setIsDouyinProfileModalOpen] = useState(false)
  const [currentDouyinProfile, setCurrentDouyinProfile] = useState<DouyinProfile | null>(null)

  const [isTaggingModalOpen, setIsTaggingModalOpen] = useState(false)
  const [taggingForm] = Form.useForm()
  const [taggingSubmitting, setTaggingSubmitting] = useState(false)

  const [isEditModalOpen, setIsEditModalOpen] = useState(false)
  const [editForm] = Form.useForm()
  const [editSubmitting, setEditSubmitting] = useState(false)

  // 任务下发状态
  const [isTaskModalOpen, setIsTaskModalOpen] = useState(false)
  const [taskForm] = Form.useForm()
  const [taskSubmitting, setTaskSubmitting] = useState(false)
  const [taskDefaultsLoading, setTaskDefaultsLoading] = useState(false)
  const [taskTuningValues, setTaskTuningValues] = useState(TASK_TUNING_FORM_DEFAULTS)
  const [wechatDeviceOptions, setWechatDeviceOptions] = useState<WechatDeviceOption[]>([])
  const [tasks, setTasks] = useState<Task[]>([])
  const [tasksTotal, setTasksTotal] = useState(0)
  const [tasksLoading, setTasksLoading] = useState(false)
  const [projectStats, setProjectStats] = useState<ProjectStatsResponse | null>(null)
  const [projectStatsLoading, setProjectStatsLoading] = useState(false)

  // 手机操作产物
  const [mobileLoading, setMobileLoading] = useState(false)
  const [mobileProfiles, setMobileProfiles] = useState<ContactProfile[]>([])
  const [mobileProfilesTotal, setMobileProfilesTotal] = useState(0)
  const [mobileScreenshots, setMobileScreenshots] = useState<MobileScreenshot[]>([])
  const [mobileScreenshotsTotal, setMobileScreenshotsTotal] = useState(0)
  const [mobileOperations, setMobileOperations] = useState<MobileOperationLog[]>([])
  const [mobileOperationsTotal, setMobileOperationsTotal] = useState(0)
  const [mobileObservations, setMobileObservations] = useState<MobileProfileObservation[]>([])
  const [mobileObservationsTotal, setMobileObservationsTotal] = useState(0)
  const [mobileSessions, setMobileSessions] = useState<AutoChatSession[]>([])
  const [mobileSessionsTotal, setMobileSessionsTotal] = useState(0)
  const [mobilePreview, setMobilePreview] = useState<MobileScreenshot | null>(null)
  const [expandedMobileProfileIds, setExpandedMobileProfileIds] = useState<string[]>([])
  const mobileArtifactsReqRef = useRef(0)

  // 公众号采集记录(复用手机采集记录, 按项目过滤)
  const [wechatRecords, setWechatRecords] = useState<CollectRecord[]>([])
  const [wechatRecordsTotal, setWechatRecordsTotal] = useState(0)
  const [wechatLoading, setWechatLoading] = useState(false)
  const [wechatOnlyIncremental, setWechatOnlyIncremental] = useState(false)
  const [wechatTargets, setWechatTargets] = useState<ProjectTargetSummary[]>([])
  const [wechatTargetId, setWechatTargetId] = useState('')
  const [scholarContacts, setScholarContacts] = useState<ScholarContact[]>([])
  const [scholarContactsTotal, setScholarContactsTotal] = useState(0)
  const [scholarLoading, setScholarLoading] = useState(false)
  const [scholarOnlyCorresponding, setScholarOnlyCorresponding] = useState(false)
  const [scholarOnlyVerified, setScholarOnlyVerified] = useState(false)

  // 话术 Drawer 状态
  const [copywritingDrawerOpen, setCopywritingDrawerOpen] = useState(false)
  const [copywritingLoading, setCopywritingLoading] = useState(false)
  const [currentCopywriting, setCurrentCopywriting] = useState<FindingCopywriting | null>(null)
  const [currentFindingLabel, setCurrentFindingLabel] = useState('')
  const [currentFindingId, setCurrentFindingId] = useState('')

  const fetchRecords = async (pid: string, page = 1, pageSize = 10) => {
    setTaggingLoading(true)
    try {
      const res = await listProjectWebTaggingRecords(pid, { page, page_size: pageSize })
      setTaggingRecords(res.items)
      setTaggingTotal(res.total)
    } catch (e) {
      console.error(e)
    } finally {
      setTaggingLoading(false)
    }
  }

  // 加载公众号采集记录(复用手机采集框架的记录, 按 project_id 过滤)
  const fetchWechatRecords = async (pid: string, incremental?: boolean, targetId?: string) => {
    const onlyInc = incremental ?? wechatOnlyIncremental
    const selectedTargetId = targetId ?? wechatTargetId
    setWechatLoading(true)
    try {
      const [res, targetResult] = await Promise.all([
        listCollectRecords({
          project_id: pid,
          target_id: selectedTargetId || undefined,
          only_incremental: onlyInc,
          limit: 100,
        }),
        listProjectTargets(pid),
      ])
      setWechatTargets(targetResult.items)
      const sorted = [...res.items].sort((a, b) => {
        const ca = extractContactsFromFields((a.fields || {}) as Record<string, unknown>).length > 0 ? 1 : 0
        const cb = extractContactsFromFields((b.fields || {}) as Record<string, unknown>).length > 0 ? 1 : 0
        if (ca !== cb) return cb - ca
        return (b.score ?? 0) - (a.score ?? 0)
      })
      setWechatRecords(sorted)
      setWechatRecordsTotal(res.total)
    } catch (e) {
      console.error('加载公众号采集记录失败:', e)
    } finally {
      setWechatLoading(false)
    }
  }

  // 加载学者学术联系
  const fetchScholarContacts = async (
    pid: string,
    onlyCorresponding?: boolean,
    onlyVerified?: boolean,
  ) => {
    const onlyCorr = onlyCorresponding ?? scholarOnlyCorresponding
    const onlyVer = onlyVerified ?? scholarOnlyVerified
    setScholarLoading(true)
    try {
      const res = await listScholarContacts(pid, {
        page: 1,
        page_size: 200,
        only_corresponding: onlyCorr,
        only_verified: onlyVer,
      })
      setScholarContacts(res.items)
      setScholarContactsTotal(res.total)
    } catch (e) {
      console.error('加载学者学术联系失败:', e)
    } finally {
      setScholarLoading(false)
    }
  }

  // 加载任务列表
  const fetchTasks = async (pid: string, page = 1, pageSize = 10) => {
    setTasksLoading(true)
    try {
      const data = await listTasks(pid, { page, page_size: pageSize })
      setTasks(data.items)
      setTasksTotal(data.total)
    } catch (e) {
      console.error('加载任务列表失败:', e)
    } finally {
      setTasksLoading(false)
    }
  }

  // 加载看板数据
  const fetchDashboard = async (pid: string) => {
    setDashboardLoading(true)
    try {
      const data = await getProjectDashboard(pid)
      setDashboardData(data)
    } catch (e) {
      console.error('加载看板数据失败:', e)
      // API 失败时设置空数据结构，避免显示空白
      setDashboardData(null)
    } finally {
      setDashboardLoading(false)
    }
  }

  // 加载项目统计
  const fetchProjectStats = async (pid: string) => {
    setProjectStatsLoading(true)
    try {
      const data = await getProjectStats(pid)
      setProjectStats(data)
    } catch (e) {
      console.error('加载项目统计失败:', e)
    } finally {
      setProjectStatsLoading(false)
    }
  }

  const fetchMobileArtifacts = async (pid: string) => {
    const reqId = ++mobileArtifactsReqRef.current
    setMobileLoading(true)
    try {
      const [profilesRes, observationsRes, screenshotsRes, operationsRes, sessionsRes] = await Promise.all([
        listProjectMobileProfiles(pid, 100),
        listProjectMobileProfileObservations(pid, 120),
        listProjectMobileScreenshots(pid, 60),
        listProjectMobileOperations(pid, 120),
        listProjectAutoChatSessions(pid, 80),
      ])
      if (reqId !== mobileArtifactsReqRef.current) return
      setMobileProfiles(profilesRes.profiles)
      setMobileProfilesTotal(profilesRes.total)
      setMobileObservations(observationsRes.observations)
      setMobileObservationsTotal(observationsRes.total)
      setMobileScreenshots(screenshotsRes.screenshots)
      setMobileScreenshotsTotal(screenshotsRes.total)
      setMobileOperations(operationsRes.operations)
      setMobileOperationsTotal(operationsRes.total)
      setMobileSessions(sessionsRes.sessions)
      setMobileSessionsTotal(sessionsRes.total)
    } catch (e) {
      if (reqId !== mobileArtifactsReqRef.current) return
      console.error('加载手机操作产物失败:', e)
      setMobileProfiles([])
      setMobileScreenshots([])
      setMobileOperations([])
      setMobileObservations([])
      setMobileSessions([])
      setMobileProfilesTotal(0)
      setMobileScreenshotsTotal(0)
      setMobileOperationsTotal(0)
      setMobileObservationsTotal(0)
      setMobileSessionsTotal(0)
    } finally {
      if (reqId === mobileArtifactsReqRef.current) {
        setMobileLoading(false)
      }
    }
  }

  // 查看 finding 话术
  const handleViewFindingCopywriting = async (finding: WebTaggingFinding) => {
    const findingId = finding.finding_id
    if (!findingId) {
      message.warning('该信息节点缺少 finding_id')
      return
    }
    setCurrentFindingLabel(finding.label || finding.value || '话术详情')
    setCurrentFindingId(findingId)
    setCopywritingDrawerOpen(true)
    setCopywritingLoading(true)
    setCurrentCopywriting(null)
    try {
      const cw = await getFindingCopywriting(findingId)
      setCurrentCopywriting(cw)
    } catch (e) {
      message.error(e instanceof Error ? e.message : '获取话术失败')
      setCopywritingDrawerOpen(false)
    } finally {
      setCopywritingLoading(false)
    }
  }

  // 通用：通过 finding_id 查看话术（用于画像等场景）
  const handleViewCopywritingById = async (findingId: string, label?: string) => {
    if (!findingId) {
      message.warning('缺少关联的 finding_id')
      return
    }
    setCurrentFindingLabel(label || '话术详情')
    setCurrentFindingId(findingId)
    setCopywritingDrawerOpen(true)
    setCopywritingLoading(true)
    setCurrentCopywriting(null)
    try {
      const cw = await getFindingCopywriting(findingId)
      setCurrentCopywriting(cw)
    } catch (e) {
      message.error(e instanceof Error ? e.message : '获取话术失败')
      setCopywritingDrawerOpen(false)
    } finally {
      setCopywritingLoading(false)
    }
  }

  // finding 深链：读取 finding_id 查询参数，打开对应人物画像抽屉
  useEffect(() => {
    const fid = searchParams.get('finding_id')
    if (!fid) return
    // 清除 URL 参数，避免刷新时重复打开
    searchParams.delete('finding_id')
    setSearchParams(searchParams, { replace: true })

    setFindingProfileOpen(true)
    setFindingProfileLoading(true)
    setFindingProfile(null)
    getFindingProfile(fid)
      .then((profile) => setFindingProfile(profile))
      .catch((e) => {
        message.error(e instanceof Error ? e.message : '获取人物画像失败')
        setFindingProfileOpen(false)
      })
      .finally(() => setFindingProfileLoading(false))
  }, [searchParams])

  // 加载小红书笔记
  const fetchXhsNotes = async (pid: string, page = 1, pageSize = 10) => {
    setXhsNotesLoading(true)
    try {
      const res = await listXhsNotes(pid, { page, page_size: pageSize })
      setXhsNotes(res.items)
      setXhsNotesTotal(res.total)
    } catch (e) {
      console.error('Failed to load XHS notes:', e)
    } finally {
      setXhsNotesLoading(false)
    }
  }

  // 加载小红书人物画像
  const fetchXhsProfiles = async (pid: string, page = 1, pageSize = 10) => {
    setXhsProfilesLoading(true)
    try {
      const res = await listXhsProfiles(pid, { page, page_size: pageSize })
      setXhsProfiles(res.items)
      setXhsProfilesTotal(res.total)
    } catch (e) {
      console.error('Failed to load XHS profiles:', e)
    } finally {
      setXhsProfilesLoading(false)
    }
  }

  // 加载抖音搜索结果
  const fetchDouyinSearchResults = async (pid: string, page = 1, pageSize = 10) => {
    setDouyinSearchLoading(true)
    try {
      const data = await listDouyinSearchResults(pid, { page, page_size: pageSize })
      setDouyinSearchResults(data.items)
      setDouyinSearchTotal(data.total)
    } catch (e) {
      console.error('Failed to load Douyin search results:', e)
    } finally {
      setDouyinSearchLoading(false)
    }
  }

  // 加载抖音打标结果
  const fetchDouyinTaggedResults = async (pid: string, tag?: string, page = 1, pageSize = 10) => {
    setDouyinTaggedLoading(true)
    try {
      const data = await listDouyinTaggedResults(pid, { tag, page, page_size: pageSize })
      setDouyinTaggedResults(data.items)
      setDouyinTaggedTotal(data.total)
      setDouyinTaggedStats(data.stats)
    } catch (e) {
      console.error('Failed to load Douyin tagged results:', e)
    } finally {
      setDouyinTaggedLoading(false)
    }
  }

  // 加载抖音用户画像
  const fetchDouyinProfiles = async (pid: string, page = 1, pageSize = 10) => {
    setDouyinProfilesLoading(true)
    try {
      const data = await listDouyinProfiles(pid, { page, page_size: pageSize })
      setDouyinProfiles(data.items)
      setDouyinProfilesTotal(data.total)
    } catch (e) {
      console.error('Failed to load Douyin profiles:', e)
    } finally {
      setDouyinProfilesLoading(false)
    }
  }

  useEffect(() => {
    let cancelled = false

    const run = async () => {
      if (!projectId) {
        setError('缺少项目 ID')
        setLoading(false)
        return
      }

      setLoading(true)
      setError(null)
      try {
        const data = await getProject(projectId)
        if (!cancelled) {
          setProject(data)
          await fetchRecords(projectId)
          // 加载任务列表
          await fetchTasks(projectId)
          // 加载看板数据
          fetchDashboard(projectId)
          // 加载项目统计
          fetchProjectStats(projectId)
          // 加载手机操作产物
          fetchMobileArtifacts(projectId)
          // 加载公众号采集记录
          fetchWechatRecords(projectId)
          // 加载学者学术联系
          fetchScholarContacts(projectId)
          // 加载小红书数据
          await Promise.all([fetchXhsNotes(projectId), fetchXhsProfiles(projectId)])
          // 加载抖音数据
          await Promise.all([
            fetchDouyinSearchResults(projectId),
            fetchDouyinTaggedResults(projectId),
            fetchDouyinProfiles(projectId),
          ])
        }
      } catch (e) {
        const msg = e instanceof Error ? e.message : '加载失败'
        if (!cancelled) {
          setError(msg)
          setProject(null)
          setTaggingRecords([])
          setXhsNotes([])
          setXhsProfiles([])
          setDouyinSearchResults([])
          setDouyinTaggedResults([])
          setDouyinProfiles([])
          setMobileProfiles([])
          setMobileScreenshots([])
          setMobileOperations([])
          setMobileSessions([])
        }
      } finally {
        if (!cancelled) {
          setLoading(false)
        }
      }
    }

    run()
    return () => {
      cancelled = true
      mobileArtifactsReqRef.current += 1
    }
  }, [projectId])

  const handleAddTagging = () => {
    taggingForm.resetFields()
    setIsTaggingModalOpen(true)
  }

  const handleOpenTaskModal = async () => {
    setTaskDefaultsLoading(true)
    try {
      const [configResult, poolResult, collectTasksResult] = await Promise.allSettled([
        getConfigSection('collection_runtime'),
        getPool(),
        projectId ? listMobileCollectTaskDefs(projectId) : Promise.resolve({ items: [], total: 0 }),
      ])
      if (configResult.status === 'fulfilled') {
        const { config } = configResult.value
        setTaskTuningValues({
          asset_probe_concurrency: boundedTaskTuning(config.asset_probe_concurrency, TASK_TUNING_DEFAULTS.asset_probe_concurrency, 128),
          probe_concurrency: boundedTaskTuning(config.asset_probe_concurrency, TASK_TUNING_DEFAULTS.asset_probe_concurrency, 128),
          url_probe_concurrency: boundedTaskTuning(config.url_probe_concurrency, TASK_TUNING_DEFAULTS.url_probe_concurrency, 128),
          url_scan_concurrency: boundedTaskTuning(config.url_scan_concurrency, TASK_TUNING_DEFAULTS.url_scan_concurrency, 16),
          copywriting_concurrency: boundedTaskTuning(config.copywriting_concurrency, TASK_TUNING_DEFAULTS.copywriting_concurrency, 12),
          xhs_search_concurrency: boundedTaskTuning(config.xhs_search_concurrency, TASK_TUNING_DEFAULTS.xhs_search_concurrency, 8),
        })
      } else {
        setTaskTuningValues(TASK_TUNING_FORM_DEFAULTS)
      }

      const poolDevices = poolResult.status === 'fulfilled' ? poolResult.value.devices : []
      const collectTasks = collectTasksResult.status === 'fulfilled' ? collectTasksResult.value.items : []
      const options = collectTasks
        .filter((task) => task.app_name.toLowerCase().includes('微信') || task.app_name.toLowerCase().includes('wechat'))
        .map((task) => {
          const poolDevice = poolDevices.find((device) =>
            device.device_key === task.device_id || device.device_id === task.device_id,
          )
          return {
            deviceId: task.device_id,
            model: poolDevice?.model || poolDevice?.meta?.display_name || task.device_id,
            online: Boolean(poolDevice?.online),
          }
        })
        .filter((option, index, all) => all.findIndex((item) => item.deviceId === option.deviceId) === index)
      setWechatDeviceOptions(options)
    } finally {
      setTaskDefaultsLoading(false)
      setIsTaskModalOpen(true)
    }
  }

  const handleTaggingSubmit = async () => {
    if (!projectId) return
    try {
      const values = await taggingForm.validateFields()
      setTaggingSubmitting(true)
      
      if (values.type === 'url') {
        await createWebTagging({ project_id: projectId, url: values.value })
      } else {
        await createCompanyWebTagging({ project_id: projectId, company_name: values.value })
      }
      
      setIsTaggingModalOpen(false)
      message.success('任务已提交')
      await fetchRecords(projectId)
    } catch (e) {
      const msg = e instanceof Error ? e.message : '提交失败'
      message.error(msg)
    } finally {
      setTaggingSubmitting(false)
    }
  }

  const handleEdit = () => {
    if (!project) return
    editForm.setFieldsValue({
      name: project.name,
      description: project.description || '',
    })
    setIsEditModalOpen(true)
  }

  const handleEditSubmit = async () => {
    if (!projectId) return
    try {
      const values = await editForm.validateFields()
      setEditSubmitting(true)
      const updated = await updateProject(projectId, {
        name: values.name,
        description: values.description || undefined,
      })
      setProject(updated)
      setIsEditModalOpen(false)
      message.success('项目已更新')
    } catch (e) {
      const msg = e instanceof Error ? e.message : '更新失败'
      message.error(msg)
    } finally {
      setEditSubmitting(false)
    }
  }

  const handleDelete = () => {
    if (!projectId) return
    Modal.confirm({
      title: '确认删除项目',
      icon: <ExclamationCircleOutlined />,
      content: '删除后无法恢复，是否同时删除该项目下的所有 Web Tagging 记录？',
      okText: '删除',
      okType: 'danger',
      cancelText: '取消',
      onOk: async () => {
        try {
          await deleteProject(projectId)
          message.success('项目已删除')
          navigate('/projects')
        } catch (e) {
          const msg = e instanceof Error ? e.message : '删除失败'
          message.error(msg)
        }
      },
    })
  }

  const tags = useMemo(() => {
    const list: string[] = []
    if (project?.description) list.push('有描述')
    return list
  }, [project])

  // 小红书搜索任务
  const handleAddXhsSearch = () => {
    xhsSearchForm.resetFields()
    setIsXhsSearchModalOpen(true)
  }

  const handleXhsSearchSubmit = async () => {
    if (!projectId) return
    try {
      const values = await xhsSearchForm.validateFields()
      setXhsSearchSubmitting(true)
      await createXhsSearchTask({
        project_id: projectId,
        keyword: values.keyword,
        max_notes: values.max_notes || 20,
        attention_threshold: values.attention_threshold || 60,
      })
      setIsXhsSearchModalOpen(false)
      message.success('搜索任务已提交，后台正在处理')
      // 延迟刷新数据
      setTimeout(() => {
        if (projectId) {
          fetchXhsNotes(projectId)
          fetchXhsProfiles(projectId)
        }
      }, 3000)
    } catch (e) {
      const msg = e instanceof Error ? e.message : '提交失败'
      message.error(msg)
    } finally {
      setXhsSearchSubmitting(false)
    }
  }

  // 删除人物画像
  const handleDeleteProfile = async (profileId: string, e: React.MouseEvent) => {
    e.stopPropagation()
    Modal.confirm({
      title: '确认删除',
      icon: <ExclamationCircleOutlined />,
      content: '确定要删除这个人物画像吗？删除后无法恢复。',
      okText: '删除',
      okType: 'danger',
      cancelText: '取消',
      onOk: async () => {
        try {
          await deleteXhsProfile(profileId)
          message.success('人物画像已删除')
          if (projectId) {
            fetchXhsProfiles(projectId)
          }
        } catch (e) {
          const msg = e instanceof Error ? e.message : '删除失败'
          message.error(msg)
        }
      },
    })
  }

  // 删除抖音用户画像
  const handleDeleteDouyinProfile = async (profileId: string, e: React.MouseEvent) => {
    e.stopPropagation()
    Modal.confirm({
      title: '确认删除',
      icon: <ExclamationCircleOutlined />,
      content: '确定要删除这个抖音用户画像吗？删除后无法恢复。',
      okText: '删除',
      okType: 'danger',
      cancelText: '取消',
      onOk: async () => {
        try {
          await deleteDouyinProfile(profileId)
          message.success('用户画像已删除')
          if (projectId) {
            fetchDouyinProfiles(projectId)
          }
        } catch (e) {
          const msg = e instanceof Error ? e.message : '删除失败'
          message.error(msg)
        }
      },
    })
  }

  // 查看笔记详情
  const handleViewNoteDetail = async (note: XhsNote) => {
    setCurrentNote(note)
    setIsNoteDetailModalOpen(true)
    setNoteDetailLoading(true)
    setCurrentNoteDetail(null)
    
    try {
      const detail = await getXhsNoteDetail(note.note_id)
      setCurrentNoteDetail(detail)
    } catch (e) {
      const msg = e instanceof Error ? e.message : '获取详情失败'
      message.error(msg)
    } finally {
      setNoteDetailLoading(false)
    }
  }

  // 构建笔记链接
  const buildNoteUrl = (note: XhsNote) => {
    // 优先使用 xsec_token，即使 xsec_source 为空也可以跳转
    if (note.xsec_token) {
      const params = new URLSearchParams()
      params.set('xsec_token', note.xsec_token)
      if (note.xsec_source) {
        params.set('xsec_source', note.xsec_source)
      }
      return `https://www.xiaohongshu.com/explore/${note.note_id}?${params.toString()}`
    }
    return `https://www.xiaohongshu.com/explore/${note.note_id}`
  }

  // 小红书笔记表格列
  const xhsNotesColumns: ColumnsType<XhsNote> = [
    {
      title: '笔记',
      key: 'note',
      width: 260,
      render: (_, rec) => {
        const noteUrl = buildNoteUrl(rec)
        return (
          <div className="xhs-note-cell">
            {rec.cover && (
              <a href={noteUrl} target="_blank" rel="noopener noreferrer" className="xhs-note-cover-link">
                <img src={rec.cover} alt="" className="xhs-note-cover" referrerPolicy="no-referrer" />
              </a>
            )}
            <div className="xhs-note-info">
              <a href={noteUrl} target="_blank" rel="noopener noreferrer" className="xhs-note-title-link">
                <span className="xhs-note-title-text">{rec.title || '无标题'}</span>
              </a>
              {rec.liked_count && (
                <Text type="secondary" className="xhs-note-likes">❤️ {rec.liked_count}</Text>
              )}
            </div>
          </div>
        )
      },
    },
    {
      title: '作者',
      key: 'user',
      width: 120,
      render: (_, rec) => {
        const profileUrl = rec.user?.user_id 
          ? `https://www.xiaohongshu.com/user/profile/${rec.user.user_id}`
          : undefined
        return (
          <a href={profileUrl} target="_blank" rel="noopener noreferrer" className="xhs-author-link">
            <Avatar size="small" src={rec.user?.avatar} icon={<UserOutlined />} />
            <Text ellipsis className="xhs-author-name">{rec.user?.nickname || '-'}</Text>
          </a>
        )
      },
    },
    {
      title: '关键词',
      dataIndex: 'keyword',
      key: 'keyword',
      width: 100,
      render: (val: string) => val ? <Tag color="magenta">{val}</Tag> : <Text type="secondary">-</Text>,
    },
    {
      title: '关联度',
      key: 'relevance',
      width: 70,
      render: (_, rec) => (
        <Tag color={(rec.tagging?.keyword_relevance || 0) >= 70 ? 'error' : (rec.tagging?.keyword_relevance || 0) >= 50 ? 'warning' : 'processing'}>
          {rec.tagging?.keyword_relevance || 0}
        </Tag>
      ),
    },
    {
      title: '关注度',
      key: 'attention',
      width: 70,
      render: (_, rec) => (
        <Tag color={(rec.tagging?.attention_score || 0) >= 70 ? 'error' : (rec.tagging?.attention_score || 0) >= 40 ? 'warning' : 'processing'}>
          {rec.tagging?.attention_score || 0}
        </Tag>
      ),
    },
    {
      title: '公司',
      key: 'company',
      width: 100,
      render: (_, rec) => rec.tagging?.company_mentioned 
        ? <Tag color="blue">{rec.tagging.company_mentioned}</Tag> 
        : <Text type="secondary">-</Text>,
    },
    {
      title: '攻击面',
      key: 'attack_surface',
      width: 180,
      render: (_, rec) => rec.tagging?.attack_surface_types && rec.tagging.attack_surface_types.length > 0 ? (
        <div 
          className="xhs-clickable-tags"
          onClick={() => {
            Modal.info({
              title: '攻击面类型',
              content: (
                <Space size={[4, 8]} wrap style={{ marginTop: 12 }}>
                  {rec.tagging?.attack_surface_types?.map((t, i) => (
                    <Tag key={i} color="volcano">{mapAttackSurfaceType(t)}</Tag>
                  ))}
                </Space>
              ),
              okText: '关闭',
            })
          }}
        >
          <Space size={2} wrap>
            {rec.tagging.attack_surface_types.slice(0, 2).map((t, i) => (
              <Tag key={i} color="volcano" style={{ fontSize: 11 }}>{mapAttackSurfaceType(t)}</Tag>
            ))}
            {rec.tagging.attack_surface_types.length > 2 && (
              <Tag style={{ fontSize: 11 }}>+{rec.tagging.attack_surface_types.length - 2}</Tag>
            )}
          </Space>
        </div>
      ) : <Text type="secondary">-</Text>,
    },
    {
      title: '关键信息',
      key: 'key_info',
      width: 200,
      render: (_, rec) => rec.tagging?.key_info_extracted && rec.tagging.key_info_extracted.length > 0 ? (
        <div 
          className="xhs-clickable-tags"
          onClick={() => {
            Modal.info({
              title: '提取的关键信息',
              content: (
                <Space size={[4, 8]} wrap style={{ marginTop: 12 }}>
                  {rec.tagging?.key_info_extracted?.map((info, i) => (
                    <Tag key={i} color="cyan">{info}</Tag>
                  ))}
                </Space>
              ),
              okText: '关闭',
            })
          }}
        >
          <Space size={2} wrap>
            {rec.tagging.key_info_extracted.slice(0, 2).map((info, i) => (
              <Tag key={i} color="cyan" style={{ fontSize: 11 }}>{info}</Tag>
            ))}
            {rec.tagging.key_info_extracted.length > 2 && (
              <Tag style={{ fontSize: 11 }}>+{rec.tagging.key_info_extracted.length - 2}</Tag>
            )}
          </Space>
        </div>
      ) : <Text type="secondary">-</Text>,
    },
    {
      title: '分析',
      key: 'reason',
      width: 80,
      render: (_, rec) => (rec.tagging?.relevance_reason || rec.tagging?.reason) ? (
        <Button
          type="link"
          size="small"
          onClick={() => {
            setCurrentReasonNote(rec)
            setIsReasonModalOpen(true)
          }}
        >
          查看
        </Button>
      ) : <Text type="secondary">-</Text>,
    },
    {
      title: '操作',
      key: 'action',
      width: 100,
      fixed: 'right',
      render: (_, rec) => (
        <Space size={8}>
          <Tooltip title="查看原文">
            <a href={buildNoteUrl(rec)} target="_blank" rel="noopener noreferrer" className="xhs-link-btn">
              <LinkOutlined />
            </a>
          </Tooltip>
          <Tooltip title="查看详情">
            <a className="xhs-detail-btn" onClick={() => handleViewNoteDetail(rec)}>
              <EyeOutlined />
            </a>
          </Tooltip>
        </Space>
      ),
    },
  ]

  // 小红书人物画像表格列
  const xhsProfilesColumns: ColumnsType<XhsProfile> = [
    {
      title: '用户',
      key: 'user',
      width: 200,
      render: (_, rec) => {
        const profileUrl = rec.user_id 
          ? `https://www.xiaohongshu.com/user/profile/${rec.user_id}`
          : undefined
        return (
          <Space>
            <a href={profileUrl} target="_blank" rel="noopener noreferrer">
              <Avatar size="default" src={rec.avatar_url} icon={<UserOutlined />} style={{ cursor: 'pointer' }} />
            </a>
            <div>
              <div>
                <a 
                  href={profileUrl} 
                  target="_blank" 
                  rel="noopener noreferrer"
                  style={{ color: 'inherit' }}
                >
                  <Text strong>{rec.nickname || '-'}</Text>
                </a>
              </div>
              <Text type="secondary" style={{ fontSize: 12 }}>
                {rec.identity?.company || rec.identity?.position || '-'}
              </Text>
            </div>
          </Space>
        )
      },
    },
    {
      title: '关联度',
      key: 'relevance',
      width: 100,
      render: (_, rec) => (
        <Tooltip title={rec.keyword_relevance?.analysis}>
          <Progress
            percent={rec.keyword_relevance?.score || 0}
            size="small"
            strokeColor={(rec.keyword_relevance?.score || 0) >= 70 ? '#ff4d4f' : (rec.keyword_relevance?.score || 0) >= 50 ? '#faad14' : '#1890ff'}
            format={(p) => p}
          />
        </Tooltip>
      ),
    },
    {
      title: '公司',
      key: 'company',
      width: 140,
      render: (_, rec) => rec.company_identification?.identified_company ? (
        <Tooltip title={rec.company_identification.evidence?.join(', ')}>
          <Space orientation="vertical" size={0}>
            <Tag color="blue">{rec.company_identification.identified_company}</Tag>
            <Text type="secondary" style={{ fontSize: 11 }}>
              {rec.company_identification.confidence}
            </Text>
          </Space>
        </Tooltip>
      ) : <Text type="secondary">-</Text>,
    },
    {
      title: '风险',
      key: 'risk',
      width: 100,
      render: (_, rec) => {
        const riskLevel = rec.attack_surface?.risk_level
        const riskScore = rec.attack_surface?.risk_score || 0
        return (
          <Tag color={
            riskLevel === '高' || riskLevel === '极高' ? 'error' : 
            riskLevel === '中' ? 'warning' : 'success'
          }>
            {riskLevel || '-'} ({riskScore})
          </Tag>
        )
      },
    },
    {
      title: '关注度',
      key: 'attention',
      width: 90,
      render: (_, rec) => (
        <Tag color={(rec.attention_score || 0) >= 70 ? 'error' : (rec.attention_score || 0) >= 40 ? 'warning' : 'processing'}>
          {rec.attention_score || 0}
        </Tag>
      ),
    },
    {
      title: '标签',
      key: 'tags',
      width: 200,
      render: (_, rec) => rec.tags && rec.tags.length > 0 ? (
        <Space size={4} wrap>
          {rec.tags.slice(0, 3).map((tag, i) => (
            <Tag key={i} color="default">{tag}</Tag>
          ))}
          {rec.tags.length > 3 && <Tag>+{rec.tags.length - 3}</Tag>}
        </Space>
      ) : <Text type="secondary">-</Text>,
    },
    {
      title: '操作',
      key: 'action',
      width: 180,
      render: (_, rec) => (
        <Space size={4}>
          <Button
            type="link"
            size="small"
            icon={<EyeOutlined />}
            onClick={() => {
              setCurrentProfile(rec)
              setIsProfileDetailModalOpen(true)
            }}
          >
            详情
          </Button>
          {rec.finding_id && (
            <Button
              type="link"
              size="small"
              icon={<CopyOutlined />}
              onClick={(e) => { e.stopPropagation(); handleViewCopywritingById(rec.finding_id!, rec.nickname || '话术详情') }}
            >
              话术
            </Button>
          )}
          <Button
            type="text"
            danger
            size="small"
            icon={<DeleteOutlined />}
            onClick={(e) => handleDeleteProfile(rec.id, e)}
          />
        </Space>
      ),
    },
  ]

  // 抖音搜索结果表格列
  const douyinSearchColumns: ColumnsType<DouyinSearchResult> = [
    {
      title: '作品',
      key: 'aweme',
      width: 280,
      render: (_, rec) => (
        <div className="douyin-aweme-cell">
          {rec.cover_url && (
            <a href={rec.aweme_url} target="_blank" rel="noopener noreferrer" className="douyin-cover-link">
              <img 
                src={rec.cover_url} 
                alt="" 
                className="douyin-cover" 
                referrerPolicy="no-referrer"
                onError={(e) => {
                  (e.target as HTMLImageElement).style.display = 'none'
                }}
              />
            </a>
          )}
          <div className="douyin-aweme-info">
            <a href={rec.aweme_url} target="_blank" rel="noopener noreferrer" className="douyin-title-link">
              <span className="douyin-title-text">{rec.title || '无标题'}</span>
            </a>
            <div className="douyin-stats">
              {rec.liked_count && <span>❤️ {rec.liked_count}</span>}
              {rec.comment_count && <span>💬 {rec.comment_count}</span>}
              {rec.collected_count && <span>⭐ {rec.collected_count}</span>}
            </div>
            {rec.create_time_str && (
              <Text type="secondary" style={{ fontSize: 11 }}>{rec.create_time_str}</Text>
            )}
          </div>
        </div>
      ),
    },
    {
      title: '作者',
      key: 'user',
      width: 140,
      render: (_, rec) => (
        <a href={rec.user_profile_url} target="_blank" rel="noopener noreferrer" className="douyin-author-link">
          {rec.avatar ? (
            <img 
              src={rec.avatar} 
              alt="" 
              style={{ width: 24, height: 24, borderRadius: '50%', objectFit: 'cover' }}
              referrerPolicy="no-referrer"
              onError={(e) => {
                (e.target as HTMLImageElement).style.display = 'none'
              }}
            />
          ) : (
            <Avatar size="small" icon={<UserOutlined />} />
          )}
          <Text ellipsis className="douyin-author-name">{rec.nickname || '-'}</Text>
        </a>
      ),
    },
    {
      title: '关键词',
      dataIndex: 'keyword',
      key: 'keyword',
      width: 100,
      render: (val: string) => val ? <Tag>{val}</Tag> : '-',
    },
    {
      title: 'IP 归属',
      dataIndex: 'ip_location',
      key: 'ip_location',
      width: 80,
      render: (val: string) => val || '-',
    },
    {
      title: '发布时间',
      dataIndex: 'create_time_str',
      key: 'create_time_str',
      width: 140,
      render: (val: string) => <Text type="secondary">{val || '-'}</Text>,
    },
    {
      title: '操作',
      key: 'action',
      width: 80,
      render: (_, rec) => (
        <Tooltip title="查看原文">
          <a href={rec.aweme_url} target="_blank" rel="noopener noreferrer" className="douyin-link-btn">
            <LinkOutlined />
          </a>
        </Tooltip>
      ),
    },
  ]

  // 抖音打标结果表格列
  const douyinTaggedColumns: ColumnsType<DouyinTaggedResult> = [
    {
      title: '作品',
      key: 'aweme',
      width: 280,
      render: (_, rec) => (
        <div className="douyin-aweme-cell">
          {rec.cover_url && (
            <a href={rec.aweme_url} target="_blank" rel="noopener noreferrer" className="douyin-cover-link">
              <img 
                src={rec.cover_url} 
                alt="" 
                className="douyin-cover" 
                referrerPolicy="no-referrer"
                onError={(e) => {
                  (e.target as HTMLImageElement).style.display = 'none'
                }}
              />
            </a>
          )}
          <div className="douyin-aweme-info">
            <a href={rec.aweme_url} target="_blank" rel="noopener noreferrer" className="douyin-title-link">
              <span className="douyin-title-text">{rec.title || '无标题'}</span>
            </a>
            <div className="douyin-stats">
              {rec.liked_count && <span>❤️ {rec.liked_count}</span>}
              {rec.comment_count && <span>💬 {rec.comment_count}</span>}
            </div>
            {rec.create_time_str && (
              <Text type="secondary" style={{ fontSize: 11 }}>{rec.create_time_str}</Text>
            )}
          </div>
        </div>
      ),
    },
    {
      title: '作者',
      key: 'user',
      width: 140,
      render: (_, rec) => (
        <a href={rec.user_profile_url} target="_blank" rel="noopener noreferrer" className="douyin-author-link">
          {rec.avatar ? (
            <img 
              src={rec.avatar} 
              alt="" 
              style={{ width: 24, height: 24, borderRadius: '50%', objectFit: 'cover' }}
              referrerPolicy="no-referrer"
              onError={(e) => {
                (e.target as HTMLImageElement).style.display = 'none'
              }}
            />
          ) : (
            <Avatar size="small" icon={<UserOutlined />} />
          )}
          <Text ellipsis className="douyin-author-name">{rec.nickname || '-'}</Text>
        </a>
      ),
    },
    {
      title: '标签',
      dataIndex: 'tag',
      key: 'tag',
      width: 100,
      render: (val: string) => (
        <Tag color={val === 'potential_employee' ? 'green' : val === 'marketing' ? 'orange' : 'default'}>
          {douyinTagMap[val] || val}
        </Tag>
      ),
    },
    {
      title: '置信度',
      dataIndex: 'confidence',
      key: 'confidence',
      width: 80,
      render: (val: string) => (
        <Tag color={val === 'high' ? 'green' : val === 'medium' ? 'orange' : 'default'}>
          {confidenceMap[val] || val}
        </Tag>
      ),
    },
    {
      title: '优先级',
      dataIndex: 'priority',
      key: 'priority',
      width: 70,
      render: (val: number) => (
        <Tag color={val >= 8 ? 'red' : val >= 5 ? 'orange' : 'default'}>{val}</Tag>
      ),
    },
    {
      title: '公司/职位',
      key: 'company_position',
      width: 120,
      render: (_, rec) => (
        <Space orientation="vertical" size={0}>
          {rec.company_mentioned && <Tag color="blue">{rec.company_mentioned}</Tag>}
          {rec.position_mentioned && <Tag color="cyan">{rec.position_mentioned}</Tag>}
          {!rec.company_mentioned && !rec.position_mentioned && '-'}
        </Space>
      ),
    },
    {
      title: '关键证据',
      key: 'key_evidence',
      width: 180,
      render: (_, rec) => rec.key_evidence && rec.key_evidence.length > 0 ? (
        <div 
          className="xhs-clickable-tags"
          onClick={() => {
            Modal.info({
              title: '关键证据',
              content: (
                <Space size={[4, 8]} wrap style={{ marginTop: 12 }}>
                  {rec.key_evidence?.map((e, i) => (
                    <Tag key={i} color="purple">{e}</Tag>
                  ))}
                </Space>
              ),
              okText: '关闭',
            })
          }}
        >
          <Space size={2} wrap>
            {rec.key_evidence.slice(0, 2).map((e, i) => (
              <Tag key={i} color="purple" style={{ fontSize: 11 }}>{e}</Tag>
            ))}
            {rec.key_evidence.length > 2 && (
              <Tag style={{ fontSize: 11 }}>+{rec.key_evidence.length - 2}</Tag>
            )}
          </Space>
        </div>
      ) : '-',
    },
    {
      title: '分析',
      key: 'reason',
      width: 70,
      render: (_, rec) => rec.tag_reason ? (
        <Button
          type="link"
          size="small"
          onClick={() => {
            Modal.info({
              title: '打标分析',
              width: 500,
              content: (
                <div style={{ marginTop: 12 }}>
                  <div style={{ marginBottom: 12 }}>
                    <Space wrap>
                      <Tag color={rec.tag === 'potential_employee' ? 'green' : rec.tag === 'marketing' ? 'orange' : 'default'}>
                        {douyinTagMap[rec.tag] || rec.tag}
                      </Tag>
                      <Tag color={rec.confidence === 'high' ? 'green' : rec.confidence === 'medium' ? 'orange' : 'default'}>
                        置信度: {confidenceMap[rec.confidence] || rec.confidence}
                      </Tag>
                      <Tag color={rec.priority >= 8 ? 'red' : rec.priority >= 5 ? 'orange' : 'default'}>
                        优先级: {rec.priority}
                      </Tag>
                    </Space>
                  </div>
                  <Paragraph>{rec.tag_reason}</Paragraph>
                </div>
              ),
              okText: '关闭',
            })
          }}
        >
          查看
        </Button>
      ) : '-',
    },
    {
      title: '操作',
      key: 'action',
      width: 80,
      fixed: 'right',
      render: (_, rec) => (
        <Tooltip title="查看原文">
          <a href={rec.aweme_url} target="_blank" rel="noopener noreferrer" className="douyin-link-btn">
            <LinkOutlined />
          </a>
        </Tooltip>
      ),
    },
  ]

  // 抖音用户画像表格列
  const douyinProfilesColumns: ColumnsType<DouyinProfile> = [
    {
      title: '用户',
      key: 'user',
      width: 180,
      render: (_, rec) => (
        <a href={rec.user_profile_url} target="_blank" rel="noopener noreferrer" className="douyin-author-link">
          <Text strong>{rec.nickname || '-'}</Text>
        </a>
      ),
    },
    {
      title: '示例作品',
      dataIndex: 'sample_title',
      key: 'sample_title',
      width: 200,
      render: (val: string) => (
        <Tooltip title={val}>
          <Text ellipsis style={{ maxWidth: 180 }}>{val || '-'}</Text>
        </Tooltip>
      ),
    },
    {
      title: '置信度',
      dataIndex: 'confidence',
      key: 'confidence',
      width: 80,
      render: (val: string) => val ? (
        <Tag color={val === 'high' ? 'green' : val === 'medium' ? 'orange' : 'default'}>
          {confidenceMap[val] || val}
        </Tag>
      ) : '-',
    },
    {
      title: '优先级',
      dataIndex: 'priority',
      key: 'priority',
      width: 80,
      render: (val: number) => val ? (
        <Tag color={val >= 8 ? 'red' : val >= 5 ? 'orange' : 'default'}>{val}</Tag>
      ) : '-',
    },
    {
      title: '打标理由',
      dataIndex: 'tag_reason',
      key: 'tag_reason',
      render: (val: string) => (
        <Tooltip title={val}>
          <Text ellipsis style={{ maxWidth: 200 }}>{val || '-'}</Text>
        </Tooltip>
      ),
    },
    {
      title: '操作',
      key: 'action',
      width: 180,
      render: (_, rec) => (
        <Space size={4}>
          <Button
            type="link"
            size="small"
            icon={<EyeOutlined />}
            onClick={() => {
              setCurrentDouyinProfile(rec)
              setIsDouyinProfileModalOpen(true)
            }}
          >
            详情
          </Button>
          {rec.finding_id && (
            <Button
              type="link"
              size="small"
              icon={<CopyOutlined />}
              onClick={(e) => { e.stopPropagation(); handleViewCopywritingById(rec.finding_id!, rec.nickname || '话术详情') }}
            >
              话术
            </Button>
          )}
          <Button
            type="text"
            danger
            size="small"
            icon={<DeleteOutlined />}
            onClick={(e) => handleDeleteDouyinProfile(rec.id, e)}
          />
        </Space>
      ),
    },
  ]

  // 渲染人物画像详情内容
  const renderProfileDetailContent = (record: XhsProfile) => {
    const profileUrl = record.user_id 
      ? `https://www.xiaohongshu.com/user/profile/${record.user_id}`
      : undefined
    
    const collapseItems = []

    // 基础信息
    collapseItems.push({
      key: 'basic',
      label: <Space><UserOutlined /> 基础信息</Space>,
      children: (
        <Descriptions size="small" column={3} bordered className="xhs-profile-descriptions">
          <Descriptions.Item label="用户">
            <a href={profileUrl} target="_blank" rel="noopener noreferrer" className="xhs-user-link">
              <Avatar size="small" src={record.avatar_url} icon={<UserOutlined />} />
              <span className="xhs-user-info">{record.nickname} - {record.user_id}</span>
            </a>
          </Descriptions.Item>
          <Descriptions.Item label="IP 归属">{record.basic_info?.ip_location || '-'}</Descriptions.Item>
          <Descriptions.Item label="账号类型">{record.basic_info?.account_type || '-'}</Descriptions.Item>
          <Descriptions.Item label="粉丝">{record.stats?.fans || '-'}</Descriptions.Item>
          <Descriptions.Item label="关注">{record.stats?.follows || '-'}</Descriptions.Item>
          <Descriptions.Item label="获赞收藏">{record.stats?.likes_and_collects || '-'}</Descriptions.Item>
          <Descriptions.Item label="笔记数">{record.stats?.notes_count || record.notes_count || '-'}</Descriptions.Item>
          <Descriptions.Item label="活跃度">{record.stats?.activity_level || '-'}</Descriptions.Item>
          <Descriptions.Item label="影响力">{record.stats?.influence_level || '-'}</Descriptions.Item>
        </Descriptions>
      ),
    })

    // 身份信息
    collapseItems.push({
      key: 'identity',
      label: <Space><TeamOutlined /> 身份信息</Space>,
      children: (
        <Descriptions size="small" column={3} bordered className="xhs-profile-descriptions">
          <Descriptions.Item label="公司">{record.identity?.company || '-'}</Descriptions.Item>
          <Descriptions.Item label="职位">{record.identity?.position || '-'}</Descriptions.Item>
          <Descriptions.Item label="部门">{record.identity?.department || '-'}</Descriptions.Item>
          <Descriptions.Item label="行业">{record.identity?.industry || '-'}</Descriptions.Item>
          <Descriptions.Item label="职级">{record.identity?.position_level || '-'}</Descriptions.Item>
          <Descriptions.Item label="状态">{record.identity?.employment_status || '-'}</Descriptions.Item>
          <Descriptions.Item label="性别">
            {record.gender_analysis?.conclusion || '-'}
            {record.gender_analysis?.confidence && <Tag style={{ marginLeft: 4 }}>{record.gender_analysis.confidence}</Tag>}
          </Descriptions.Item>
          <Descriptions.Item label="学历">
            {record.bio_analysis?.education?.school_tier || ''} {record.bio_analysis?.education?.degree || '-'}
          </Descriptions.Item>
          <Descriptions.Item label="城市">{record.bio_analysis?.location?.city || '-'}</Descriptions.Item>
        </Descriptions>
      ),
    })

    // 设备信息
    if (record.device_info) {
      collapseItems.push({
        key: 'device',
        label: <Space><InfoCircleOutlined /> 设备信息</Space>,
        children: (
          <Descriptions size="small" column={3} bordered className="xhs-profile-descriptions">
            <Descriptions.Item label="电脑系统">{record.device_info.computer_os || '-'}</Descriptions.Item>
            <Descriptions.Item label="电脑品牌">{record.device_info.computer_brand || '-'}</Descriptions.Item>
            <Descriptions.Item label="手机品牌">{record.device_info.phone_brand || '-'}</Descriptions.Item>
            {record.device_info.evidence && record.device_info.evidence.length > 0 && (
              <Descriptions.Item label="证据" span={2}>
                {record.device_info.evidence.join('；')}
              </Descriptions.Item>
            )}
          </Descriptions>
        ),
      })
    }

    // 性格画像
    if (record.personality_profile) {
      collapseItems.push({
        key: 'personality',
        label: <Space><UserOutlined /> 性格画像</Space>,
        children: (
          <div className="xhs-personality-content">
            {record.personality_profile.keywords && record.personality_profile.keywords.length > 0 && (
              <div className="xhs-personality-row">
                <span className="xhs-label">性格关键词：</span>
                <Space size={4} wrap>
                  {record.personality_profile.keywords.map((k, i) => (
                    <Tag key={i} color="purple">{k}</Tag>
                  ))}
                </Space>
              </div>
            )}
            {record.personality_profile.mbti_estimate && (
              <div className="xhs-personality-row">
                <span className="xhs-label">MBTI：</span>
                <Tag color="blue">{record.personality_profile.mbti_estimate}</Tag>
              </div>
            )}
            {record.personality_profile.vulnerability_points && record.personality_profile.vulnerability_points.length > 0 && (
              <div className="xhs-personality-row">
                <span className="xhs-label">弱点：</span>
                <Space size={4} wrap>
                  {record.personality_profile.vulnerability_points.map((v, i) => (
                    <Tag key={i} color="orange">{v}</Tag>
                  ))}
                </Space>
              </div>
            )}
            {record.personality_profile.trust_building_approach && (
              <div className="xhs-personality-row">
                <span className="xhs-label">信任建立方式：</span>
                <Text>{record.personality_profile.trust_building_approach}</Text>
              </div>
            )}
          </div>
        ),
      })
    }

    // 敏感笔记
    if (record.notes_analysis?.sensitive_notes && record.notes_analysis.sensitive_notes.length > 0) {
      collapseItems.push({
        key: 'sensitive',
        label: <Space><WarningOutlined /> 敏感笔记 <Tag color="red">{record.notes_analysis.sensitive_notes.length}</Tag></Space>,
        children: (
          <div className="xhs-sensitive-notes">
            {record.notes_analysis.sensitive_notes.map((note, i) => (
              <div key={i} className="xhs-sensitive-note-item">
                <div className="xhs-sensitive-note-header">
                  <Tag color={note.sensitive_level === '极高' ? 'red' : note.sensitive_level === '高' ? 'orange' : 'blue'}>
                    {note.sensitive_level}
                  </Tag>
                  <Text strong>{note.title}</Text>
                  <Tag>{note.type}</Tag>
                </div>
                <div className="xhs-sensitive-note-info">
                  <Text type="secondary">暴露信息：{note.exposed_info?.join('、') || '-'}</Text>
                </div>
                <div className="xhs-sensitive-note-exploit">
                  <Text type="secondary">利用方式：{note.exploitability || '-'}</Text>
                </div>
              </div>
            ))}
          </div>
        ),
      })
    }

    // 暴露信息
    if (record.attack_surface?.exposed_information && record.attack_surface.exposed_information.length > 0) {
      collapseItems.push({
        key: 'exposed',
        label: <Space><ExclamationCircleOutlined /> 暴露信息 <Tag color="orange">{record.attack_surface.exposed_information.length}</Tag></Space>,
        children: (
          <div className="xhs-exposed-list">
            {record.attack_surface.exposed_information.map((info, i) => (
              <div key={i} className="xhs-exposed-item">
                <Tag color="orange">{info.type}</Tag>
                <Text className="xhs-exposed-value">{info.value}</Text>
                <Text type="secondary" className="xhs-exposed-meta">
                  来源: {info.source} | 敏感度: {info.sensitivity} | {info.freshness}
                </Text>
              </div>
            ))}
          </div>
        ),
      })
    }

    // 攻击向量
    if (record.attack_surface?.attack_vectors && record.attack_surface.attack_vectors.length > 0) {
      collapseItems.push({
        key: 'attack',
        label: <Space><AimOutlined /> 攻击向量 <Tag color="red">{record.attack_surface.attack_vectors.length}</Tag></Space>,
        children: (
          <div className="xhs-vector-list-new">
            {record.attack_surface.attack_vectors.map((v, i) => (
              <div key={i} className="xhs-vector-item">
                <div className="xhs-vector-header">
                  <Tag color={v.difficulty === '低' ? 'green' : v.difficulty === '中' ? 'orange' : 'red'}>
                    {v.difficulty}难度
                  </Tag>
                  <Tag color={v.success_probability === '高' ? 'green' : v.success_probability === '中' ? 'orange' : 'default'}>
                    成功率{v.success_probability}
                  </Tag>
                  <span className="xhs-vector-name">{v.vector}</span>
                </div>
                <div className="xhs-vector-method">{v.method}</div>
                <div className="xhs-vector-gain">
                  <Text type="secondary">预期收益：{v.potential_gain}</Text>
                </div>
              </div>
            ))}
          </div>
        ),
      })
    }

    // 建议操作
    if (record.recommended_actions && record.recommended_actions.length > 0) {
      collapseItems.push({
        key: 'actions',
        label: <Space><EyeOutlined /> 建议操作 <Tag color="blue">{record.recommended_actions.length}</Tag></Space>,
        children: (
          <div className="xhs-action-list-new">
            {record.recommended_actions.map((a, i) => (
              <div key={i} className="xhs-action-item">
                <div className="xhs-action-header">
                  <Tag color={a.priority === '高' ? 'red' : a.priority === '中' ? 'orange' : 'blue'}>
                    {a.priority}优先级
                  </Tag>
                  <Tag color={a.difficulty === '低' ? 'green' : a.difficulty === '中' ? 'orange' : 'red'}>
                    {a.difficulty}难度
                  </Tag>
                  <span className="xhs-action-name">{a.action}</span>
                </div>
                <div className="xhs-action-desc">{a.description}</div>
                <div className="xhs-action-outcome">
                  <Text type="secondary">预期结果：{a.expected_outcome}</Text>
                </div>
              </div>
            ))}
          </div>
        ),
      })
    }

    // 社交图谱
    if (record.social_graph) {
      collapseItems.push({
        key: 'social',
        label: <Space><TeamOutlined /> 社交图谱</Space>,
        children: (
          <Descriptions size="small" column={3} bordered className="xhs-profile-descriptions">
            <Descriptions.Item label="团队信息">{record.social_graph.team_info || '-'}</Descriptions.Item>
            <Descriptions.Item label="社交圈">{record.social_graph.social_circle || '-'}</Descriptions.Item>
            <Descriptions.Item label="关系状态">{record.social_graph.relationship_status || '-'}</Descriptions.Item>
            {record.social_graph.mentioned_companies && record.social_graph.mentioned_companies.length > 0 && (
              <Descriptions.Item label="提及公司" span={2}>
                <Space size={4} wrap>
                  {record.social_graph.mentioned_companies.map((c, i) => (
                    <Tag key={i} color="blue">{c}</Tag>
                  ))}
                </Space>
              </Descriptions.Item>
            )}
          </Descriptions>
        ),
      })
    }

    // 职业时间线
    if (record.timeline?.career_history && record.timeline.career_history.length > 0) {
      collapseItems.push({
        key: 'timeline',
        label: <Space><FileTextOutlined /> 职业经历 <Tag>{record.timeline.career_history.length}</Tag></Space>,
        children: (
          <div className="xhs-timeline-list">
            {record.timeline.career_history.map((item, i) => (
              <div key={i} className="xhs-timeline-item">
                <Tag color={item.period === '当前' ? 'green' : 'default'}>{item.period}</Tag>
                <Text strong>{item.company}</Text>
                <Text type="secondary"> - {item.position}</Text>
              </div>
            ))}
          </div>
        ),
      })
    }

    // 画像摘要
    if (record.profile_summary) {
      collapseItems.push({
        key: 'summary',
        label: <Space><FileTextOutlined /> 画像摘要</Space>,
        children: (
          <Paragraph className="xhs-profile-summary">{record.profile_summary}</Paragraph>
        ),
      })
    }

    // 标签
    if (record.tags && record.tags.length > 0) {
      collapseItems.push({
        key: 'tags',
        label: <Space>标签 <Tag color="blue">{record.tags.length}</Tag></Space>,
        children: (
          <Space size={4} wrap>
            {record.tags.map((tag, i) => (
              <Tag key={i} color="blue">{tag}</Tag>
            ))}
          </Space>
        ),
      })
    }

    return (
      <div className="xhs-profile-detail-content">
        <Collapse 
          defaultActiveKey={['basic', 'identity']} 
          ghost 
          items={collapseItems}
          className="xhs-profile-collapse"
        />
      </div>
    )
  }

  // Tab 内容渲染
  const renderWebsiteContent = () => (
    <>
      <div className="project-detail-section-title">
        <Space><SearchOutlined /> Web Tagging 记录</Space>
      </div>
      <div className="tagging-table-container">
        <Table
          className="tagging-table"
          dataSource={taggingRecords}
          rowKey="id"
          loading={taggingLoading}
          locale={{ emptyText: <Empty description="暂无 Web Tagging 记录" /> }}
          pagination={{
            total: taggingTotal,
            pageSize: 10,
            showSizeChanger: true,
            showTotal: (total) => `共 ${total} 条记录`,
            onChange: (page, pageSize) => { if (projectId) fetchRecords(projectId, page, pageSize) },
          }}
          expandable={{
            expandedRowRender: (rec) => <ExpandedRecordContent record={rec} onViewCopywriting={handleViewFindingCopywriting} />,
            expandRowByClick: true,
          }}
          columns={taggingColumns}
        />
      </div>
    </>
  )

  const renderXiaohongshuContent = () => {
    const items = [
      {
        key: 'notes',
        label: (
          <Space>
            <FileTextOutlined />
            <span>笔记列表</span>
            <Tag>{xhsNotes.length}</Tag>
          </Space>
        ),
        children: (
          <Table
            className="xhs-notes-table"
            dataSource={xhsNotes}
            rowKey="id"
            loading={xhsNotesLoading}
            columns={xhsNotesColumns}
            locale={{ emptyText: <Empty description="暂无小红书笔记数据" /> }}
            pagination={{
              total: xhsNotesTotal,
              pageSize: 10,
              showSizeChanger: true,
              showTotal: (total) => `共 ${total} 条笔记`,
              onChange: (page, pageSize) => { if (projectId) fetchXhsNotes(projectId, page, pageSize) },
            }}
            size="small"
          />
        ),
      },
      {
        key: 'profiles',
        label: (
          <div className="xhs-panel-header">
            <Space>
              <TeamOutlined />
              <span>人物画像</span>
              <Tag color="blue">{xhsProfiles.length}</Tag>
            </Space>
            <Button
              type="primary"
              size="small"
              icon={<PlusOutlined />}
              onClick={(e) => {
                e.stopPropagation()
                setIsProfileDrawerOpen(true)
              }}
            >
              新增画像
            </Button>
          </div>
        ),
        children: (
          <Table
            className="xhs-profiles-table"
            dataSource={xhsProfiles}
            rowKey="id"
            loading={xhsProfilesLoading}
            columns={xhsProfilesColumns}
            locale={{ emptyText: <Empty description="暂无人物画像数据" /> }}
            pagination={{
              total: xhsProfilesTotal,
              pageSize: 10,
              showSizeChanger: true,
              showTotal: (total) => `共 ${total} 个画像`,
              onChange: (page, pageSize) => { if (projectId) fetchXhsProfiles(projectId, page, pageSize) },
            }}
            size="small"
          />
        ),
      },
    ]

    return (
      <>
        <div className="xhs-content-header">
          <Button
            type="primary"
            icon={<SearchOutlined />}
            onClick={handleAddXhsSearch}
            className="hover-float"
          >
            新建搜索任务
          </Button>
        </div>

        <Collapse defaultActiveKey={['notes', 'profiles']} className="xhs-collapse" ghost items={items} />
      </>
    )
  }

  // 抖音内容渲染
  const renderDouyinContent = () => {
    const items = [
      {
        key: 'search',
        label: (
            <Space>
              <SearchOutlined />
              <span>搜索结果</span>
              <Tag>{douyinSearchResults.length}</Tag>
            </Space>
        ),
        children: (
          <Table
            className="douyin-search-table"
            dataSource={douyinSearchResults}
            rowKey="id"
            loading={douyinSearchLoading}
            columns={douyinSearchColumns}
            locale={{ emptyText: <Empty description="暂无抖音搜索结果" /> }}
            pagination={{
              total: douyinSearchTotal,
              pageSize: 10,
              showSizeChanger: true,
              showTotal: (total) => `共 ${total} 条结果`,
              onChange: (page, pageSize) => { if (projectId) fetchDouyinSearchResults(projectId, page, pageSize) },
            }}
            size="small"
          />
        ),
      },
      {
        key: 'tagged',
        label: (
            <div className="douyin-panel-header">
              <Space>
                <FileTextOutlined />
                <span>打标结果</span>
                <Tag color="green">{douyinTaggedStats.potential_employee} 潜在员工</Tag>
                <Tag color="orange">{douyinTaggedStats.marketing} 营销号</Tag>
                <Tag>{douyinTaggedStats.uncertain} 不确定</Tag>
              </Space>
              <Select
                placeholder="筛选标签"
                allowClear
                style={{ width: 120 }}
                value={douyinTagFilter}
                onChange={(val) => {
                  setDouyinTagFilter(val)
                  if (projectId) {
                    fetchDouyinTaggedResults(projectId, val)
                  }
                }}
                options={[
                  { label: '潜在员工', value: 'potential_employee' },
                  { label: '营销号', value: 'marketing' },
                  { label: '不确定', value: 'uncertain' },
                ]}
                onClick={(e) => e.stopPropagation()}
              />
            </div>
        ),
        children: (
          <Table
            className="douyin-tagged-table"
            dataSource={douyinTaggedResults}
            rowKey="id"
            loading={douyinTaggedLoading}
            columns={douyinTaggedColumns}
            locale={{ emptyText: <Empty description="暂无打标结果" /> }}
            pagination={{
              total: douyinTaggedTotal,
              pageSize: 10,
              showSizeChanger: true,
              showTotal: (total) => `共 ${total} 条结果`,
              onChange: (page, pageSize) => { if (projectId) fetchDouyinTaggedResults(projectId, douyinTagFilter, page, pageSize) },
            }}
            size="small"
          />
        ),
      },
      {
        key: 'profiles',
        label: (
            <Space>
              <TeamOutlined />
              <span>用户画像</span>
              <Tag color="blue">{douyinProfiles.length}</Tag>
            </Space>
        ),
        children: (
          <Table
            className="douyin-profiles-table"
            dataSource={douyinProfiles}
            rowKey="id"
            loading={douyinProfilesLoading}
            columns={douyinProfilesColumns}
            locale={{ emptyText: <Empty description="暂无用户画像" /> }}
            pagination={{
              total: douyinProfilesTotal,
              pageSize: 10,
              showSizeChanger: true,
              showTotal: (total) => `共 ${total} 个画像`,
              onChange: (page, pageSize) => { if (projectId) fetchDouyinProfiles(projectId, page, pageSize) },
            }}
            size="small"
          />
        ),
      },
    ]

    return <Collapse defaultActiveKey={['tagged', 'profiles']} className="douyin-collapse" ghost items={items} />
  }

  const renderMobileContent = () => {
    const renderProfileTags = (values: string[] | undefined, color?: string) => (
      <Space size={[4, 6]} wrap>
        {values?.length
          ? values.map((value, index) => <Tag color={color} key={`${value}-${index}`}>{value}</Tag>)
          : <Text type="secondary">-</Text>}
      </Space>
    )

    const renderMobileProfileExpanded = (record: ContactProfile) => {
      const persona = record.persona ?? { interests: [], tags: [] }
      const confidence = typeof persona.confidence === 'number'
        ? `${Math.round((persona.confidence <= 1 ? persona.confidence * 100 : persona.confidence))}%`
        : '-'

      return (
        <div className="mobile-profile-expanded">
          <Descriptions size="small" bordered column={1}>
            <Descriptions.Item label="画像摘要">{persona.summary || '-'}</Descriptions.Item>
            <Descriptions.Item label="背景">{persona.background || '-'}</Descriptions.Item>
            <Descriptions.Item label="性格">{persona.personality || '-'}</Descriptions.Item>
            <Descriptions.Item label="沟通风格">{persona.communication_style || '-'}</Descriptions.Item>
            <Descriptions.Item label="语气">{persona.tone || '-'}</Descriptions.Item>
            <Descriptions.Item label="回复习惯">{persona.reply_pattern || '-'}</Descriptions.Item>
            <Descriptions.Item label="置信度">{confidence}</Descriptions.Item>
            <Descriptions.Item label="兴趣">{renderProfileTags(persona.interests, 'blue')}</Descriptions.Item>
            <Descriptions.Item label="标签">{renderProfileTags(persona.tags)}</Descriptions.Item>
            <Descriptions.Item label="常用表达">{renderProfileTags(persona.common_phrases, 'cyan')}</Descriptions.Item>
            <Descriptions.Item label="风险点">{renderProfileTags(persona.risk_signals, 'red')}</Descriptions.Item>
            <Descriptions.Item label="关联 Finding">
              {record.latest_finding_id
                ? <Text copyable={{ text: record.latest_finding_id }}>{record.latest_finding_id}</Text>
                : '-'}
            </Descriptions.Item>
            <Descriptions.Item label="观察记录">{record.observations?.length ?? 0} 条</Descriptions.Item>
          </Descriptions>
        </div>
      )
    }

    const profileColumns: ColumnsType<ContactProfile> = [
      {
        title: '联系人',
        key: 'contact',
        width: 220,
        render: (_: unknown, rec) => (
          <Space orientation="vertical" size={0}>
            <Text strong>{rec.name || rec.contact_id}</Text>
            <Text type="secondary" copyable={{ text: rec.contact_id }}>{rec.contact_id}</Text>
          </Space>
        ),
      },
      {
        title: '平台/设备',
        key: 'source',
        width: 180,
        render: (_: unknown, rec) => (
          <Space orientation="vertical" size={0}>
            <Tag color="blue">{rec.platform || '未知平台'}</Tag>
            <Text type="secondary">{rec.device_id || '-'}</Text>
          </Space>
        ),
      },
      {
        title: '画像摘要',
        key: 'persona',
        render: (_: unknown, rec) => (
          <Space orientation="vertical" size={4}>
            <Text>{rec.persona?.summary || rec.persona?.background || '-'}</Text>
            <Space size={4} wrap>
              {(rec.persona?.tags ?? []).slice(0, 6).map((tag) => <Tag key={tag}>{tag}</Tag>)}
            </Space>
          </Space>
        ),
      },
      {
        title: '更新时间',
        dataIndex: 'updated_at',
        key: 'updated_at',
        width: 170,
        render: (val: string) => <Text type="secondary">{formatDate(val)}</Text>,
      },
      {
        title: '查看',
        key: 'view',
        width: 64,
        fixed: 'right',
        align: 'center',
        render: (_: unknown, rec) => {
          const expanded = expandedMobileProfileIds.includes(rec.contact_id)
          return (
            <Tooltip title={expanded ? '收起画像' : '展开画像'}>
              <Button
                type="text"
                size="small"
                className={expanded ? 'mobile-profile-eye is-expanded' : 'mobile-profile-eye'}
                icon={expanded ? <EyeInvisibleOutlined /> : <EyeOutlined />}
                aria-label={expanded ? '收起画像' : '展开画像'}
                onClick={(event) => {
                  event.stopPropagation()
                  setExpandedMobileProfileIds((current) => (
                    current.includes(rec.contact_id)
                      ? current.filter((id) => id !== rec.contact_id)
                      : [...current, rec.contact_id]
                  ))
                }}
              />
            </Tooltip>
          )
        },
      },
    ]

    const observationColumns: ColumnsType<MobileProfileObservation> = [
      {
        title: '时间',
        dataIndex: 'created_at',
        key: 'created_at',
        width: 170,
        render: (val: string) => <Text type="secondary">{formatDate(val)}</Text>,
      },
      {
        title: '联系人',
        key: 'contact',
        width: 220,
        render: (_: unknown, rec) => (
          <Space orientation="vertical" size={0}>
            <Text strong>{rec.contact_name || rec.contact_id}</Text>
            <Text type="secondary" copyable={{ text: rec.contact_id }}>{rec.contact_id}</Text>
          </Space>
        ),
      },
      {
        title: 'Finding',
        dataIndex: 'finding_id',
        key: 'finding_id',
        width: 190,
        render: (val?: string | null) => (val ? <Text copyable={{ text: val }}>{val}</Text> : '-'),
      },
      {
        title: '新增画像信号',
        key: 'signals',
        render: (_: unknown, rec) => {
          const patch = rec.persona_patch ?? {}
          const tags = Array.isArray(patch.tags) ? patch.tags : []
          const risks = Array.isArray(patch.risk_signals) ? patch.risk_signals : []
          const phrases = Array.isArray(patch.common_phrases) ? patch.common_phrases : []
          return (
            <Space orientation="vertical" size={4}>
              <Text>{String(patch.summary || patch.communication_style || patch.background || '-')}</Text>
              <Space size={4} wrap>
                {tags.slice(0, 4).map((tag) => <Tag key={String(tag)}>{String(tag)}</Tag>)}
                {phrases.slice(0, 3).map((phrase) => <Tag color="blue" key={String(phrase)}>{String(phrase)}</Tag>)}
                {risks.slice(0, 3).map((risk) => <Tag color="orange" key={String(risk)}>{String(risk)}</Tag>)}
              </Space>
            </Space>
          )
        },
      },
      {
        title: '证据',
        key: 'evidence',
        width: 180,
        render: (_: unknown, rec) => {
          const shot = typeof rec.evidence?.screenshot_id === 'string' ? rec.evidence.screenshot_id : ''
          return (
            <Space orientation="vertical" size={0}>
              <Tag>{rec.source || 'profile'}</Tag>
              {shot ? <Text type="secondary" copyable={{ text: shot }}>{shot}</Text> : <Text type="secondary">-</Text>}
            </Space>
          )
        },
      },
    ]

    const operationColumns: ColumnsType<MobileOperationLog> = [
      {
        title: '时间',
        dataIndex: 'created_at',
        key: 'created_at',
        width: 170,
        render: (val: string) => <Text type="secondary">{formatDate(val)}</Text>,
      },
      {
        title: '类型',
        dataIndex: 'operation_type',
        key: 'operation_type',
        width: 130,
        render: (val: string) => <Tag color="geekblue">{val}</Tag>,
      },
      {
        title: '动作',
        dataIndex: 'action',
        key: 'action',
        width: 140,
        render: (val: string) => val || '-',
      },
      {
        title: '状态',
        dataIndex: 'status',
        key: 'status',
        width: 90,
        render: (val: string) => <Tag color={val === 'ok' ? 'success' : val === 'error' ? 'error' : 'default'}>{val || '-'}</Tag>,
      },
      {
        title: '设备/联系人',
        key: 'target',
        width: 210,
        render: (_: unknown, rec) => (
          <Space orientation="vertical" size={0}>
            <Text>{rec.device_id || '-'}</Text>
            <Text type="secondary">{rec.contact_id || '-'}</Text>
          </Space>
        ),
      },
      {
        title: '消息',
        key: 'message',
        render: (_: unknown, rec) => {
          const dataText = rec.data && Object.keys(rec.data).length ? JSON.stringify(rec.data) : ''
          return (
            <Tooltip title={dataText || rec.message || ''}>
              <span className="mobile-operation-message">{rec.message || dataText || '-'}</span>
            </Tooltip>
          )
        },
      },
    ]

    const sessionColumns: ColumnsType<AutoChatSession> = [
      {
        title: '任务',
        dataIndex: 'task_id',
        key: 'task_id',
        width: 180,
        render: (val: string) => <Text copyable={{ text: val }}>{val}</Text>,
      },
      {
        title: '联系人',
        key: 'contact',
        width: 180,
        render: (_: unknown, rec) => rec.contact_name || rec.contact_id || '-',
      },
      {
        title: '状态',
        key: 'running',
        width: 100,
        render: (_: unknown, rec) => <Tag color={rec.running ? 'processing' : 'default'}>{rec.running ? '运行中' : '已停止'}</Tag>,
      },
      { title: '轮次', dataIndex: 'rounds', key: 'rounds', width: 80 },
      { title: '已发送', dataIndex: 'replies_sent', key: 'replies_sent', width: 90 },
      {
        title: '最近回复',
        dataIndex: 'last_reply',
        key: 'last_reply',
        render: (val: string) => val || '-',
      },
    ]

    const items = [
      {
        key: 'profiles',
        label: <Space><TeamOutlined /> 手机人物画像 <Tag color="blue">{mobileProfilesTotal}</Tag></Space>,
        children: (
          <Table<ContactProfile>
            dataSource={mobileProfiles}
            rowKey="contact_id"
            loading={mobileLoading}
            columns={profileColumns}
            size="small"
            scroll={{ x: 900 }}
            expandable={{
              expandedRowKeys: expandedMobileProfileIds,
              expandedRowRender: renderMobileProfileExpanded,
              showExpandColumn: false,
              onExpand: (expanded, record) => {
                setExpandedMobileProfileIds((current) => (
                  expanded
                    ? Array.from(new Set([...current, record.contact_id]))
                    : current.filter((id) => id !== record.contact_id)
                ))
              },
            }}
            locale={{ emptyText: <Empty description="暂无手机人物画像" /> }}
            pagination={{ pageSize: 10, showTotal: (total) => `共 ${total} 个画像` }}
          />
        ),
      },
      {
        key: 'observations',
        label: <Space><BarChartOutlined /> 画像观察明细 <Tag color="purple">{mobileObservationsTotal}</Tag></Space>,
        children: (
          <Table<MobileProfileObservation>
            dataSource={mobileObservations}
            rowKey="observation_id"
            loading={mobileLoading}
            columns={observationColumns}
            size="small"
            locale={{ emptyText: <Empty description="暂无画像观察明细" /> }}
            pagination={{ pageSize: 10, showTotal: (total) => `共 ${total} 条观察` }}
          />
        ),
      },
      {
        key: 'screenshots',
        label: <Space><PictureOutlined /> 真实手机截图 <Tag color="green">{mobileScreenshotsTotal}</Tag></Space>,
        children: mobileLoading ? (
          <Skeleton active />
        ) : mobileScreenshots.length === 0 ? (
          <Empty description="暂无手机截图" />
        ) : (
          <div className="mobile-screenshot-grid">
            {mobileScreenshots.map((shot) => (
              <Card
                key={shot.screenshot_id}
                size="small"
                className="mobile-shot-card"
                hoverable
                onClick={() => setMobilePreview(shot)}
              >
                <MobileScreenshotImage screenshot={shot} />
                <div className="mobile-shot-meta">
                  <Text className="mobile-shot-id" copyable={{ text: shot.screenshot_id }}>{shot.screenshot_id}</Text>
                  <Text type="secondary">{formatDate(shot.created_at)}</Text>
                  <Space size={4} wrap>
                    {shot.source && <Tag>{shot.source}</Tag>}
                    {shot.device_id && <Tag color="blue">{shot.device_id}</Tag>}
                    {shot.contact_id && <Tag color="purple">{shot.contact_id}</Tag>}
                  </Space>
                </div>
              </Card>
            ))}
          </div>
        ),
      },
      {
        key: 'operations',
        label: <Space><MobileOutlined /> 手机操作日志 <Tag color="geekblue">{mobileOperationsTotal}</Tag></Space>,
        children: (
          <Table<MobileOperationLog>
            dataSource={mobileOperations}
            rowKey="operation_id"
            loading={mobileLoading}
            columns={operationColumns}
            size="small"
            locale={{ emptyText: <Empty description="暂无手机操作日志" /> }}
            pagination={{ pageSize: 12, showTotal: (total) => `共 ${total} 条日志` }}
          />
        ),
      },
      {
        key: 'sessions',
        label: <Space><ClockCircleOutlined /> 自动聊天会话 <Tag>{mobileSessionsTotal}</Tag></Space>,
        children: (
          <Table<AutoChatSession>
            dataSource={mobileSessions}
            rowKey="task_id"
            loading={mobileLoading}
            columns={sessionColumns}
            size="small"
            locale={{ emptyText: <Empty description="暂无自动聊天会话" /> }}
            pagination={{ pageSize: 8, showTotal: (total) => `共 ${total} 个会话` }}
          />
        ),
      },
    ]

    return (
      <div className="mobile-project-panel">
        <Row gutter={[12, 12]} className="mobile-project-stats">
          <Col xs={12} sm={6}><Statistic title="手机画像" value={mobileProfilesTotal} /></Col>
          <Col xs={12} sm={6}><Statistic title="画像观察" value={mobileObservationsTotal} /></Col>
          <Col xs={12} sm={6}><Statistic title="真实截图" value={mobileScreenshotsTotal} /></Col>
          <Col xs={12} sm={6}><Statistic title="操作日志" value={mobileOperationsTotal} /></Col>
          <Col xs={12} sm={6}><Statistic title="聊天会话" value={mobileSessionsTotal} /></Col>
        </Row>

        <Collapse defaultActiveKey={['profiles', 'observations', 'screenshots', 'operations']} ghost items={items} />
      </div>
    )
  }

  const renderWechatContent = () => {
    return (
      <div>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 12, flexWrap: 'wrap', gap: 8 }}>
          <Text type="secondary">
            手机负责发现文章，浏览器池永久保存全文、原图、截图和结构化版本；记录按 Target 聚类。
          </Text>
          <Space>
            <Select
              value={wechatTargetId}
              style={{ width: 360, maxWidth: '100%' }}
              options={[
                { value: '', label: `全部 Target (${wechatTargets.length})` },
                ...wechatTargets.map((target) => ({
                  value: target.target_id,
                  label: `${target.relation_type === 'wholly_owned_controlled_entity' ? '[100% 控股] ' : ''}${target.target_name}${target.root_domain ? ` · ${target.root_domain}` : ''} (记录 ${target.record_count} · 原文 ${target.project_document_count} · 存活资产 ${target.alive_asset_count})`,
                })),
              ]}
              onChange={(value) => {
                setWechatTargetId(value)
                if (projectId) fetchWechatRecords(projectId, wechatOnlyIncremental, value)
              }}
            />
            <span>仅增量</span>
            <Checkbox
              checked={wechatOnlyIncremental}
              onChange={(e) => {
                setWechatOnlyIncremental(e.target.checked)
                if (projectId) fetchWechatRecords(projectId, e.target.checked, wechatTargetId)
              }}
            />
            <Button
              size="small"
              icon={<SyncOutlined />}
              loading={wechatLoading}
              onClick={() => { if (projectId) fetchWechatRecords(projectId, wechatOnlyIncremental, wechatTargetId) }}
            >
              刷新
            </Button>
          </Space>
        </div>
        <CollectRecordsView
          records={wechatRecords}
          loading={wechatLoading}
          emptyText="暂无公众号采集记录，请在手机采集中创建任务并绑定本项目与 Target"
        />
      </div>
    )
  }

  const renderScholarsContent = () => {
    const buildArticleUrl = (r: ScholarContact): string | null => {
      if (r.article_doi) return `https://doi.org/${encodeURIComponent(r.article_doi)}`
      if (r.article_pmcid) return `https://europepmc.org/article/PMC/${encodeURIComponent(r.article_pmcid.replace(/^PMC/i, ''))}`
      if (r.article_landing_page) return r.article_landing_page
      const aid = r.article_id || ''
      if (/^10\./.test(aid)) return `https://doi.org/${encodeURIComponent(aid)}`
      if (/^PMC\d+/i.test(aid)) return `https://europepmc.org/article/PMC/${encodeURIComponent(aid.replace(/^PMC/i, ''))}`
      return null
    }
    const columns: ColumnsType<ScholarContact> = [
      {
        title: '邮箱',
        dataIndex: 'email',
        key: 'email',
        width: 300,
        render: (v: string, r) => (
          <Space size={4} wrap>
            <Text copyable>{v}</Text>
            {r.email_kind === 'personal'
              ? <Tag color="volcano">私人</Tag>
              : <Tag>机构</Tag>}
          </Space>
        ),
      },
      {
        title: '通讯',
        key: 'corr',
        width: 80,
        render: (_, r) =>
          r.is_corresponding ? <Tag color="green">通讯</Tag> : <Tag>联系</Tag>,
      },
      { title: '作者', dataIndex: 'author_name', key: 'author_name', width: 150, ellipsis: true, render: (v) => v || '-' },
      {
        title: '单位',
        dataIndex: 'unit',
        key: 'unit',
        width: 220,
        ellipsis: true,
        render: (v, r) => (
          <Space size={4} wrap>
            <span>{v || '-'}</span>
            {r.unit_verified ? (
              <Tooltip title={r.evidence || '匹配到目标单位别名'}>
                <Tag color="green">✓ 已验证</Tag>
              </Tooltip>
            ) : (
              <Tooltip title={r.evidence || '<corresp>/<aff> 中未直接命中目标单位别名，请人工确认'}>
                <Tag color="orange">⚠ 未匹配</Tag>
              </Tooltip>
            )}
          </Space>
        ),
      },
      { title: '年份', dataIndex: 'article_year', key: 'article_year', width: 70, render: (v) => v || '-' },
      {
        title: '来源',
        dataIndex: 'source_key',
        key: 'source_key',
        width: 100,
        render: (v: string) => <Tag color="blue">{v}</Tag>,
      },
      {
        title: '文章',
        key: 'article',
        ellipsis: true,
        render: (_, r) => {
          const url = buildArticleUrl(r)
          const label = r.article_title || r.article_id
          if (url) {
            return (
              <a href={url} target="_blank" rel="noreferrer" title={r.article_title || r.article_id} style={{ wordBreak: 'break-all' }}>
                {label}
              </a>
            )
          }
          return <span title={r.article_id} style={{ wordBreak: 'break-all' }}>{label}</span>
        },
      },
      {
        title: '快查',
        key: 'lookup',
        width: 170,
        render: (_, r) => {
          const q = encodeURIComponent(r.author_name || r.email)
          return (
            <Space size={4}>
              <a href={`https://scholar.google.com/scholar?q=${q}`} target="_blank" rel="noreferrer">Scholar</a>
              <a href={`https://orcid.org/orcid-search/search?searchQuery=${q}`} target="_blank" rel="noreferrer">ORCID</a>
              <a href={`https://openalex.org/works?search=${q}`} target="_blank" rel="noreferrer">OpenAlex</a>
            </Space>
          )
        },
      },
    ]

    return (
      <div>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 12, flexWrap: 'wrap', gap: 8 }}>
          <Text type="secondary">
            学者学术联系来自「学者联系发现」任务(按单位+方向从公开学术源抽取文章绑定的通讯邮箱),仅用于一对一学术合作,不做整单位名单聚合。
          </Text>
          <Space>
            <span>仅通讯作者</span>
            <Checkbox
              checked={scholarOnlyCorresponding}
              onChange={(e) => {
                setScholarOnlyCorresponding(e.target.checked)
                if (projectId) fetchScholarContacts(projectId, e.target.checked, scholarOnlyVerified)
              }}
            />
            <span>仅目标单位验证</span>
            <Checkbox
              checked={scholarOnlyVerified}
              onChange={(e) => {
                setScholarOnlyVerified(e.target.checked)
                if (projectId) fetchScholarContacts(projectId, scholarOnlyCorresponding, e.target.checked)
              }}
            />
            <Button
              size="small"
              icon={<SyncOutlined />}
              loading={scholarLoading}
              onClick={() => { if (projectId) fetchScholarContacts(projectId) }}
            >
              刷新
            </Button>
          </Space>
        </div>
        <Table<ScholarContact>
          rowKey="doc_id"
          size="small"
          loading={scholarLoading}
          columns={columns}
          dataSource={scholarContacts}
          locale={{
            emptyText: (
              <Empty description='暂无学者联系，请在「任务」中创建"学者联系发现"任务(填写单位+研究方向)' />
            ),
          }}
          pagination={{ pageSize: 15, showTotal: (t) => `共 ${t} 条` }}
        />
      </div>
    )
  }

  // 数据源中文映射
  const sourceNameMap: Record<string, string> = { web_tagging: '官网打标', xhs: '小红书', douyin: '抖音', mobile: '手机画像' }
  // 渲染简易看板（基本信息下方）
  const renderMiniDashboard = () => {
    if (dashboardLoading) return <Skeleton active paragraph={{ rows: 1 }} />
    if (!dashboardData) return null

    const f = dashboardData.findings ?? { total: 0, by_source: {}, score_distribution: { high: 0, medium: 0, low: 0 } }
    const t = dashboardData.tasks ?? { total: 0, by_status: {} }
    const c = dashboardData.data_counts ?? {
      copywritings: 0,
      xhs_notes: 0,
      xhs_profiles: 0,
      web_tagging: 0,
      douyin_search: 0,
      douyin_tagged: 0,
      douyin_profiles: 0,
      mobile_profiles: 0,
      mobile_profile_observations: 0,
    }

    return (
      <div className="mini-dashboard slide-up stagger-2">
        <Row gutter={[8, 8]}>
          <Col xs={8} sm={6} md={4} lg={3}><Statistic title="发现" value={f.total} styles={{ content: { fontSize: 16 } }} /></Col>
          <Col xs={8} sm={6} md={4} lg={3}><Statistic title="高分" value={f.score_distribution?.high ?? 0} styles={{ content: { fontSize: 16, color: '#ff4d4f' } }} /></Col>
          <Col xs={8} sm={6} md={4} lg={3}><Statistic title="任务" value={t.total} styles={{ content: { fontSize: 16 } }} /></Col>
          <Col xs={8} sm={6} md={4} lg={3}><Statistic title="话术" value={c.copywritings} styles={{ content: { fontSize: 16 } }} /></Col>
          <Col xs={8} sm={6} md={4} lg={3}><Statistic title="笔记" value={c.xhs_notes} styles={{ content: { fontSize: 16 } }} /></Col>
          <Col xs={8} sm={6} md={4} lg={3}><Statistic title="画像" value={(c.xhs_profiles ?? 0) + (c.douyin_profiles ?? 0) + (c.mobile_profiles ?? 0)} styles={{ content: { fontSize: 16 } }} /></Col>
          <Col xs={8} sm={6} md={4} lg={3}><Statistic title="打标" value={c.web_tagging} styles={{ content: { fontSize: 16 } }} /></Col>
          <Col xs={8} sm={6} md={4} lg={3}>
            <Statistic title="数据源" valueRender={() => (
              <Space size={4}>
                {Object.entries(f.by_source ?? {}).map(([src, cnt]) => (
                  <Tag key={src} style={{ fontSize: 11 }}>{sourceNameMap[src] || src}: {cnt as number}</Tag>
                ))}
              </Space>
            )} />
          </Col>
        </Row>
      </div>
    )
  }

  // Tab 配置
  const tabItems = [
    {
      key: 'website' as TabKey,
      label: (
        <Space>
          <GlobalOutlined />
          网站
        </Space>
      ),
      children: renderWebsiteContent(),
    },
    {
      key: 'xiaohongshu' as TabKey,
      label: (
        <Space>
          <img src="https://www.xiaohongshu.com/favicon.ico" alt="小红书" style={{ width: 14, height: 14 }} />
          小红书
        </Space>
      ),
      children: renderXiaohongshuContent(),
    },
    {
      key: 'douyin' as TabKey,
      label: (
        <Space>
          <img src="https://www.douyin.com/favicon.ico" alt="抖音" style={{ width: 14, height: 14 }} />
          抖音
        </Space>
      ),
      children: renderDouyinContent(),
    },
    {
      key: 'wechat' as TabKey,
      label: (
        <Space>
          <FileTextOutlined />
          公众号
          <Tag>{wechatRecordsTotal}</Tag>
        </Space>
      ),
      children: renderWechatContent(),
    },
    {
      key: 'mobile' as TabKey,
      label: (
        <Space>
          <MobileOutlined />
          手机操作
          <Tag>{mobileOperationsTotal + mobileScreenshotsTotal + mobileProfilesTotal + mobileObservationsTotal}</Tag>
        </Space>
      ),
      children: renderMobileContent(),
    },
    {
      key: 'scholars' as TabKey,
      label: (
        <Space>
          <TeamOutlined />
          学者联系
          <Tag>{scholarContactsTotal}</Tag>
        </Space>
      ),
      children: renderScholarsContent(),
    },
    {
      key: 'tasks' as TabKey,
      label: (
        <Space>
          <ThunderboltOutlined />
          任务
          <Tag>{tasks.length}</Tag>
        </Space>
      ),
      children: (
        <>
          <div style={{ display: 'flex', justifyContent: 'flex-end', marginBottom: 12, gap: 8 }}>
            <Button
              danger
              size="small"
              icon={<DeleteOutlined />}
              disabled={tasks.filter(t => t.status === 'error').length === 0}
              onClick={() => {
                if (!projectId) return
                const errorCount = tasks.filter(t => t.status === 'error').length
                Modal.confirm({
                  title: '批量删除失败任务',
                  content: `将删除该项目下 ${errorCount} 个状态为"失败"的任务及其关联数据。`,
                  okText: '删除',
                  okType: 'danger',
                  cancelText: '取消',
                  onOk: async () => {
                    try {
                      const res = await batchDeleteTasks(projectId, { status: 'error' as TaskStatus })
                      message.success(`已删除 ${res.deleted_count} 个任务`)
                      fetchTasks(projectId)
                    } catch (err) {
                      message.error(err instanceof Error ? err.message : '批量删除失败')
                    }
                  },
                })
              }}
            >
              清除失败任务
            </Button>
          </div>
          <Table<Task>
            dataSource={tasks}
            rowKey="task_id"
            size="small"
            loading={tasksLoading}
            pagination={{
              total: tasksTotal,
              pageSize: 10,
              showSizeChanger: true,
              showTotal: (total) => `共 ${total} 个任务`,
              onChange: (page, pageSize) => { if (projectId) fetchTasks(projectId, page, pageSize) },
            }}
            onRow={(record) => ({
              onClick: () => navigate(`/projects/${projectId}/tasks/${record.task_id}`),
              style: { cursor: 'pointer' },
            })}
          columns={[
            {
              title: '任务ID',
              dataIndex: 'task_id',
              key: 'task_id',
              width: 140,
              render: (val: string) => <Text copyable={{ text: val }}>{val}</Text>,
            },
            {
              title: '类型',
              dataIndex: 'task_type',
              key: 'task_type',
              width: 120,
              render: (val: string) => <Tag>{
                val === 'company_scan' ? '综合扫描' :
                val === 'url_scan' ? 'URL 扫描' :
                val === 'xhs_search' ? '小红书搜索' :
                val === 'douyin_search' ? '抖音搜索' :
                val === 'web_tagging' ? '官网打标' :
                val === 'fofa_collect' ? 'FOFA + Hunter 资产采集' : val
              }</Tag>,
            },
            {
              title: '状态',
              dataIndex: 'status',
              key: 'status',
              width: 100,
              render: (val: string) => {
                const map: Record<string, { color: string; icon: React.ReactNode; label: string }> = {
                  pending: { color: 'default', icon: <ClockCircleOutlined />, label: '等待中' },
                  probing: { color: 'processing', icon: <SyncOutlined spin />, label: '探活中' },
                  scanning: { color: 'processing', icon: <SyncOutlined spin />, label: '扫描中' },
                  generating: { color: 'processing', icon: <SyncOutlined spin />, label: '生成中' },
                  running: { color: 'processing', icon: <SyncOutlined spin />, label: '执行中' },
                  completed: { color: 'success', icon: <CheckCircleOutlined />, label: '已完成' },
                  error: { color: 'error', icon: <ExclamationCircleOutlined />, label: '失败' },
                }
                const info = map[val] || map.pending
                return <Tag icon={info.icon} color={info.color}>{info.label}</Tag>
              },
            },
            {
              title: '创建时间',
              dataIndex: 'created_at',
              key: 'created_at',
              render: (val: string) => <Text type="secondary">{val?.replace('T', ' ')}</Text>,
            },
            {
              title: '操作',
              key: 'action',
              width: 140,
              render: (_: unknown, rec: Task) => (
                <Space size={4}>
                  <Button type="link" size="small" icon={<EyeOutlined />} onClick={(e) => { e.stopPropagation(); navigate(`/projects/${projectId}/tasks/${rec.task_id}`) }}>
                    详情
                  </Button>
                  <Button
                    type="text"
                    danger
                    size="small"
                    icon={<DeleteOutlined />}
                    onClick={(e) => {
                      e.stopPropagation()
                      Modal.confirm({
                        title: '确认删除任务',
                        content: '删除后将同时清除关联的信息节点和话术数据。',
                        okText: '删除',
                        okType: 'danger',
                        cancelText: '取消',
                        onOk: async () => {
                          if (!projectId) return
                          try {
                            await deleteTask(projectId, rec.task_id)
                            message.success('任务已删除')
                            if (projectId) fetchTasks(projectId)
                          } catch (err) {
                            message.error(err instanceof Error ? err.message : '删除失败')
                          }
                        },
                      })
                    }}
                  />
                </Space>
              ),
            },
          ]}
        />
        </>
      ),
    },
    {
      key: 'stats' as TabKey,
      label: (
        <Space>
          <BarChartOutlined />
          统计
        </Space>
      ),
      children: projectStatsLoading ? <Skeleton active /> : !projectStats ? <Empty description="暂无统计数据" /> : (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
          {/* 总览 — 全部字段 */}
          <Row gutter={16}>
            <Col xs={12} sm={8} md={4}><Statistic title="总调用次数" value={projectStats.stats.total_calls} /></Col>
            <Col xs={12} sm={8} md={4}><Statistic title="总 Token" value={projectStats.stats.total_tokens} groupSeparator="," /></Col>
            <Col xs={12} sm={8} md={4}><Statistic title="总费用" prefix="¥" value={projectStats.stats.total_cost_yuan} precision={4} /></Col>
            <Col xs={12} sm={8} md={4}><Statistic title="累计耗时" value={(projectStats.stats.total_duration_ms / 1000).toFixed(1)} suffix="s" /></Col>
          </Row>

          {/* 模型用量 — 表格展示全部字段 */}
          {Object.keys(projectStats.stats.by_model).length > 0 && (
            <Card size="small" title={<Space><BarChartOutlined /> 模型用量分布</Space>}>
              <Table
                dataSource={Object.entries(projectStats.stats.by_model).map(([model, ms]) => ({ model, ...ms }))}
                rowKey="model"
                size="small"
                pagination={false}
                columns={[
                  { title: '模型', dataIndex: 'model', key: 'model', render: (v: string) => <Tag>{v}</Tag> },
                  { title: '调用次数', dataIndex: 'calls', key: 'calls' },
                  { title: 'Token', dataIndex: 'total_tokens', key: 'tokens', render: (v: number) => v?.toLocaleString() },
                  { title: '费用', dataIndex: 'cost_yuan', key: 'cost_yuan', render: (v: number) => <Tag color="gold"><DollarOutlined /> ¥{v?.toFixed(4)}</Tag> },
                  { title: '占比', key: 'pct', width: 120, render: (_: unknown, rec: { calls: number }) => <Progress percent={projectStats.stats.total_calls > 0 ? Math.round((rec.calls / projectStats.stats.total_calls) * 100) : 0} size="small" format={p => `${p}%`} /> },
                ]}
              />
            </Card>
          )}

          {/* 任务用量列表 */}
          {projectStats.tasks && projectStats.tasks.length > 0 && (
            <Card size="small" title={<Space><ThunderboltOutlined /> 任务用量明细</Space>}>
              <Table
                dataSource={projectStats.tasks}
                rowKey="task_id"
                size="small"
                pagination={false}
                onRow={(rec) => ({ onClick: () => navigate(`/projects/${projectId}/tasks/${rec.task_id}`), style: { cursor: 'pointer' } })}
                columns={[
                  { title: '任务 ID', dataIndex: 'task_id', key: 'task_id', render: (v: string) => <Tag>{v}</Tag> },
                  { title: '调用次数', dataIndex: 'total_calls', key: 'calls' },
                  { title: 'Token', dataIndex: 'total_tokens', key: 'tokens', render: (v: number) => v?.toLocaleString() },
                  { title: '费用', dataIndex: 'total_cost_yuan', key: 'cost', render: (v: number) => `¥${v?.toFixed(4)}` },
                ]}
              />
            </Card>
          )}
        </div>
      ),
    },
  ]

  return (
    <div className="project-detail page-container fade-in">
      <div className="page-header slide-up">
        <div>
          <Title level={2} className="page-title">
            项目详情
          </Title>
          <Paragraph className="page-description">
            查看项目基本信息与更新时间
          </Paragraph>
        </div>
        <Space>
          <Button
            icon={<ArrowLeftOutlined />}
            onClick={() => navigate('/projects')}
            className="hover-float"
          >
            返回列表
          </Button>
          <Button
            icon={<RobotOutlined />}
            onClick={() => {
              if (!projectId) return
              const params = new URLSearchParams({ ref_project: projectId, label: project?.name || projectId })
              if (project?.description) params.set('desc', project.description)
              navigate(`/phishing?${params.toString()}`)
            }}
            className="hover-float"
            disabled={!project}
          >
            引用到 AI 中枢
          </Button>
          <Button
            icon={<EditOutlined />}
            onClick={handleEdit}
            className="hover-float"
            disabled={!project}
          >
            编辑
          </Button>
          <Button
            danger
            icon={<DeleteOutlined />}
            onClick={handleDelete}
            className="hover-float"
            disabled={!project}
          >
            删除
          </Button>
          <Button
            type="primary"
            icon={<RocketOutlined />}
            onClick={handleAddTagging}
            className="hover-float"
          >
            执行打标
          </Button>
          <Button
            type="primary"
            icon={<ThunderboltOutlined />}
            onClick={handleOpenTaskModal}
            loading={taskDefaultsLoading}
            className="hover-float"
          >
            下发任务
          </Button>
        </Space>
      </div>

      <Card className="glass-card slide-up stagger-1">
        {loading ? (
          <Skeleton active />
        ) : error ? (
          <div>
            <Text type="danger">{error}</Text>
          </div>
        ) : project ? (
          <>
            <div className="project-detail-header">
              <Title level={3} className="project-detail-title">{project.name}</Title>
              <div className="project-detail-tags">
                {tags.map((t) => (
                  <Tag key={t} color={stringToColor(t)}>
                    {t}
                  </Tag>
                ))}
              </div>
            </div>

            <div className="project-detail-section-title slide-up stagger-2">
              <Space><FileTextOutlined /> 基本信息</Space>
            </div>
            <div className="project-info-container slide-up stagger-2">
              <Descriptions
                bordered
                column={{ xxl: 2, xl: 2, lg: 2, md: 1, sm: 1, xs: 1 }}
                className="project-detail-descriptions"
              >
                <Descriptions.Item label="项目 ID">{project.id}</Descriptions.Item>
                <Descriptions.Item label="创建时间">
                  {formatDate(project.created_at)}
                </Descriptions.Item>
                <Descriptions.Item label="更新时间">
                  {formatDate(project.updated_at)}
                </Descriptions.Item>
                <Descriptions.Item label="描述">
                  {project.description || <Text type="secondary">暂无项目描述信息</Text>}
                </Descriptions.Item>
              </Descriptions>
            </div>

            {/* 简易看板 */}
            {renderMiniDashboard()}

            {/* Tab 切换：网站 / 小红书 */}
            <div className="project-detail-tabs slide-up stagger-3">
              <Tabs
                activeKey={activeTab}
                onChange={(key) => setActiveTab(key as TabKey)}
                items={tabItems}
                className="detail-tabs"
              />
            </div>

            <Modal
              title={mobilePreview ? `手机截图 ${mobilePreview.screenshot_id}` : '手机截图'}
              open={Boolean(mobilePreview)}
              footer={null}
              onCancel={() => setMobilePreview(null)}
              width={520}
              destroyOnHidden
            >
              {mobilePreview && (
                <div className="mobile-shot-preview">
                  <MobileScreenshotImage screenshot={mobilePreview} variant="preview" />
                  <Descriptions size="small" bordered column={1}>
                    <Descriptions.Item label="设备">{mobilePreview.device_id || '-'}</Descriptions.Item>
                    <Descriptions.Item label="联系人">{mobilePreview.contact_id || '-'}</Descriptions.Item>
                    <Descriptions.Item label="来源">{mobilePreview.source || '-'}</Descriptions.Item>
                    <Descriptions.Item label="时间">{formatDate(mobilePreview.created_at)}</Descriptions.Item>
                  </Descriptions>
                </div>
              )}
            </Modal>

            <Modal
              title="执行 Web Tagging"
              open={isTaggingModalOpen}
              onOk={handleTaggingSubmit}
              onCancel={() => setIsTaggingModalOpen(false)}
              confirmLoading={taggingSubmitting}
              destroyOnHidden
              className="project-modal"
            >
              <Form form={taggingForm} layout="vertical" initialValues={{ type: 'url' }}>
                <Form.Item name="type" label="输入类型">
                  <Select options={[
                    { label: 'URL 地址', value: 'url' },
                    { label: '公司名称', value: 'company' },
                  ]} />
                </Form.Item>
                <Form.Item
                  name="value"
                  label="输入内容"
                  rules={[{ required: true, message: '请输入内容' }]}
                >
                  <Input placeholder="https://... 或 公司名称" />
                </Form.Item>
                <div style={{ padding: '8px', background: 'var(--accent-color)', borderRadius: '4px', fontSize: '12px', color: 'var(--text-tertiary)' }}>
                  <InfoCircleOutlined /> 提交后后台将启动 Agent 进行自动化打标，可能需要数秒时间。
                </div>
              </Form>
            </Modal>

            <Modal
              title="编辑项目"
              open={isEditModalOpen}
              onOk={handleEditSubmit}
              onCancel={() => setIsEditModalOpen(false)}
              confirmLoading={editSubmitting}
              destroyOnHidden
              className="project-modal"
            >
              <Form form={editForm} layout="vertical">
                <Form.Item
                  name="name"
                  label="项目名称"
                  rules={[{ required: true, message: '请输入项目名称' }]}
                >
                  <Input placeholder="请输入项目名称" />
                </Form.Item>
                <Form.Item name="description" label="项目描述">
                  <Input.TextArea placeholder="可选" rows={4} />
                </Form.Item>
              </Form>
            </Modal>
          
            {/* 小红书搜索任务 Modal */}
            <Modal
              title="新建小红书搜索任务"
              open={isXhsSearchModalOpen}
              onOk={handleXhsSearchSubmit}
              onCancel={() => setIsXhsSearchModalOpen(false)}
              confirmLoading={xhsSearchSubmitting}
              destroyOnHidden
              className="project-modal"
            >
              <Form form={xhsSearchForm} layout="vertical" initialValues={{ max_notes: 20, attention_threshold: 60 }}>
                <Form.Item
                  name="keyword"
                  label="搜索关键词"
                  rules={[{ required: true, message: '请输入搜索关键词' }]}
                >
                  <Input placeholder="如公司名、产品名等" />
                </Form.Item>
                <Form.Item name="max_notes" label="最大笔记数">
                  <Select options={[
                    { label: '10 条', value: 10 },
                    { label: '20 条', value: 20 },
                    { label: '50 条', value: 50 },
                    { label: '100 条', value: 100 },
                  ]} />
                </Form.Item>
                <Form.Item name="attention_threshold" label="关注度阈值">
                  <Select options={[
                    { label: '低 (40)', value: 40 },
                    { label: '中 (60)', value: 60 },
                    { label: '高 (80)', value: 80 },
                  ]} />
                </Form.Item>
                <div style={{ padding: '8px', background: 'var(--accent-color)', borderRadius: '4px', fontSize: '12px', color: 'var(--text-tertiary)' }}>
                  <InfoCircleOutlined /> 提交后后台将启动小红书采集任务，包括搜索、笔记分析和人物画像生成。
                </div>
              </Form>
            </Modal>

            {/* 人物画像生成 Drawer */}
            <ProfileDrawer
              open={isProfileDrawerOpen}
              onClose={() => setIsProfileDrawerOpen(false)}
              projectId={projectId || ''}
              onSuccess={() => {
                if (projectId) {
                  fetchXhsProfiles(projectId)
                }
              }}
            />

            {/* 任务下发 Modal */}
            <Modal
              title="下发任务"
              open={isTaskModalOpen}
              onOk={async () => {
                if (!projectId) return
                try {
                  const values = await taskForm.validateFields()
                  setTaskSubmitting(true)
                  const taskType = values.task_type as TaskType
                  const params: Record<string, unknown> = {}
                  if (taskType === 'company_scan') {
                    params.company_name = values.company_name
                    if (values.urls) {
                      const urlList = values.urls.split('\n').map((u: string) => u.trim()).filter(Boolean)
                      if (urlList.length > 0) params.urls = urlList
                    }
                    params.enable_url_scan = values.enable_url_scan ?? true
                    params.enable_asset_discovery = values.enable_asset_discovery ?? true
                    params.enable_xhs = values.enable_xhs ?? true
                    params.enable_wechat = values.enable_wechat ?? false
                    if (values.enable_wechat) params.wechat_device_id = values.wechat_device_id
                    params.enable_copywriting = values.enable_copywriting ?? true
                    params.enable_control_structure = values.enable_control_structure ?? true
                    params.incremental_scan = values.asset_scan_mode === 'incremental'
                    if (values.xhs_max_notes) params.xhs_max_notes = values.xhs_max_notes
                    if (values.min_attention_score != null) params.min_attention_score = values.min_attention_score
                    if (values.fofa_size) params.fofa_size = values.fofa_size
                    if (values.hunter_size) params.hunter_size = values.hunter_size
                    if (values.asset_probe_concurrency) params.asset_probe_concurrency = values.asset_probe_concurrency
                    if (values.url_probe_concurrency) params.url_probe_concurrency = values.url_probe_concurrency
                    if (values.url_scan_concurrency) params.url_scan_concurrency = values.url_scan_concurrency
                    if (values.copywriting_concurrency) params.copywriting_concurrency = values.copywriting_concurrency
                    if (values.xhs_search_concurrency) params.xhs_search_concurrency = values.xhs_search_concurrency
                    if (values.control_max_entities) params.control_max_entities = values.control_max_entities
                    if (values.control_lookup_concurrency) params.control_lookup_concurrency = values.control_lookup_concurrency
                    if (values.control_icp_concurrency) params.control_icp_concurrency = values.control_icp_concurrency
                    if (values.control_scan_concurrency) params.control_scan_concurrency = values.control_scan_concurrency
                  }
                  if (taskType === 'url_scan') {
                    if (values.urls) {
                      params.urls = values.urls.split('\n').map((u: string) => u.trim()).filter(Boolean)
                    }
                    params.enable_copywriting = values.enable_copywriting ?? true
                    if (values.url_probe_concurrency) params.url_probe_concurrency = values.url_probe_concurrency
                    if (values.url_scan_concurrency) params.url_scan_concurrency = values.url_scan_concurrency
                    if (values.copywriting_concurrency) params.copywriting_concurrency = values.copywriting_concurrency
                  }
                  if (taskType === 'xhs_search') {
                    params.keyword = values.keyword
                    if (values.max_notes) params.max_notes = values.max_notes
                    if (values.attention_threshold) params.attention_threshold = values.attention_threshold
                  }
                  if (taskType === 'douyin_search') {
                    params.keyword = values.keyword
                    if (values.max_videos) params.max_videos = values.max_videos
                  }
                  if (taskType === 'web_tagging') {
                    params.company_name = values.company_name
                  }
                  if (taskType === 'fofa_collect') {
                    params.company_name = values.company_name
                    params.enable_scan = values.enable_scan ?? true
                    params.incremental_scan = values.asset_scan_mode === 'incremental'
                    if (values.fofa_size) params.fofa_size = values.fofa_size
                    if (values.hunter_size) params.hunter_size = values.hunter_size
                    if (values.probe_concurrency) params.probe_concurrency = values.probe_concurrency
                    if (values.url_probe_concurrency) params.url_probe_concurrency = values.url_probe_concurrency
                    if (values.url_scan_concurrency) params.url_scan_concurrency = values.url_scan_concurrency
                    if (values.copywriting_concurrency) params.copywriting_concurrency = values.copywriting_concurrency
                    if (values.min_attention_score != null) params.min_attention_score = values.min_attention_score
                  }
                  if (taskType === 'scholar_contact') {
                    params.unit = values.unit
                    params.direction = values.direction
                    if (values.unit_en) params.unit_en = values.unit_en
                    if (values.limit != null) params.limit = values.limit
                    params.enable_chrome_pmc = values.enable_chrome_pmc ?? false
                    params.dry_run = values.dry_run ?? false
                  }
                  const result = await createTask(projectId, { task_type: taskType, params })
                  message.success('任务已下发')
                  setIsTaskModalOpen(false)
                  taskForm.resetFields()
                  // 刷新任务列表
                  fetchTasks(projectId)
                  // 跳转到任务详情
                  navigate(`/projects/${projectId}/tasks/${result.task_id}`)
                } catch (e) {
                  if (e instanceof Error && e.message) message.error(e.message)
                } finally {
                  setTaskSubmitting(false)
                }
              }}
              onCancel={() => setIsTaskModalOpen(false)}
              afterOpenChange={(open) => {
                if (!open) return
                taskForm.resetFields()
                taskForm.setFieldsValue(taskTuningValues)
              }}
              confirmLoading={taskSubmitting}
              destroyOnHidden
              width={640}
              className="project-modal"
            >
              <Form form={taskForm} layout="vertical" initialValues={{ task_type: 'company_scan', asset_scan_mode: 'full', enable_asset_discovery: true, enable_url_scan: true, enable_xhs: true, enable_wechat: false, enable_copywriting: true, enable_control_structure: true, enable_scan: true, xhs_max_notes: 20, min_attention_score: 40, fofa_size: 200, hunter_size: 200, control_max_entities: 100, control_lookup_concurrency: 4, control_icp_concurrency: 6, control_scan_concurrency: 4, ...TASK_TUNING_FORM_DEFAULTS }}>
                <Form.Item name="task_type" label="任务类型" rules={[{ required: true }]}>
                  <Select options={[
                    { label: '综合公司扫描', value: 'company_scan' },
                    { label: 'URL 扫描 + 话术生成', value: 'url_scan' },
                    { label: '小红书搜索', value: 'xhs_search' },
                    { label: '抖音搜索', value: 'douyin_search' },
                    { label: '官网打标', value: 'web_tagging' },
                    { label: 'FOFA + Hunter 资产采集', value: 'fofa_collect' },
                    { label: '学者联系发现', value: 'scholar_contact' },
                  ]} />
                </Form.Item>
                <Form.Item noStyle shouldUpdate={(prev, cur) => prev.task_type !== cur.task_type}>
                  {({ getFieldValue }) => {
                    const taskType = getFieldValue('task_type')
                    if (taskType === 'company_scan') return (
                      <>
                        <Form.Item name="company_name" label="公司名称" rules={[{ required: true, message: '请输入公司名称' }]}>
                          <Input placeholder="如：字节跳动" />
                        </Form.Item>
                        <Form.Item name="urls" label="URL 列表（可选）">
                          <Input.TextArea rows={3} placeholder="每行一个 URL；留空时由公司身份、FOFA 和 Hunter 自动发现" />
                        </Form.Item>
                        <Form.Item label="扫描模块">
                          <Space orientation="vertical">
                            <Form.Item name="enable_asset_discovery" valuePropName="checked" noStyle>
                              <Checkbox>公司标准化 + FOFA/Hunter 资产发现与存活去重</Checkbox>
                            </Form.Item>
                            <Form.Item name="enable_control_structure" valuePropName="checked" noStyle>
                              <Checkbox title="仅使用天眼查实际控制权 ID 747，未授权时明确标记不可用">天眼查控股结构：第一层 100% 控股单位 + ICP 域名</Checkbox>
                            </Form.Item>
                            <Form.Item name="enable_url_scan" valuePropName="checked" noStyle>
                              <Checkbox>URL 扫描（探活 + 信息提取）</Checkbox>
                            </Form.Item>
                            <Form.Item name="enable_xhs" valuePropName="checked" noStyle>
                              <Checkbox>小红书爬取</Checkbox>
                            </Form.Item>
                            <Form.Item name="enable_wechat" valuePropName="checked" noStyle>
                              <Checkbox>微信公众号采集（默认关闭）</Checkbox>
                            </Form.Item>
                            <Form.Item name="enable_copywriting" valuePropName="checked" noStyle>
                              <Checkbox>话术生成</Checkbox>
                            </Form.Item>
                          </Space>
                        </Form.Item>
                        <Form.Item noStyle shouldUpdate={(prev, cur) => prev.enable_wechat !== cur.enable_wechat}>
                          {({ getFieldValue }) => getFieldValue('enable_wechat') ? (
                            <Form.Item
                              name="wechat_device_id"
                              label="公众号执行手机"
                              rules={[{ required: true, message: '请选择执行公众号采集的手机' }]}
                              extra="任务会通过 ADB 打开微信；手机用于搜索文章，原文与图片由 Chrome 继续读取。"
                            >
                              <Select
                                placeholder={wechatDeviceOptions.length ? '选择手机型号' : '当前项目没有可用的微信采集手机'}
                                options={wechatDeviceOptions.map((device) => ({
                                  value: device.deviceId,
                                  label: `${device.model} · ${device.online ? '在线' : '离线'}`,
                                  disabled: !device.online,
                                }))}
                              />
                            </Form.Item>
                          ) : null}
                        </Form.Item>
                        <Form.Item name="asset_scan_mode" label="资产深扫范围">
                          <Segmented block options={[
                            { label: '全量扫描', value: 'full' },
                            { label: '增量扫描', value: 'incremental' },
                          ]} />
                        </Form.Item>
                        <Row gutter={16}>
                          <Col xs={24} sm={6}>
                            <Form.Item name="control_max_entities" label="控股单位上限">
                              <InputNumber min={1} max={500} style={{ width: '100%' }} />
                            </Form.Item>
                          </Col>
                          <Col xs={24} sm={6}>
                            <Form.Item name="control_lookup_concurrency" label="控股查询并发">
                              <InputNumber min={1} max={12} style={{ width: '100%' }} />
                            </Form.Item>
                          </Col>
                          <Col xs={24} sm={6}>
                            <Form.Item name="control_icp_concurrency" label="ICP 查询并发">
                              <InputNumber min={1} max={20} style={{ width: '100%' }} />
                            </Form.Item>
                          </Col>
                          <Col xs={24} sm={6}>
                            <Form.Item name="control_scan_concurrency" label="控股采集并发">
                              <InputNumber min={1} max={12} style={{ width: '100%' }} />
                            </Form.Item>
                          </Col>
                        </Row>
                        <Row gutter={16}>
                          <Col xs={24} sm={8}>
                            <Form.Item name="fofa_size" label="FOFA 单路条数">
                              <InputNumber min={1} max={2000} style={{ width: '100%' }} />
                            </Form.Item>
                          </Col>
                          <Col xs={24} sm={8}>
                            <Form.Item name="hunter_size" label="Hunter 单路条数">
                              <InputNumber min={1} max={2000} style={{ width: '100%' }} />
                            </Form.Item>
                          </Col>
                          <Col xs={24} sm={8}>
                            <Form.Item name="asset_probe_concurrency" label="资产探活并发">
                              <InputNumber min={1} max={128} style={{ width: '100%' }} />
                            </Form.Item>
                          </Col>
                        </Row>
                        <Row gutter={16}>
                          <Col xs={24} sm={6}>
                            <Form.Item name="url_probe_concurrency" label="URL 探活并发">
                              <InputNumber min={1} max={128} style={{ width: '100%' }} />
                            </Form.Item>
                          </Col>
                          <Col xs={24} sm={6}>
                            <Form.Item name="url_scan_concurrency" label="浏览器深扫并发">
                              <InputNumber min={1} max={16} style={{ width: '100%' }} />
                            </Form.Item>
                          </Col>
                          <Col xs={24} sm={6}>
                            <Form.Item name="copywriting_concurrency" label="话术生成并发">
                              <InputNumber min={1} max={12} style={{ width: '100%' }} />
                            </Form.Item>
                          </Col>
                          <Col xs={24} sm={6}>
                            <Form.Item name="xhs_search_concurrency" label="小红书搜索并发">
                              <InputNumber min={1} max={8} style={{ width: '100%' }} />
                            </Form.Item>
                          </Col>
                        </Row>
                        <Row gutter={16}>
                          <Col xs={24} sm={12}>
                            <Form.Item name="xhs_max_notes" label="小红书最大笔记数">
                              <InputNumber min={1} max={100} style={{ width: '100%' }} />
                            </Form.Item>
                          </Col>
                          <Col xs={24} sm={12}>
                            <Form.Item name="min_attention_score" label="最低关注度阈值">
                              <InputNumber min={0} max={100} style={{ width: '100%' }} />
                            </Form.Item>
                          </Col>
                        </Row>
                      </>
                    )
                    if (taskType === 'url_scan') return (
                      <>
                        <Form.Item name="urls" label="URL 列表" rules={[{ required: true, message: '请输入URL' }]}>
                          <Input.TextArea rows={3} placeholder="每行一个 URL" />
                        </Form.Item>
                        <Form.Item name="enable_copywriting" valuePropName="checked">
                          <Checkbox>生成话术</Checkbox>
                        </Form.Item>
                        <Row gutter={16}>
                          <Col xs={24} sm={8}>
                            <Form.Item name="url_probe_concurrency" label="URL 探活并发">
                              <InputNumber min={1} max={128} style={{ width: '100%' }} />
                            </Form.Item>
                          </Col>
                          <Col xs={24} sm={8}>
                            <Form.Item name="url_scan_concurrency" label="浏览器深扫并发">
                              <InputNumber min={1} max={16} style={{ width: '100%' }} />
                            </Form.Item>
                          </Col>
                          <Col xs={24} sm={8}>
                            <Form.Item name="copywriting_concurrency" label="话术生成并发">
                              <InputNumber min={1} max={12} style={{ width: '100%' }} />
                            </Form.Item>
                          </Col>
                        </Row>
                      </>
                    )
                    if (taskType === 'xhs_search') return (
                      <Form.Item name="keyword" label="搜索关键词" rules={[{ required: true }]}>
                        <Input placeholder="公司名+职位，如：字节跳动 产品经理" />
                      </Form.Item>
                    )
                    if (taskType === 'douyin_search') return (
                      <Form.Item name="keyword" label="搜索关键词" rules={[{ required: true }]}>
                        <Input placeholder="搜索关键词" />
                      </Form.Item>
                    )
                    if (taskType === 'web_tagging') return (
                      <Form.Item name="company_name" label="公司名称" rules={[{ required: true }]}>
                        <Input placeholder="ICP 备案名" />
                      </Form.Item>
                    )
                    if (taskType === 'fofa_collect') return (
                      <>
                        <Form.Item name="company_name" label="公司名称" rules={[{ required: true, message: '请输入公司名称' }]}>
                          <Input placeholder="支持法定名、品牌名和简称，如：B站" />
                        </Form.Item>
                        <Form.Item name="enable_scan" valuePropName="checked" noStyle>
                          <Checkbox>对存活资产做深度扫描</Checkbox>
                        </Form.Item>
                        <Form.Item name="asset_scan_mode" label="资产深扫范围" style={{ marginTop: 12 }}>
                          <Segmented block options={[
                            { label: '全量扫描', value: 'full' },
                            { label: '增量扫描', value: 'incremental' },
                          ]} />
                        </Form.Item>
                        <Row gutter={16} style={{ marginTop: 12 }}>
                          <Col xs={24} sm={8}>
                            <Form.Item name="fofa_size" label="FOFA 单路最大条数">
                              <InputNumber min={1} max={2000} style={{ width: '100%' }} />
                            </Form.Item>
                          </Col>
                          <Col xs={24} sm={8}>
                            <Form.Item name="hunter_size" label="Hunter 单路最大条数">
                              <InputNumber min={1} max={2000} style={{ width: '100%' }} />
                            </Form.Item>
                          </Col>
                          <Col xs={24} sm={8}>
                            <Form.Item name="min_attention_score" label="最低关注度阈值">
                              <InputNumber min={0} max={100} style={{ width: '100%' }} />
                            </Form.Item>
                          </Col>
                        </Row>
                        <Row gutter={16}>
                          <Col xs={24} sm={6}>
                            <Form.Item name="probe_concurrency" label="资产探活并发">
                              <InputNumber min={1} max={128} style={{ width: '100%' }} />
                            </Form.Item>
                          </Col>
                          <Col xs={24} sm={6}>
                            <Form.Item name="url_probe_concurrency" label="URL 补充探活并发">
                              <InputNumber min={1} max={128} style={{ width: '100%' }} />
                            </Form.Item>
                          </Col>
                          <Col xs={24} sm={6}>
                            <Form.Item name="url_scan_concurrency" label="浏览器深扫并发">
                              <InputNumber min={1} max={16} style={{ width: '100%' }} />
                            </Form.Item>
                          </Col>
                          <Col xs={24} sm={6}>
                            <Form.Item name="copywriting_concurrency" label="话术生成并发">
                              <InputNumber min={1} max={12} style={{ width: '100%' }} />
                            </Form.Item>
                          </Col>
                        </Row>
                      </>
                    )
                    if (taskType === 'scholar_contact') return (
                      <>
                        <Form.Item name="unit" label="单位名称" rules={[{ required: true, message: '请输入单位名称' }]}>
                          <Input placeholder="如：中山大学附属第一医院（支持中文，用于 OpenAlex 机构解析）" />
                        </Form.Item>
                        <Form.Item name="direction" label="研究方向" rules={[{ required: true, message: '请输入研究方向' }]}>
                          <Input placeholder="如：nasopharyngeal carcinoma（建议英文关键词，命中更多）" />
                        </Form.Item>
                        <Form.Item name="unit_en" label="单位英文名（可选）">
                          <Input placeholder="如：Sun Yat-sen（用于 PubMed/EuropePMC 检索，留空回退单位名）" />
                        </Form.Item>
                        <Form.Item name="limit" label="OpenAlex 返回文章数">
                          <InputNumber min={1} max={50} style={{ width: '100%' }} />
                        </Form.Item>
                        <Space orientation="vertical">
                          <Form.Item name="enable_chrome_pmc" valuePropName="checked" noStyle>
                            <Checkbox>用 Chrome 打开 PMC 全文补抽通讯邮箱（较慢，默认关闭）</Checkbox>
                          </Form.Item>
                          <Form.Item name="dry_run" valuePropName="checked" noStyle>
                            <Checkbox>试运行（只跑通不入库，用于验证命中量）</Checkbox>
                          </Form.Item>
                        </Space>
                        <div style={{ marginTop: 8, padding: '8px', background: 'var(--accent-color)', borderRadius: '4px', fontSize: '12px', color: 'var(--text-tertiary)' }}>
                          <InfoCircleOutlined /> 仅抽取公开学术源中按文章绑定的通讯/联系邮箱，用于一对一学术合作；不导出整单位名单，不含个人电话。
                        </div>
                      </>
                    )
                    return null
                  }}
                </Form.Item>
              </Form>
            </Modal>

            {/* Reason 弹窗 */}
            <Modal
              title="笔记分析详情"
              open={isReasonModalOpen}
              onCancel={() => setIsReasonModalOpen(false)}
              footer={null}
              width={600}
            >
              {currentReasonNote?.tagging && (
                <div className="xhs-reason-modal-content">
                  <div className="xhs-reason-scores">
                    <div className="xhs-reason-score-item">
                      <span className="xhs-reason-label">关联度</span>
                      <Tag color={(currentReasonNote.tagging.keyword_relevance || 0) >= 70 ? 'error' : 'processing'}>
                        {currentReasonNote.tagging.keyword_relevance}
                      </Tag>
                    </div>
                    <div className="xhs-reason-score-item">
                      <span className="xhs-reason-label">关注度</span>
                      <Tag color={(currentReasonNote.tagging.attention_score || 0) >= 70 ? 'error' : 'warning'}>
                        {currentReasonNote.tagging.attention_score}
                      </Tag>
                    </div>
                    {currentReasonNote.tagging.company_mentioned && (
                      <div className="xhs-reason-score-item">
                        <span className="xhs-reason-label">公司</span>
                        <Tag color="blue">{currentReasonNote.tagging.company_mentioned}</Tag>
                      </div>
                    )}
                  </div>

                  {currentReasonNote.tagging.relevance_reason && (
                    <div className="xhs-reason-section">
                      <div className="xhs-reason-section-title">关联度分析</div>
                      <div className="xhs-reason-section-content">{currentReasonNote.tagging.relevance_reason}</div>
                    </div>
                  )}

                  {currentReasonNote.tagging.reason && (
                    <div className="xhs-reason-section">
                      <div className="xhs-reason-section-title">风险原因</div>
                      <div className="xhs-reason-section-content">{currentReasonNote.tagging.reason}</div>
                    </div>
                  )}

                  {currentReasonNote.tagging.evidence && (
                    <div className="xhs-reason-section">
                      <div className="xhs-reason-section-title">证据</div>
                      <div className="xhs-reason-section-content">{currentReasonNote.tagging.evidence}</div>
                    </div>
                  )}

                  {currentReasonNote.tagging.attack_surface_types && currentReasonNote.tagging.attack_surface_types.length > 0 && (
                    <div className="xhs-reason-section">
                      <div className="xhs-reason-section-title">攻击面类型</div>
                      <Space size={4} wrap>
                        {currentReasonNote.tagging.attack_surface_types.map((t, i) => (
                          <Tag key={i} color="volcano">{mapAttackSurfaceType(t)}</Tag>
                        ))}
                      </Space>
                    </div>
                  )}

                  {currentReasonNote.tagging.key_info_extracted && currentReasonNote.tagging.key_info_extracted.length > 0 && (
                    <div className="xhs-reason-section">
                      <div className="xhs-reason-section-title">提取的关键信息</div>
                      <Space size={4} wrap>
                        {currentReasonNote.tagging.key_info_extracted.map((info, i) => (
                          <Tag key={i} color="cyan">{info}</Tag>
                        ))}
                      </Space>
                    </div>
                  )}
                </div>
              )}
            </Modal>

            {/* 人物画像详情 Modal */}
            <Modal
              title={
                <Space>
                  <UserOutlined />
                  <span>{currentProfile?.nickname || '用户'} - 人物画像</span>
                </Space>
              }
              open={isProfileDetailModalOpen}
              onCancel={() => setIsProfileDetailModalOpen(false)}
              footer={null}
              width="80vw"
              style={{ maxWidth: 1400 }}
              className="xhs-profile-detail-modal"
              destroyOnHidden
            >
              {currentProfile && renderProfileDetailContent(currentProfile)}
            </Modal>

            {/* 抖音用户画像详情 Modal */}
            <Modal
              title={
                <Space>
                  <UserOutlined />
                  <span>{currentDouyinProfile?.nickname || '用户'} - 抖音画像</span>
                </Space>
              }
              open={isDouyinProfileModalOpen}
              onCancel={() => setIsDouyinProfileModalOpen(false)}
              footer={null}
              width={700}
              className="douyin-profile-detail-modal"
              destroyOnHidden
            >
              {currentDouyinProfile && (
                <div className="douyin-profile-detail-content">
                  <Descriptions size="small" column={2} bordered>
                    <Descriptions.Item label="用户昵称">{currentDouyinProfile.nickname}</Descriptions.Item>
                    <Descriptions.Item label="用户 ID">{currentDouyinProfile.user_id}</Descriptions.Item>
                    <Descriptions.Item label="Sec UID" span={2}>
                      <Text copyable style={{ fontSize: 12 }}>{currentDouyinProfile.sec_uid}</Text>
                    </Descriptions.Item>
                    <Descriptions.Item label="主页链接" span={2}>
                      <a href={currentDouyinProfile.user_profile_url} target="_blank" rel="noopener noreferrer">
                        {currentDouyinProfile.user_profile_url}
                      </a>
                    </Descriptions.Item>
                    <Descriptions.Item label="置信度">
                      {currentDouyinProfile.confidence ? (
                        <Tag color={currentDouyinProfile.confidence === 'high' ? 'green' : currentDouyinProfile.confidence === 'medium' ? 'orange' : 'default'}>
                          {confidenceMap[currentDouyinProfile.confidence] || currentDouyinProfile.confidence}
                        </Tag>
                      ) : '-'}
                    </Descriptions.Item>
                    <Descriptions.Item label="优先级">
                      {currentDouyinProfile.priority ? (
                        <Tag color={currentDouyinProfile.priority >= 8 ? 'red' : currentDouyinProfile.priority >= 5 ? 'orange' : 'default'}>
                          {currentDouyinProfile.priority}
                        </Tag>
                      ) : '-'}
                    </Descriptions.Item>
                    <Descriptions.Item label="示例作品" span={2}>
                      {currentDouyinProfile.sample_title || '-'}
                    </Descriptions.Item>
                    <Descriptions.Item label="打标理由" span={2}>
                      {currentDouyinProfile.tag_reason || '-'}
                    </Descriptions.Item>
                    {currentDouyinProfile.vision_analysis && (
                      <Descriptions.Item label="视觉分析" span={2}>
                        <pre style={{ whiteSpace: 'pre-wrap', fontSize: 12, maxHeight: 300, overflow: 'auto' }}>
                          {currentDouyinProfile.vision_analysis}
                        </pre>
                      </Descriptions.Item>
                    )}
                  </Descriptions>
                </div>
              )}
            </Modal>

            {/* 笔记详情 Modal */}
            <Modal
              title={
                <Space>
                  <InfoCircleOutlined />
                  <span>笔记详情</span>
                  {currentNote && (
                    <a 
                      href={buildNoteUrl(currentNote)} 
                      target="_blank" 
                      rel="noopener noreferrer"
                      style={{ fontSize: 12, fontWeight: 'normal' }}
                    >
                      <LinkOutlined /> 查看原文
                    </a>
                  )}
                </Space>
              }
              open={isNoteDetailModalOpen}
              onCancel={() => setIsNoteDetailModalOpen(false)}
              footer={null}
              width={800}
              className="xhs-note-detail-modal"
            >
              {noteDetailLoading ? (
                <div style={{ textAlign: 'center', padding: 40 }}>
                  <Spin size="large" />
                  <div style={{ marginTop: 16 }}>加载中...</div>
                </div>
              ) : currentNoteDetail ? (
                <div className="xhs-note-detail-content">
                  {/* 笔记基本信息 */}
                  {currentNote && (
                    <div className="xhs-note-detail-header">
                      <Space>
                        <Avatar src={currentNote.user?.avatar} icon={<UserOutlined />} />
                        <div>
                          <Text strong>{currentNote.user?.nickname}</Text>
                          <div>
                            <Text type="secondary" style={{ fontSize: 12 }}>{currentNote.title}</Text>
                          </div>
                        </div>
                      </Space>
                      {currentNote.liked_count && (
                        <Tag>❤️ {currentNote.liked_count}</Tag>
                      )}
                    </div>
                  )}

                  {/* 笔记内容 */}
                  <div className="xhs-note-detail-body">
                    <div className="xhs-note-detail-section">
                      <div className="xhs-note-detail-section-title">📝 笔记内容</div>
                      <div className="xhs-note-content-text">
                        {currentNoteDetail.content 
                          ? parseHashtags(currentNoteDetail.content)
                          : '-'
                        }
                      </div>
                    </div>

                    {/* 评论摘要 */}
                    {currentNoteDetail.comments_summary && (
                      <div className="xhs-note-detail-section">
                        <div className="xhs-note-detail-section-title">💬 评论摘要</div>
                        <Paragraph type="secondary">
                          {currentNoteDetail.comments_summary}
                        </Paragraph>
                      </div>
                    )}

                    {/* 打标结果 */}
                    <div className="xhs-note-detail-section">
                      <div className="xhs-note-detail-section-title">🏷️ 分析结果</div>
                      <Descriptions size="small" column={2} bordered>
                        <Descriptions.Item label="关联度">
                          <Progress
                            percent={currentNoteDetail.tagging?.keyword_relevance || 0}
                            size="small"
                            style={{ width: 100 }}
                          />
                        </Descriptions.Item>
                        <Descriptions.Item label="关注度">
                          <Tag color={(currentNoteDetail.tagging?.attention_score || 0) >= 70 ? 'error' : (currentNoteDetail.tagging?.attention_score || 0) >= 40 ? 'warning' : 'processing'}>
                            {currentNoteDetail.tagging?.attention_score || 0}
                          </Tag>
                        </Descriptions.Item>
                        <Descriptions.Item label="关键词分析" span={2}>
                          {currentNoteDetail.tagging?.keyword_analysis || '-'}
                        </Descriptions.Item>
                        {currentNoteDetail.tagging?.company_identified && (
                          <Descriptions.Item label="识别公司" span={2}>
                            <Space>
                              <Tag color="blue">{currentNoteDetail.tagging.company_identified.name}</Tag>
                              <Tag>{currentNoteDetail.tagging.company_identified.confidence}</Tag>
                            </Space>
                            {currentNoteDetail.tagging.company_identified.evidence && (
                              <div style={{ marginTop: 4 }}>
                                <Text type="secondary" style={{ fontSize: 12 }}>
                                  证据：{currentNoteDetail.tagging.company_identified.evidence}
                                </Text>
                              </div>
                            )}
                          </Descriptions.Item>
                        )}
                        {currentNoteDetail.tagging?.summary && (
                          <Descriptions.Item label="摘要" span={2}>
                            {currentNoteDetail.tagging.summary}
                          </Descriptions.Item>
                        )}
                      </Descriptions>
                    </div>

                    {/* 发现列表 */}
                    {currentNoteDetail.tagging?.findings && currentNoteDetail.tagging.findings.length > 0 && (
                      <div className="xhs-note-detail-section">
                        <div className="xhs-note-detail-section-title">
                          🔍 发现 <Tag color="red">{currentNoteDetail.tagging.findings.length}</Tag>
                        </div>
                        <div className="xhs-note-findings-list">
                          {currentNoteDetail.tagging.findings.map((finding, i) => (
                            <div key={i} className="xhs-note-finding-item">
                              <div className="xhs-note-finding-header">
                                <Tag color="orange">{finding.type}</Tag>
                                {renderFindingValue(finding.value, { copyable: true })}
                              </div>
                              <div className="xhs-note-finding-evidence">
                                <Text type="secondary">证据：{finding.evidence}</Text>
                              </div>
                              <div className="xhs-note-finding-reason">
                                <Text type="secondary">关注原因：{finding.attention_reason}</Text>
                              </div>
                            </div>
                          ))}
                        </div>
                      </div>
                    )}
                  </div>
                </div>
              ) : (
                <Empty description="暂无详情数据" />
              )}
            </Modal>
          </>
        ) : null}
      </Card>

      {/* 话术详情 Drawer */}
      <Drawer
        title={currentFindingLabel || '话术详情'}
        open={copywritingDrawerOpen}
        onClose={() => { setCopywritingDrawerOpen(false); setCurrentCopywriting(null) }}
        size="large"
        destroyOnHidden
        extra={
          <Button
            type="primary"
            icon={<RobotOutlined />}
            disabled={!currentFindingId}
            onClick={() => {
              const params = new URLSearchParams({ ref_finding: currentFindingId, label: currentFindingLabel || currentFindingId })
              navigate(`/phishing?${params.toString()}`)
            }}
          >
            引用到 AI 中枢
          </Button>
        }
      >
        {copywritingLoading ? <Skeleton active /> : currentCopywriting ? <CopywritingRenderer data={currentCopywriting} /> : null}
      </Drawer>

      {/* finding 人物画像 Drawer（从 AI 中枢引用跳转） */}
      <Drawer
        title={
          <Space>
            <UserOutlined />
            <span>{findingProfile?.nickname || '人物画像'}</span>
          </Space>
        }
        open={findingProfileOpen}
        onClose={() => { setFindingProfileOpen(false); setFindingProfile(null) }}
        size="large"
        destroyOnHidden
        extra={
          findingProfile?.finding_id ? (
            <Button
              type="primary"
              icon={<CopyOutlined />}
              onClick={() => handleViewCopywritingById(findingProfile.finding_id, findingProfile.nickname || '话术详情')}
            >
              查看话术
            </Button>
          ) : null
        }
      >
        {findingProfileLoading ? (
          <Skeleton active avatar paragraph={{ rows: 6 }} />
        ) : findingProfile ? (
          <Space orientation="vertical" size="large" style={{ width: '100%' }}>
            <Space align="center" size="middle">
              <Avatar size={56} src={findingProfile.avatar_url} icon={<UserOutlined />} />
              <div>
                <Typography.Title level={5} style={{ margin: 0 }}>
                  {findingProfile.nickname || '未知人物'}
                </Typography.Title>
                <Space size={4} wrap style={{ marginTop: 4 }}>
                  {findingProfile.platform && <Tag color="blue">{findingProfile.platform}</Tag>}
                  <Tag color={findingProfile.attention_score >= 70 ? 'error' : findingProfile.attention_score >= 40 ? 'warning' : 'processing'}>
                    关注度 {findingProfile.attention_score}
                  </Tag>
                  {typeof findingProfile.notes_count === 'number' && (
                    <Tag>笔记 {findingProfile.notes_count}</Tag>
                  )}
                </Space>
              </div>
            </Space>

            {findingProfile.tags?.length ? (
              <Space size={4} wrap>
                {findingProfile.tags.map((t, i) => <Tag key={i} color="geekblue">{t}</Tag>)}
              </Space>
            ) : null}

            <Descriptions size="small" column={1} bordered>
              {findingProfile.user_id && <Descriptions.Item label="用户 ID">{findingProfile.user_id}</Descriptions.Item>}
              {findingProfile.communication_style && <Descriptions.Item label="沟通风格">{findingProfile.communication_style}</Descriptions.Item>}
              {findingProfile.tone && <Descriptions.Item label="语气">{findingProfile.tone}</Descriptions.Item>}
              {findingProfile.reply_pattern && <Descriptions.Item label="回复习惯">{findingProfile.reply_pattern}</Descriptions.Item>}
            </Descriptions>

            {findingProfile.common_phrases?.length ? (
              <div>
                <Typography.Text strong>常用话术</Typography.Text>
                <Space size={4} wrap style={{ marginTop: 8, display: 'flex' }}>
                  {findingProfile.common_phrases.map((p, i) => <Tag key={i}>{p}</Tag>)}
                </Space>
              </div>
            ) : null}

            {findingProfile.risk_signals?.length ? (
              <div>
                <Typography.Text strong>风险信号</Typography.Text>
                <Space size={4} wrap style={{ marginTop: 8, display: 'flex' }}>
                  {findingProfile.risk_signals.map((p, i) => <Tag key={i} color="red">{p}</Tag>)}
                </Space>
              </div>
            ) : null}

            {(['identity', 'personality_profile', 'attack_surface'] as const).map((key) => {
              const label = key === 'identity' ? '身份画像' : key === 'personality_profile' ? '性格画像' : '攻击面'
              const obj = findingProfile[key]
              if (!obj || typeof obj !== 'object' || !Object.keys(obj).length) return null
              return (
                <div key={key}>
                  <Typography.Text strong>{label}</Typography.Text>
                  <pre style={{ whiteSpace: 'pre-wrap', fontSize: 12, marginTop: 8, background: 'var(--tertiary-bg, #f1f3f5)', padding: 12, borderRadius: 8, maxHeight: 260, overflow: 'auto' }}>
                    {JSON.stringify(obj, null, 2)}
                  </pre>
                </div>
              )
            })}
          </Space>
        ) : (
          <Empty description="暂无人物画像数据" />
        )}
      </Drawer>
    </div>
  )
}
