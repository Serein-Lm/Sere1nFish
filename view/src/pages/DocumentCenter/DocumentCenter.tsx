import { Card, Row, Col, Table, Button, Tag, Tabs, Input, Space, Typography, Statistic } from 'antd'
import {
  BookOutlined,
  FileTextOutlined,
  HistoryOutlined,
  SearchOutlined,
  DownloadOutlined,
  EyeOutlined,
  DeleteOutlined,
  PlusOutlined,
  FolderOpenOutlined,
} from '@ant-design/icons'
import type { ColumnsType } from 'antd/es/table'
import { caseLibrary, historyFiles } from '../../utils/mockData'
import './DocumentCenter.css'

const { Title, Paragraph, Text } = Typography

export default function DocumentCenter() {
  const caseColumns: ColumnsType<any> = [
    { title: '标题', dataIndex: 'title', key: 'title' },
    { title: '分类', dataIndex: 'category', key: 'category' },
    {
      title: '成功率',
      dataIndex: 'successRate',
      key: 'successRate',
      render: (rate) => <Tag color={rate > 70 ? 'green' : 'orange'}>{rate}%</Tag>,
    },
    { title: '日期', dataIndex: 'date', key: 'date' },
    {
      title: '标签',
      dataIndex: 'tags',
      key: 'tags',
      render: (tags: string[]) => (
        <>
          {tags.map((tag, idx) => (
            <Tag key={idx}>{tag}</Tag>
          ))}
        </>
      ),
    },
    {
      title: '操作',
      key: 'action',
      render: () => (
        <Space size="small">
          <Button type="text" icon={<EyeOutlined />} size="small" />
          <Button type="text" icon={<DownloadOutlined />} size="small" />
        </Space>
      ),
    },
  ]

  const fileColumns: ColumnsType<any> = [
    { title: '文件名', dataIndex: 'name', key: 'name' },
    { title: '类型', dataIndex: 'type', key: 'type' },
    { title: '大小', dataIndex: 'size', key: 'size' },
    { title: '日期', dataIndex: 'date', key: 'date' },
    {
      title: '操作',
      key: 'action',
      render: () => (
        <Space size="small">
          <Button type="text" icon={<DownloadOutlined />} size="small" />
          <Button type="text" danger icon={<DeleteOutlined />} size="small" />
        </Space>
      ),
    },
  ]

  const tabItems = [
    {
      key: 'cases',
      label: (
        <Space>
          <FileTextOutlined /> 案例知识库
        </Space>
      ),
      children: (
        <div className="tab-pane-content fade-in">
          <div className="table-actions-header">
            <Input
              placeholder="搜索案例标题、分类或标签..."
              prefix={<SearchOutlined />}
              className="search-input-premium"
              allowClear
            />
            <Button type="primary" icon={<PlusOutlined />} className="hover-float">
              录入案例
            </Button>
          </div>
          <Table 
            columns={caseColumns} 
            dataSource={caseLibrary} 
            rowKey="id" 
            className="custom-table"
          />
        </div>
      ),
    },
    {
      key: 'history',
      label: (
        <Space>
          <HistoryOutlined /> 历史产出文件
        </Space>
      ),
      children: (
        <div className="tab-pane-content fade-in">
          <div className="table-actions-header">
            <Input
              placeholder="搜索文件名或类型..."
              prefix={<SearchOutlined />}
              className="search-input-premium"
              allowClear
            />
            <Button type="primary" icon={<PlusOutlined />} className="hover-float">
              上传文件
            </Button>
          </div>
          <Table 
            columns={fileColumns} 
            dataSource={historyFiles} 
            rowKey="id" 
            className="custom-table"
          />
        </div>
      ),
    },
  ]

  return (
    <div className="document-center page-container fade-in">
      <div className="page-header slide-up">
        <div>
          <Title level={2} className="page-title">
            <BookOutlined /> 文档中心
          </Title>
          <Paragraph className="page-description">
            汇集攻防案例知识库与系统生成的历史报告文件
          </Paragraph>
        </div>
      </div>

      <Row gutter={[24, 24]} style={{ marginBottom: '24px' }}>
        <Col xs={24} sm={8} className="slide-up stagger-1">
          <Card className="glass-card hover-float stat-mini-card">
            <Statistic
              title="知识案例"
              value={caseLibrary.length}
              prefix={<FileTextOutlined />}
              valueStyle={{ color: 'var(--color-info)', fontWeight: 700 }}
            />
            <div className="stat-footer">
              <Text type="secondary">本周新增: 3</Text>
            </div>
          </Card>
        </Col>
        <Col xs={24} sm={8} className="slide-up stagger-2">
          <Card className="glass-card hover-float stat-mini-card">
            <Statistic
              title="产出文件"
              value={historyFiles.length}
              prefix={<FolderOpenOutlined />}
              valueStyle={{ color: 'var(--color-success)', fontWeight: 700 }}
            />
            <div className="stat-footer">
              <Text type="secondary">已同步云端</Text>
            </div>
          </Card>
        </Col>
        <Col xs={24} sm={8} className="slide-up stagger-3">
          <Card className="glass-card hover-float stat-mini-card">
            <Statistic
              title="下载热度"
              value={234}
              prefix={<DownloadOutlined />}
              valueStyle={{ color: 'var(--color-warning)', fontWeight: 700 }}
            />
            <div className="stat-footer">
              <Text type="secondary">报告被查看次数</Text>
            </div>
          </Card>
        </Col>
      </Row>

      <div className="slide-up stagger-2">
        <Card className="glass-card table-tabs-card">
          <Tabs items={tabItems} className="custom-tabs" />
        </Card>
      </div>
    </div>
  )
}
