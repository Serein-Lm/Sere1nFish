import { useEffect, useState } from 'react'
import { Card, Button, Table, Tag, Typography, Space, Modal, Form, Input, message, Tooltip, Popconfirm, Select, Divider } from 'antd'
import {
  UserOutlined,
  PlusOutlined,
  DeleteOutlined,
  CheckCircleOutlined,
  CloseCircleOutlined,
  EditOutlined,
  TeamOutlined,
  KeyOutlined,
  EyeOutlined,
  EyeInvisibleOutlined,
  CopyOutlined,
} from '@ant-design/icons'
import type { ColumnsType } from 'antd/es/table'
import {
  listUsers,
  createUser,
  updateUser,
  deleteUser,
  getLoginKey,
  changeLoginKey,
  type UserInfo,
} from '../../services/authService'
import './UserManagement.css'

const { Title, Paragraph, Text } = Typography

export default function UserManagement() {
  const [users, setUsers] = useState<UserInfo[]>([])
  const [loading, setLoading] = useState(false)
  
  // 添加用户
  const [isAddModalOpen, setIsAddModalOpen] = useState(false)
  const [addForm] = Form.useForm()
  const [addSubmitting, setAddSubmitting] = useState(false)
  
  // 编辑用户
  const [isEditModalOpen, setIsEditModalOpen] = useState(false)
  const [editForm] = Form.useForm()
  const [editSubmitting, setEditSubmitting] = useState(false)
  const [editingUser, setEditingUser] = useState<string | null>(null)

  // 登录 Key 管理
  const [loginKey, setLoginKey] = useState<string>('')
  const [showLoginKey, setShowLoginKey] = useState(false)
  const [isKeyModalOpen, setIsKeyModalOpen] = useState(false)
  const [keyForm] = Form.useForm()
  const [keySubmitting, setKeySubmitting] = useState(false)

  const fetchUsers = async () => {
    setLoading(true)
    try {
      const data = await listUsers()
      setUsers(data.users)
    } catch (e) {
      console.error('Failed to load users:', e)
      message.error('加载用户列表失败')
    } finally {
      setLoading(false)
    }
  }

  const fetchLoginKey = async () => {
    try {
      const data = await getLoginKey()
      setLoginKey(data.key)
    } catch (e) {
      console.error('Failed to load login key:', e)
    }
  }

  useEffect(() => {
    fetchUsers()
    fetchLoginKey()
  }, [])

  // 添加用户
  const handleAdd = () => {
    addForm.resetFields()
    setIsAddModalOpen(true)
  }

  const handleAddSubmit = async () => {
    try {
      const values = await addForm.validateFields()
      setAddSubmitting(true)
      await createUser({
        username: values.username,
        password: values.password,
        role: values.role,
      })
      setIsAddModalOpen(false)
      message.success('用户创建成功')
      await fetchUsers()
    } catch (e) {
      const msg = e instanceof Error ? e.message : '创建失败'
      message.error(msg)
    } finally {
      setAddSubmitting(false)
    }
  }

  // 编辑用户
  const handleEdit = (username: string, user: UserInfo) => {
    setEditingUser(username)
    editForm.setFieldsValue({
      new_username: username,
      role: user.role,
      disabled: user.disabled,
    })
    setIsEditModalOpen(true)
  }

  const handleEditSubmit = async () => {
    if (!editingUser) return
    try {
      const values = await editForm.validateFields()
      setEditSubmitting(true)
      await updateUser(editingUser, {
        new_username: values.new_username !== editingUser ? values.new_username : undefined,
        role: values.role,
        disabled: values.disabled,
        password: values.password || undefined,
      })
      setIsEditModalOpen(false)
      message.success('用户已更新')
      await fetchUsers()
    } catch (e) {
      const msg = e instanceof Error ? e.message : '更新失败'
      message.error(msg)
    } finally {
      setEditSubmitting(false)
    }
  }

  // 登录 Key 管理
  const handleChangeKey = () => {
    keyForm.resetFields()
    setIsKeyModalOpen(true)
  }

  const handleKeySubmit = async () => {
    try {
      const values = await keyForm.validateFields()
      setKeySubmitting(true)
      await changeLoginKey({
        old_key: values.old_key,
        new_key: values.new_key,
      })
      setIsKeyModalOpen(false)
      message.success('登录 Key 已更新')
      await fetchLoginKey()
    } catch (e) {
      const msg = e instanceof Error ? e.message : '修改失败'
      message.error(msg)
    } finally {
      setKeySubmitting(false)
    }
  }

  // 删除用户
  const handleDelete = async (username: string) => {
    try {
      await deleteUser(username)
      message.success(`用户 ${username} 已删除`)
      await fetchUsers()
    } catch (e) {
      const msg = e instanceof Error ? e.message : '删除失败'
      message.error(msg)
    }
  }

  const columns: ColumnsType<UserInfo> = [
    {
      title: '用户名',
      dataIndex: 'username',
      key: 'username',
      render: (name: string) => (
        <Space>
          <UserOutlined />
          <Text strong>{name}</Text>
        </Space>
      ),
    },
    {
      title: '角色',
      dataIndex: 'role',
      key: 'role',
      width: 120,
      render: (role: string) => (
        <Tag color={role === 'admin' ? 'blue' : 'default'}>
          {role === 'admin' ? '管理员' : '普通用户'}
        </Tag>
      ),
    },
    {
      title: '状态',
      dataIndex: 'disabled',
      key: 'disabled',
      width: 100,
      render: (disabled: boolean) => (
        disabled 
          ? <Tag icon={<CloseCircleOutlined />} color="error">已禁用</Tag>
          : <Tag icon={<CheckCircleOutlined />} color="success">正常</Tag>
      ),
    },
    {
      title: '操作',
      key: 'action',
      width: 150,
      render: (_, record) => (
        <Space size="small">
          <Tooltip title="编辑用户">
            <Button
              size="small"
              icon={<EditOutlined />}
              onClick={() => handleEdit(record.username, record)}
            />
          </Tooltip>
          {record.username !== 'admin' && (
            <Popconfirm
              title="确认删除"
              description={`确定要删除用户 ${record.username} 吗？`}
              onConfirm={() => handleDelete(record.username)}
              okText="删除"
              cancelText="取消"
            >
              <Tooltip title="删除用户">
                <Button size="small" danger icon={<DeleteOutlined />} />
              </Tooltip>
            </Popconfirm>
          )}
        </Space>
      ),
    },
  ]

  return (
    <div className="user-management page-container fade-in">
      <div className="page-header slide-up">
        <div>
          <Title level={2} className="page-title">
            <TeamOutlined /> 用户管理
          </Title>
          <Paragraph className="page-description">管理系统用户，设置用户角色和权限</Paragraph>
        </div>
        <Button type="primary" icon={<PlusOutlined />} onClick={handleAdd}>
          添加用户
        </Button>
      </div>

      {/* 登录 Key 管理卡片 */}
      <Card className="glass-card slide-up stagger-1" style={{ marginBottom: 24 }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
          <Space>
            <KeyOutlined style={{ fontSize: 20, color: 'var(--primary-color)' }} />
            <div>
              <Text strong>登录 Key</Text>
              <div style={{ marginTop: 4 }}>
                <Text type="secondary" style={{ fontFamily: 'monospace' }}>
                  {showLoginKey ? loginKey : '••••••••••••'}
                </Text>
                <Button
                  type="text"
                  size="small"
                  icon={showLoginKey ? <EyeInvisibleOutlined /> : <EyeOutlined />}
                  onClick={() => setShowLoginKey(!showLoginKey)}
                  style={{ marginLeft: 8 }}
                />
                <Tooltip title="复制 Key">
                  <Button
                    type="text"
                    size="small"
                    icon={<CopyOutlined />}
                    onClick={() => {
                      navigator.clipboard.writeText(loginKey)
                      message.success('已复制到剪贴板')
                    }}
                  />
                </Tooltip>
              </div>
            </div>
          </Space>
          <Button icon={<EditOutlined />} onClick={handleChangeKey}>
            修改 Key
          </Button>
        </div>
      </Card>

      <Card className="glass-card slide-up stagger-2">
        <Table
          columns={columns}
          dataSource={users}
          rowKey="username"
          loading={loading}
          pagination={false}
          className="custom-table"
          locale={{ emptyText: '暂无用户数据' }}
        />
      </Card>

      {/* 添加用户 Modal */}
      <Modal
        title="添加用户"
        open={isAddModalOpen}
        onOk={handleAddSubmit}
        onCancel={() => setIsAddModalOpen(false)}
        confirmLoading={addSubmitting}
        destroyOnClose
        width={500}
      >
        <Form form={addForm} layout="vertical" initialValues={{ role: 'user' }}>
          <Form.Item
            name="username"
            label="用户名"
            rules={[{ required: true, message: '请输入用户名' }]}
          >
            <Input placeholder="请输入用户名" prefix={<UserOutlined />} />
          </Form.Item>
          <Form.Item
            name="password"
            label="密码"
            rules={[{ required: true, message: '请输入密码' }]}
          >
            <Input.Password placeholder="请输入密码" />
          </Form.Item>
          <Form.Item
            name="role"
            label="角色"
            rules={[{ required: true, message: '请选择角色' }]}
          >
            <Select
              options={[
                { label: '普通用户', value: 'user' },
                { label: '管理员', value: 'admin' },
              ]}
            />
          </Form.Item>
        </Form>
      </Modal>

      {/* 编辑用户 Modal */}
      <Modal
        title={`编辑用户 - ${editingUser}`}
        open={isEditModalOpen}
        onOk={handleEditSubmit}
        onCancel={() => {
          setIsEditModalOpen(false)
          setEditingUser(null)
        }}
        confirmLoading={editSubmitting}
        okText="保存修改"
        destroyOnClose
        width={500}
      >
        <Form form={editForm} layout="vertical">
          <Form.Item
            name="new_username"
            label="用户名"
            rules={[{ required: true, message: '请输入用户名' }]}
            extra={editingUser === 'admin' ? '默认管理员用户名不可修改' : undefined}
          >
            <Input 
              placeholder="请输入用户名" 
              prefix={<UserOutlined />}
              disabled={editingUser === 'admin'}
            />
          </Form.Item>
          <Form.Item
            name="role"
            label="角色"
            rules={[{ required: true, message: '请选择角色' }]}
          >
            <Select
              options={[
                { label: '普通用户', value: 'user' },
                { label: '管理员', value: 'admin' },
              ]}
            />
          </Form.Item>
          <Form.Item name="disabled" label="状态">
            <Select
              options={[
                { label: '正常', value: false },
                { label: '禁用', value: true },
              ]}
            />
          </Form.Item>
          <Divider />
          <Form.Item name="password" label="新密码" extra="留空则不修改密码">
            <Input.Password placeholder="输入新密码（可选）" />
          </Form.Item>
        </Form>
      </Modal>

      {/* 修改登录 Key Modal */}
      <Modal
        title="修改登录 Key"
        open={isKeyModalOpen}
        onOk={handleKeySubmit}
        onCancel={() => setIsKeyModalOpen(false)}
        confirmLoading={keySubmitting}
        okText="确认修改"
        destroyOnClose
        width={450}
      >
        <Form form={keyForm} layout="vertical">
          <Form.Item
            name="old_key"
            label="原 Key"
            rules={[{ required: true, message: '请输入原 Key' }]}
          >
            <Input.Password placeholder="请输入原 Key" />
          </Form.Item>
          <Form.Item
            name="new_key"
            label="新 Key"
            rules={[
              { required: true, message: '请输入新 Key' },
              { min: 6, message: 'Key 长度至少 6 位' },
            ]}
          >
            <Input.Password placeholder="请输入新 Key（至少 6 位）" />
          </Form.Item>
          <Form.Item
            name="confirm_key"
            label="确认新 Key"
            dependencies={['new_key']}
            rules={[
              { required: true, message: '请确认新 Key' },
              ({ getFieldValue }) => ({
                validator(_, value) {
                  if (!value || getFieldValue('new_key') === value) {
                    return Promise.resolve()
                  }
                  return Promise.reject(new Error('两次输入的 Key 不一致'))
                },
              }),
            ]}
          >
            <Input.Password placeholder="请再次输入新 Key" />
          </Form.Item>
        </Form>
      </Modal>
    </div>
  )
}
