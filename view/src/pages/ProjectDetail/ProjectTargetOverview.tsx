import { Button, Empty, Space, Statistic, Table, Tag, Tooltip, Typography } from 'antd'
import type { ColumnsType } from 'antd/es/table'
import { ArrowRightOutlined, EyeOutlined, LinkOutlined, MailOutlined, PhoneOutlined, SyncOutlined, WarningOutlined } from '@ant-design/icons'
import { CopyableText } from '../../components/CopyLinkButton'
import { PortalResearchPanel } from '../../components/PortalResearch/PortalResearch'
import type { ProjectTargetDashboard, TargetDashboardContact, TargetDashboardFinding } from '../../services/sourceDocumentService'
import type { ProjectTargetTab } from '../../utils/targetRoutes'

const { Text } = Typography
type TargetModuleTab = Exclude<ProjectTargetTab, 'target'>
type CountKey = 'website_count' | 'xhs_count' | 'wechat_count' | 'bidding_count' | 'scholar_contact_count'
interface Props {
  projectId: string
  dashboard: ProjectTargetDashboard
  loading: boolean
  modules: ReadonlyArray<{ tab: TargetModuleTab; label: string; countKey: CountKey }>
  onOpenModule: (tab: TargetModuleTab) => void
  onViewContext: (findingId: string, label: string) => void
  onResearch: () => void
  onRefresh: () => void
}

