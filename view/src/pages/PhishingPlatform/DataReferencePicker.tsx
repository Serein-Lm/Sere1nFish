import { useEffect, useState } from 'react'
import { Modal, Tabs, Input, List, Tag, Empty, Spin, message, Button, Breadcrumb } from 'antd'
import { TeamOutlined, ProjectOutlined, SearchOutlined, AimOutlined, ArrowLeftOutlined } from '@ant-design/icons'
import { listPersons, type Person } from '../../services/personaService'
import { listProjects, type Project } from '../../services/projectService'
import { listProjectFindings, type UnifiedFinding } from '../../services/taskService'

// 可引用给 AI 中枢的数据实体：携带稳定 id，供后端 agent 自主用工具读取
export interface DataReference {
  type: 'person' | 'project' | 'finding'
  id: string
  label: string
  desc?: string
}

interface DataReferencePickerProps {
  open: boolean
  onClose: () => void
  onPick: (ref: DataReference) => void
  selectedIds?: string[]
}

export default function DataReferencePicker({ open, onClose, onPick, selectedIds = [] }: DataReferencePickerProps) {
  const [activeTab, setActiveTab] = useState<'person' | 'project'>('person')

  const [personKeyword, setPersonKeyword] = useState('')
  const [persons, setPersons] = useState<Person[]>([])
  const [personLoading, setPersonLoading] = useState(false)

  const [projects, setProjects] = useState<Project[]>([])
  const [projectKeyword, setProjectKeyword] = useState('')
  const [projectLoading, setProjectLoading] = useState(false)

  // 项目下钻：进入某项目后查看其 findings
  const [drillProject, setDrillProject] = useState<Project | null>(null)
  const [findings, setFindings] = useState<UnifiedFinding[]>([])
  const [findingsLoading, setFindingsLoading] = useState(false)

  const loadPersons = async (keyword: string) => {
    setPersonLoading(true)
    try {
      const res = await listPersons({ keyword: keyword.trim(), limit: 20 })
      setPersons(res.items)
    } catch (e) {
      message.error(e instanceof Error ? e.message : '加载人设库失败')
    } finally {
      setPersonLoading(false)
    }
  }

  const loadProjects = async () => {
    setProjectLoading(true)
    try {
      const res = await listProjects({ page: 1, page_size: 50 })
      setProjects(res.items)
    } catch (e) {
      message.error(e instanceof Error ? e.message : '加载项目失败')
    } finally {
      setProjectLoading(false)
    }
  }

  const openProjectFindings = async (project: Project) => {
    setDrillProject(project)
    setFindingsLoading(true)
    setFindings([])
    try {
      const res = await listProjectFindings(project.id, { page: 1, page_size: 50, sort: 'score_desc' })
      setFindings(res.items)
    } catch (e) {
      message.error(e instanceof Error ? e.message : '加载 findings 失败')
    } finally {
      setFindingsLoading(false)
    }
  }

  useEffect(() => {
    if (!open) return
    if (activeTab === 'person') loadPersons(personKeyword)
    else loadProjects()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, activeTab])

  // 关闭时重置下钻状态
  useEffect(() => {
    if (!open) {
      setDrillProject(null)
      setFindings([])
    }
  }, [open])

  const isSelected = (id: string) => selectedIds.includes(id)

  const filteredProjects = projectKeyword.trim()
    ? projects.filter(p =>
        (p.name || '').toLowerCase().includes(projectKeyword.trim().toLowerCase()) ||
        (p.description || '').toLowerCase().includes(projectKeyword.trim().toLowerCase()),
      )
    : projects

  // 项目 tab 内容：未下钻显示项目列表，下钻后显示该项目 findings
  const renderProjectPane = () => {
    if (drillProject) {
      return (
        <>
          <Breadcrumb
            style={{ marginBottom: 12 }}
            items={[
              {
                title: (
                  <a onClick={() => { setDrillProject(null); setFindings([]) }}>
                    <ArrowLeftOutlined /> 返回项目列表
                  </a>
                ),
              },
              { title: drillProject.name },
            ]}
          />
          <div style={{ marginBottom: 8, display: 'flex', gap: 8, flexWrap: 'wrap', alignItems: 'center' }}>
            <span style={{ color: '#999', fontSize: 12 }}>可整体引用项目，或选择具体 finding：</span>
            <Button
              size="small"
              type="primary"
              ghost
              disabled={isSelected(drillProject.id)}
              onClick={() =>
                onPick({
                  type: 'project',
                  id: drillProject.id,
                  label: drillProject.name,
                  desc: drillProject.description || undefined,
                })
              }
            >
              {isSelected(drillProject.id) ? '项目已引用' : '引用整个项目'}
            </Button>
          </div>
          {findingsLoading ? (
            <div style={{ textAlign: 'center', padding: 24 }}><Spin /></div>
          ) : findings.length === 0 ? (
            <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="该项目暂无 findings" />
          ) : (
            <List
              dataSource={findings}
              style={{ maxHeight: 360, overflow: 'auto' }}
              renderItem={f => {
                const selected = isSelected(f.finding_id)
                const title = f.label || f.value || f.type || 'Finding'
                return (
                  <List.Item
                    actions={[
                      <a
                        key="pick"
                        onClick={() =>
                          !selected &&
                          onPick({
                            type: 'finding',
                            id: f.finding_id,
                            label: title,
                            desc: [f.source, f.type, f.value].filter(Boolean).join(' · '),
                          })
                        }
                        style={selected ? { color: '#999', cursor: 'default' } : undefined}
                      >
                        {selected ? '已引用' : '引用'}
                      </a>,
                    ]}
                  >
                    <List.Item.Meta
                      title={
                        <span>
                          {title}
                          {typeof f.attention_score === 'number' && (
                            <Tag
                              style={{ marginLeft: 8 }}
                              color={f.attention_score >= 70 ? 'error' : f.attention_score >= 40 ? 'warning' : 'default'}
                            >
                              {f.attention_score}
                            </Tag>
                          )}
                        </span>
                      }
                      description={[f.source, f.type, f.attention_reason].filter(Boolean).join(' · ') || '—'}
                    />
                  </List.Item>
                )
              }}
            />
          )}
        </>
      )
    }

    return (
      <>
        <Input
          allowClear
          prefix={<SearchOutlined />}
          placeholder="按项目名称 / 描述过滤"
          value={projectKeyword}
          onChange={e => setProjectKeyword(e.target.value)}
          style={{ marginBottom: 12 }}
        />
        {projectLoading ? (
          <div style={{ textAlign: 'center', padding: 24 }}><Spin /></div>
        ) : filteredProjects.length === 0 ? (
          <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="暂无项目" />
        ) : (
          <List
            dataSource={filteredProjects}
            style={{ maxHeight: 380, overflow: 'auto' }}
            renderItem={p => {
              const selected = isSelected(p.id)
              return (
                <List.Item
                  actions={[
                    <a key="findings" onClick={() => openProjectFindings(p)}>
                      <AimOutlined /> 查看 findings
                    </a>,
                    <a
                      key="pick"
                      onClick={() =>
                        !selected &&
                        onPick({
                          type: 'project',
                          id: p.id,
                          label: p.name,
                          desc: p.description || undefined,
                        })
                      }
                      style={selected ? { color: '#999', cursor: 'default' } : undefined}
                    >
                      {selected ? '已引用' : '引用'}
                    </a>,
                  ]}
                >
                  <List.Item.Meta title={p.name} description={p.description || '—'} />
                </List.Item>
              )
            }}
          />
        )}
      </>
    )
  }

  return (
    <Modal
      open={open}
      onCancel={onClose}
      footer={null}
      title="引用平台数据给 AI 中枢"
      width={640}
      destroyOnHidden
    >
      <Tabs
        activeKey={activeTab}
        onChange={k => setActiveTab(k as 'person' | 'project')}
        items={[
          {
            key: 'person',
            label: (
              <span>
                <TeamOutlined /> 人物画像
              </span>
            ),
            children: (
              <>
                <Input
                  allowClear
                  prefix={<SearchOutlined />}
                  placeholder="按姓名 / 公司 / 职位搜索人物"
                  value={personKeyword}
                  onChange={e => setPersonKeyword(e.target.value)}
                  onPressEnter={() => loadPersons(personKeyword)}
                  style={{ marginBottom: 12 }}
                />
                {personLoading ? (
                  <div style={{ textAlign: 'center', padding: 24 }}><Spin /></div>
                ) : persons.length === 0 ? (
                  <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="暂无人物" />
                ) : (
                  <List
                    dataSource={persons}
                    style={{ maxHeight: 380, overflow: 'auto' }}
                    renderItem={p => {
                      const selected = isSelected(p.person_id)
                      return (
                        <List.Item
                          actions={[
                            <a
                              key="pick"
                              onClick={() =>
                                !selected &&
                                onPick({
                                  type: 'person',
                                  id: p.person_id,
                                  label: p.name,
                                  desc: [p.company, p.position].filter(Boolean).join(' · '),
                                })
                              }
                              style={selected ? { color: '#999', cursor: 'default' } : undefined}
                            >
                              {selected ? '已引用' : '引用'}
                            </a>,
                          ]}
                        >
                          <List.Item.Meta
                            title={
                              <span>
                                {p.name}
                                {p.position && <Tag style={{ marginLeft: 8 }}>{p.position}</Tag>}
                              </span>
                            }
                            description={[p.company, p.industry, p.summary].filter(Boolean).join(' · ') || '—'}
                          />
                        </List.Item>
                      )
                    }}
                  />
                )}
              </>
            ),
          },
          {
            key: 'project',
            label: (
              <span>
                <ProjectOutlined /> 项目 / Findings
              </span>
            ),
            children: renderProjectPane(),
          },
        ]}
      />
    </Modal>
  )
}
