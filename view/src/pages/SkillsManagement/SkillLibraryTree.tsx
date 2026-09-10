import { useMemo, useState, type Key, type ReactNode } from 'react'
import { Empty, Spin, Tag, Tree, Typography, message } from 'antd'
import {
  FileMarkdownOutlined,
  FileTextOutlined,
  FolderOutlined,
} from '@ant-design/icons'
import {
  getSkillResource,
  listSkillResources,
  statusMeta,
  type Skill,
  type SkillResource,
} from '../../services/skillService'

const { Text } = Typography

interface ResourcePreview {
  skill: Skill
  resource: SkillResource
}

interface SkillTreeNode {
  key: Key
  title: ReactNode
  children?: SkillTreeNode[]
  isLeaf?: boolean
  selectable?: boolean
  skill?: Skill
  resource?: SkillResource
  nodeType: 'root' | 'skill' | 'instruction' | 'resource'
}

interface SkillLibraryTreeProps {
  skills: Skill[]
  loading: boolean
  selectedKeys: Key[]
  onSelectSkill: (skill: Skill, key: string) => void
  onPreviewResource: (preview: ResourcePreview) => void
}

const phasesOf = (skill: Skill) => {
  const phases = skill.meta?.phases
  return Array.isArray(phases) ? phases.map(String).filter(Boolean) : []
}

const skillTitle = (skill: Skill) => (
  <span className="library-tree-folder">
    <FolderOutlined />
    <span>{skill.slug}</span>
    <Text type="secondary">{skill.name}</Text>
    {phasesOf(skill).map((phase) => (
      <Tag key={phase} bordered={false}>{phase}</Tag>
    ))}
    <Tag color={statusMeta(skill.status).color} bordered={false}>
      {statusMeta(skill.status).label}
    </Tag>
  </span>
)

const resourceTitle = (resource: SkillResource) => {
  const isDirectory = resource.kind === 'directory'
  const isMarkdown = /markdown|text\/plain/i.test(resource.content_type)
    || /\.md$/i.test(resource.name)
  return (
    <span className={isDirectory ? 'library-tree-category' : 'library-tree-leaf'}>
      {isDirectory
        ? <FolderOutlined />
        : isMarkdown ? <FileMarkdownOutlined /> : <FileTextOutlined />}
      <span>{resource.name}</span>
      {resource.role !== 'resource' && <Tag bordered={false}>{resource.role}</Tag>}
    </span>
  )
}

const resourceNode = (skill: Skill, resource: SkillResource): SkillTreeNode => ({
  key: `resource:${skill.skill_id}:${encodeURIComponent(resource.path)}`,
  title: resourceTitle(resource),
  isLeaf: resource.kind === 'file',
  skill,
  resource,
  nodeType: 'resource',
})

export default function SkillLibraryTree({
  skills,
  loading,
  selectedKeys,
  onSelectSkill,
  onPreviewResource,
}: SkillLibraryTreeProps) {
  const sortedSkills = useMemo(
    () => [...skills].sort((left, right) => left.slug.localeCompare(right.slug)),
    [skills],
  )
  const treeVersion = useMemo(
    () => sortedSkills.map((skill) => `${skill.skill_id}:${skill.version}:${skill.updated_at}`).join('|'),
    [sortedSkills],
  )
  const [resourceCache, setResourceCache] = useState<{
    version: string
    childrenByKey: Record<string, SkillTreeNode[]>
  }>({ version: '', childrenByKey: {} })
  const childrenByKey = useMemo(
    () => resourceCache.version === treeVersion ? resourceCache.childrenByKey : {},
    [resourceCache, treeVersion],
  )
  const treeData = useMemo<SkillTreeNode[]>(() => {
    const hydrate = (node: SkillTreeNode): SkillTreeNode => {
      const children = childrenByKey[String(node.key)]
      return children === undefined
        ? node
        : { ...node, children: children.map(hydrate) }
    }
    const root: SkillTreeNode = {
      key: 'skills-root',
      title: (
        <span className="library-tree-category">
          <FolderOutlined />
          <span>skills</span>
          <Tag bordered={false}>Layer 1</Tag>
        </span>
      ),
      selectable: false,
      nodeType: 'root',
      children: sortedSkills.map<SkillTreeNode>((skill) => ({
        key: `folder:${skill.skill_id}`,
        title: skillTitle(skill),
        isLeaf: false,
        skill,
        nodeType: 'skill',
      })),
    }
    return [hydrate(root)]
  }, [childrenByKey, sortedSkills])

  const loadNode = async (node: SkillTreeNode) => {
    if (Object.hasOwn(childrenByKey, String(node.key)) || !node.skill) return
    try {
      const parentPath = node.resource?.path || ''
      const response = await listSkillResources(node.skill.skill_id, parentPath)
      const children = response.items
        .filter((resource) => !(node.nodeType === 'skill' && resource.path === 'SKILL.md'))
        .map((resource) => resourceNode(node.skill!, resource))
      if (node.nodeType === 'skill') {
        children.unshift({
          key: `skill:${node.skill.skill_id}:skill-md`,
          title: (
            <span className="library-tree-leaf">
              <FileMarkdownOutlined />
              <span>SKILL.md</span>
              <Tag bordered={false}>Layer 2</Tag>
            </span>
          ),
          isLeaf: true,
          skill: node.skill,
          nodeType: 'instruction',
        })
      }
      setResourceCache((current) => ({
        version: treeVersion,
        childrenByKey: {
          ...(current.version === treeVersion ? current.childrenByKey : {}),
          [String(node.key)]: children,
        },
      }))
    } catch {
      message.error('加载 Skill 资源失败')
      setResourceCache((current) => ({
        version: treeVersion,
        childrenByKey: {
          ...(current.version === treeVersion ? current.childrenByKey : {}),
          [String(node.key)]: [],
        },
      }))
    }
  }

  const handleSelect = async (keys: Key[], info: { node: SkillTreeNode }) => {
    const node = info.node
    const key = String(keys[0] || node.key)
    if (!node.skill) return
    if (node.nodeType === 'resource' && node.resource?.kind === 'file') {
      try {
        const resource = await getSkillResource(node.skill.skill_id, node.resource.path)
        onPreviewResource({ skill: node.skill, resource })
      } catch {
        message.error('读取 Skill 资源失败')
      }
      return
    }
    if (node.nodeType === 'skill' || node.nodeType === 'instruction') {
      onSelectSkill(node.skill, key)
    }
  }

  return (
    <Spin spinning={loading}>
      {sortedSkills.length > 0 ? (
        <Tree
          showLine
          blockNode
          defaultExpandedKeys={['skills-root']}
          selectedKeys={selectedKeys}
          treeData={treeData}
          loadData={(node) => loadNode(node as unknown as SkillTreeNode)}
          onSelect={(keys, info) => void handleSelect(
            keys,
            info as unknown as { node: SkillTreeNode },
          )}
        />
      ) : (
        <Empty description="暂无技能" image={Empty.PRESENTED_IMAGE_SIMPLE} />
      )}
    </Spin>
  )
}

export type { ResourcePreview }