export default function ProjectTargetOverview({ projectId, dashboard, loading, modules, onOpenModule, onViewContext, onResearch, onRefresh }: Props) {
  const summary = dashboard.target
  const openSource = (url?: string) => {
    if (url) window.open(url, '_blank', 'noopener,noreferrer')
  }
  const canOpenModule = (module: string): module is TargetModuleTab => (
    modules.some(item => item.tab === module)
  )
  const contactColumns: ColumnsType<TargetDashboardContact> = [
    {
      title: '联系方式',
      dataIndex: 'value',
      key: 'value',
      width: 210,
      render: (value: string, contact) => (
        <Space orientation="vertical" size={0}>
          <CopyableText
            value={value}
            className="target-contact-value"
            title={value}
          />
          {contact.evidence_count > 1 || (contact.source_count || 0) > 1 ? (
            <Text type="secondary">
              {(contact.source_count || 0) > 1 ? `${contact.source_count} 个来源 · ` : ''}
              {contact.evidence_count} 条证据
            </Text>
          ) : null}
        </Space>
      ),
    },
    {
      title: '联系人 / 场景',
      key: 'identity',
      render: (_, contact) => (
        <div className="target-contact-identity">
          <Space size={6} wrap>
            <Text strong>{contact.contact_name || contact.label || '公开联系人'}</Text>
            {contact.verified ? <Tag color="success">已核验</Tag> : null}
          </Space>
          {contact.party_name ? <Text type="secondary">{contact.party_name}</Text> : null}
          {contact.context ? (
            <Tooltip title={contact.context}>
              <Text type="secondary" className="target-contact-context">{contact.context}</Text>
            </Tooltip>
          ) : null}
        </div>
      ),
    },
    {
      title: '来源',
      key: 'source',
      width: 92,
      render: (_, contact) => (
        <Space orientation="vertical" size={2}>
          <Tag>{contact.module_label}</Tag>
          <Text type="secondary">{contact.attention_score} 分</Text>
        </Space>
      ),
    },
    {
      title: '',
      key: 'actions',
      width: 112,
      render: (_, contact) => (
        <Space size={0}>
          <Tooltip title={contact.finding_id ? '查看完整上下文' : '暂无关联 Finding'}>
            <Button
              type="text"
              icon={<EyeOutlined />}
              disabled={!contact.finding_id}
              aria-label="查看联系方式上下文"
              onClick={() => onViewContext(
                contact.finding_id || '',
                contact.contact_name || contact.label || contact.value,
              )}
            />
          </Tooltip>
          <Tooltip title={contact.source_url ? '打开原文' : '暂无原文链接'}>
            <Button
              type="text"
              icon={<LinkOutlined />}
              disabled={!contact.source_url}
              aria-label="打开联系方式原文"
              onClick={() => openSource(contact.source_url)}
            />
          </Tooltip>
          <Tooltip title={canOpenModule(contact.module) ? `进入${contact.module_label}` : '暂无对应模块'}>
            <Button
              type="text"
              icon={<ArrowRightOutlined />}
              disabled={!canOpenModule(contact.module)}
              aria-label="进入联系方式来源模块"
              onClick={() => {
                if (canOpenModule(contact.module)) {
                  onOpenModule(contact.module)
                }
              }}
            />
          </Tooltip>
        </Space>
      ),
    },
  ]
  const findingColumns: ColumnsType<TargetDashboardFinding> = [
    {
      title: '分值',
      dataIndex: 'attention_score',
      key: 'attention_score',
      width: 76,
      render: (score: number) => <Tag color={score >= 80 ? 'red' : score >= 70 ? 'orange' : 'default'}>{score}</Tag>,
    },
    {
      title: '发现',
      key: 'finding',
      render: (_, finding) => (
        <div className="target-finding-summary">
          <Space size={6} wrap>
            <Text strong>{finding.label || finding.type || '未命名 Finding'}</Text>
            <Tag>{finding.module_label}</Tag>
            {(finding.source_count || 0) > 1 ? (
              <Tooltip title={`相同信息已跨网站归类，保留 ${finding.evidence_count || finding.duplicate_count || finding.source_count} 条证据`}>
                <Tag color="blue">{finding.source_count} 个来源</Tag>
              </Tooltip>
            ) : (finding.duplicate_count || 0) > 1 ? (
              <Tooltip title="相同信息已去重归类，原始证据仍完整保留">
                <Tag color="blue">{finding.evidence_count || finding.duplicate_count} 条证据</Tag>
              </Tooltip>
            ) : null}
          </Space>
          {finding.value ? (
            <CopyableText value={finding.value} className="target-finding-value" />
          ) : null}
          {finding.context ? (
            <Tooltip title={finding.context}>
              <Text type="secondary" className="target-contact-context">{finding.context}</Text>
            </Tooltip>
          ) : null}
        </div>
      ),
    },
    {
      title: '归属',
      dataIndex: 'party_name',
      key: 'party_name',
      width: 180,
      render: (value: string) => value || <Text type="secondary">当前 Target</Text>,
    },
    {
      title: '',
      key: 'actions',
      width: 122,
      render: (_, finding) => (
        <Space size={0}>
          <Tooltip title="查看完整上下文">
            <Button
              type="text"
              icon={<EyeOutlined />}
              aria-label="查看 Finding 上下文"
              onClick={() => onViewContext(
                finding.finding_id,
                finding.label || finding.value || 'Finding 上下文',
              )}
            />
          </Tooltip>
          <Tooltip title={finding.source_url ? '打开原文' : '暂无原文链接'}>
            <Button
              type="text"
              icon={<LinkOutlined />}
              disabled={!finding.source_url}
              aria-label="打开 Finding 原文"
              onClick={() => openSource(finding.source_url)}
            />
          </Tooltip>
          <Tooltip title={canOpenModule(finding.module) ? `进入${finding.module_label}` : '暂无对应模块'}>
            <Button
              type="text"
              icon={<ArrowRightOutlined />}
              disabled={!canOpenModule(finding.module)}
              aria-label="进入 Finding 来源模块"
              onClick={() => {
                if (canOpenModule(finding.module)) {
                  onOpenModule(finding.module)
                }
              }}
            />
          </Tooltip>
        </Space>
      ),
    },
  ]

  return (
    <div className="target-overview">
      <div className="target-overview-metrics">
        <Statistic title="Finding" value={summary.finding_count || 0} />
        <Statistic
          title="高分 Finding"
          value={summary.high_score_finding_count || 0}
          styles={{ content: { color: summary.high_score_finding_count ? '#cf1322' : undefined } }}
        />
        <Statistic title="存活资产" value={summary.alive_asset_count || 0} suffix={`/ ${summary.asset_count || 0}`} />
        <Statistic title="个人电话" value={dashboard.contact_counts.personal_phone} />
        <Statistic title="个人邮箱" value={dashboard.contact_counts.personal_email} />
        <Statistic title="采集覆盖" value={summary.coverage_completed_count || 0} suffix={`/ ${summary.coverage_required_count || 4}`} />
      </div>

      <PortalResearchPanel projectId={projectId} targetId={summary.target_id} onResearch={onResearch} />

      <div className="target-overview-jumpbar">
        <Text strong>快速进入</Text>
        <Space size={[6, 6]} wrap>
          {modules.map((module) => (
            <Button
              key={module.tab}
              size="small"
              onClick={() => onOpenModule(module.tab)}
            >
              {module.label} {summary[module.countKey] || 0}
            </Button>
          ))}
        </Space>
        <Tooltip title="刷新当前 Target 聚合数据">
          <Button
            type="text"
            icon={<SyncOutlined spin={loading} />}
            disabled={loading}
            aria-label="刷新 Target 看板"
            onClick={() => {
              onRefresh()
            }}
          />
        </Tooltip>
      </div>

      <div className="target-contact-grid">
        <section className="target-overview-section">
          <div className="target-overview-section-title">
            <Space><PhoneOutlined /><Text strong>个人电话</Text><Tag>{dashboard.contact_counts.personal_phone}</Tag></Space>
          </div>
          <Table<TargetDashboardContact>
            className="target-contact-table"
            rowKey="contact_id"
            size="small"
            columns={contactColumns}
            dataSource={dashboard.personal_phones}
            scroll={{ x: 640 }}
            locale={{ emptyText: <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="暂无已归档个人电话" /> }}
            pagination={dashboard.personal_phones.length > 5 ? { pageSize: 5, size: 'small', showSizeChanger: false } : false}
          />
        </section>
        <section className="target-overview-section">
          <div className="target-overview-section-title">
            <Space><MailOutlined /><Text strong>个人邮箱</Text><Tag>{dashboard.contact_counts.personal_email}</Tag></Space>
          </div>
          <Table<TargetDashboardContact>
            className="target-contact-table"
            rowKey="contact_id"
            size="small"
            columns={contactColumns}
            dataSource={dashboard.personal_emails}
            scroll={{ x: 640 }}
            locale={{ emptyText: <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="暂无已归档个人邮箱" /> }}
            pagination={dashboard.personal_emails.length > 5 ? { pageSize: 5, size: 'small', showSizeChanger: false } : false}
          />
        </section>
      </div>

      <section className="target-overview-section target-overview-findings">
        <div className="target-overview-section-title">
          <Space><WarningOutlined /><Text strong>高价值 Finding</Text><Tag>{dashboard.top_findings.length}</Tag></Space>
        </div>
        <Table<TargetDashboardFinding>
          rowKey="finding_id"
          size="small"
          columns={findingColumns}
          dataSource={dashboard.top_findings}
          scroll={{ x: 820 }}
          locale={{ emptyText: <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="暂无 Finding" /> }}
          pagination={false}
        />
      </section>
    </div>
  )
}
