import { useMemo, useState } from 'react'
import { ExperimentOutlined } from '@ant-design/icons'
import { Select, Tooltip, message } from 'antd'
import { listSkills, type Skill } from '../services/skillService'

interface SkillSelectorProps {
  value?: string[]
  onChange?: (value: string[]) => void
  disabled?: boolean
  className?: string
  placeholder?: string
  tooltip?: string
}

export default function SkillSelector({
  value = [],
  onChange,
  disabled = false,
  className,
  placeholder = '选择 Skills',
  tooltip = '为本轮任务显式选择渐进加载的 Skill',
}: SkillSelectorProps) {
  const [skills, setSkills] = useState<Skill[]>([])
  const [loading, setLoading] = useState(false)
  const [loaded, setLoaded] = useState(false)

  const loadOptions = async () => {
    if (loaded || loading) return
    setLoading(true)
    try {
      const result = await listSkills({
        status: 'approved',
        page: 1,
        page_size: 100,
        sort_by: 'category',
        sort_order: 'asc',
      })
      setSkills(result.items.filter((skill) => skill.enabled !== false))
      setLoaded(true)
    } catch {
      message.error('加载 Skills 失败')
    } finally {
      setLoading(false)
    }
  }

  const options = useMemo(() => {
    const grouped = new Map<string, Skill[]>()
    for (const skill of skills) {
      const category = skill.category || '未分类'
      grouped.set(category, [...(grouped.get(category) || []), skill])
    }
    return [...grouped.entries()].map(([category, items]) => ({
      label: category,
      options: items.map((skill) => ({
        value: skill.slug,
        label: skill.name || skill.slug,
        title: skill.description,
      })),
    }))
  }, [skills])

  return (
    <Tooltip title={tooltip}>
      <Select
        className={className}
        mode="multiple"
        allowClear
        showSearch
        maxTagCount="responsive"
        maxCount={32}
        value={value}
        options={options}
        loading={loading}
        disabled={disabled}
        placeholder={<span><ExperimentOutlined /> {placeholder}</span>}
        optionFilterProp="label"
        onOpenChange={(open) => {
          if (open) void loadOptions()
        }}
        onChange={onChange}
      />
    </Tooltip>
  )
}
