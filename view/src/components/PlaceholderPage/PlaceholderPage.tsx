import { Card, Empty, Button } from 'antd'
import { RocketOutlined } from '@ant-design/icons'
import './PlaceholderPage.css'

interface PlaceholderPageProps {
  title: string
  description?: string
  icon?: React.ReactNode
}

export default function PlaceholderPage({ title, description, icon }: PlaceholderPageProps) {
  return (
    <div className="placeholder-page page-container">
      <div className="page-header">
        <h1 className="page-title">{icon} {title}</h1>
        {description && <p className="page-description">{description}</p>}
      </div>

      <Card className="glass-card placeholder-card">
        <Empty
          image={Empty.PRESENTED_IMAGE_SIMPLE}
          description={
            <div className="empty-description">
              <p>该功能正在开发中</p>
              <p className="empty-hint">敬请期待...</p>
            </div>
          }
        >
          <Button type="primary" icon={<RocketOutlined />} className="back-btn">
            返回仪表盘
          </Button>
        </Empty>
      </Card>
    </div>
  )
}
