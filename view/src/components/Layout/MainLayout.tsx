import { useState, useEffect, useMemo } from 'react'
import { Layout, Menu, Avatar, Dropdown, Space, Switch, Tooltip, Divider, Typography } from 'antd'
import type { MenuProps } from 'antd'
import {
  DashboardOutlined,
  MailOutlined,
  SettingOutlined,
  LogoutOutlined,
  UserOutlined,
  MenuFoldOutlined,
  MenuUnfoldOutlined,
  ThunderboltOutlined,
  MobileOutlined,
  FileTextOutlined,
  RobotOutlined,
  WechatOutlined,
  CloudServerOutlined,
  ApiOutlined,
  BookOutlined,
  BulbOutlined,
  BulbFilled,
  ExperimentOutlined,
  HighlightOutlined,
  DatabaseOutlined,
  TeamOutlined,
} from '@ant-design/icons'
import { Outlet, useNavigate, useLocation } from 'react-router-dom'
import { useTheme } from '../../contexts/ThemeContext'
import { logout, getCurrentUser, type CurrentUser } from '../../services/authService'
import './MainLayout.css'

const { Header, Sider, Content } = Layout
const { Text } = Typography

export default function MainLayout() {
  const [collapsed, setCollapsed] = useState(false)
  const [isMobile, setIsMobile] = useState(false)
  const [currentUser, setCurrentUser] = useState<CurrentUser | null>(null)
  const navigate = useNavigate()
  const location = useLocation()
  const { theme, toggleTheme } = useTheme()

  // 响应式：小屏将侧边栏切换为悬浮抽屉，默认收起
  useEffect(() => {
    const mq = window.matchMedia('(max-width: 991px)')
    const apply = (matches: boolean) => {
      setIsMobile(matches)
      setCollapsed(matches)
    }
    apply(mq.matches)
    const handler = (e: MediaQueryListEvent) => apply(e.matches)
    mq.addEventListener('change', handler)
    return () => mq.removeEventListener('change', handler)
  }, [])

  // 加载用户信息
  useEffect(() => {
    const loadUserInfo = async () => {
      try {
        const user = await getCurrentUser()
        setCurrentUser(user)
        localStorage.setItem('userInfo', JSON.stringify(user))
      } catch (e) {
        console.error('Failed to load user info:', e)
        // 尝试从 localStorage 读取
        const cached = localStorage.getItem('userInfo')
        if (cached) {
          setCurrentUser(JSON.parse(cached))
        }
      }
    }
    loadUserInfo()
  }, [])

  const menuItems: MenuProps['items'] = useMemo(() => {
    const items: MenuProps['items'] = [
      {
        key: '/dashboard',
        icon: <DashboardOutlined />,
        label: '仪表盘',
      },
      {
        key: '/projects',
        icon: <FileTextOutlined />,
        label: '项目管理',
      },
      {
        key: '/phishing',
        icon: <ThunderboltOutlined />,
        label: '钓鱼中台',
      },
      {
        key: '/email-phishing',
        icon: <MailOutlined />,
        label: '邮件钓鱼',
      },
      {
        key: '/website-phishing',
        icon: <CloudServerOutlined />,
        label: '钓鱼网站',
      },
      {
        key: '/phone-control',
        icon: <MobileOutlined />,
        label: '云手机操控',
      },
      {
        key: '/mobile-collect',
        icon: <DatabaseOutlined />,
        label: '手机采集任务',
      },
      {
        key: '/persona-library',
        icon: <TeamOutlined />,
        label: '人设库',
      },
      {
        key: '/ai-tools',
        icon: <RobotOutlined />,
        label: 'AI工具',
      },
      {
        key: '/im-tools',
        icon: <WechatOutlined />,
        label: 'IM工具',
      },
      {
        key: '/infrastructure',
        icon: <CloudServerOutlined />,
        label: '基础设施',
      },
      {
        key: '/capabilities',
        icon: <ApiOutlined />,
        label: '能力复用',
      },
      {
        key: '/skills',
        icon: <ExperimentOutlined />,
        label: 'Skills 技能库',
      },
      {
        key: '/prompts',
        icon: <HighlightOutlined />,
        label: 'Prompts 提示词',
      },
      {
        key: '/docs',
        icon: <BookOutlined />,
        label: '文档中心',
      },
    ]

    // 只有管理员才显示系统管理菜单
    if (currentUser?.permissions?.system_management) {
      items.push({
        key: '/settings',
        icon: <SettingOutlined />,
        label: '系统管理',
        children: [
          {
            key: '/settings/users',
            icon: <UserOutlined />,
            label: '用户管理',
          },
          {
            key: '/settings/cookies',
            icon: <SettingOutlined />,
            label: 'Cookie 管理',
          },
          {
            key: '/settings/config',
            icon: <ApiOutlined />,
            label: '系统配置',
          },
          {
            key: '/agents',
            icon: <RobotOutlined />,
            label: 'Agent 管理',
          },
          {
            key: '/observability',
            icon: <DashboardOutlined />,
            label: '系统观测',
          },
        ],
      })
    }

    return items
  }, [currentUser])

  const handleMenuClick = ({ key }: { key: string }) => {
    navigate(key)
    if (isMobile) setCollapsed(true)
  }

  const handleUserMenuClick: MenuProps['onClick'] = ({ key }) => {
    if (key === 'logout') {
      handleLogout()
      return
    }

    if (key === 'settings') {
      navigate('/settings')
      return
    }
  }

  const handleLogout = () => {
    logout().catch(() => {
      // ignore
    })
    localStorage.removeItem('userInfo')
    navigate('/login')
  }

  const userMenuItems: MenuProps['items'] = [
    {
      key: 'profile',
      icon: <UserOutlined />,
      label: '个人信息',
    },
    {
      key: 'settings',
      icon: <SettingOutlined />,
      label: '账户设置',
    },
    {
      type: 'divider',
    },
    {
      key: 'logout',
      icon: <LogoutOutlined />,
      label: '退出登录',
      onClick: handleLogout,
    },
  ]

  return (
    <Layout className="main-layout">
      <Sider
        trigger={null}
        collapsible
        collapsed={collapsed}
        width={240}
        className={`layout-sider${isMobile ? ' is-mobile' : ''}`}
        breakpoint="lg"
        collapsedWidth={isMobile ? 0 : 72}
      >
        <div className="logo-container">
          <div className="logo-icon">
            <MailOutlined />
          </div>
          {!collapsed && (
            <div className="logo-text">
              <div className="logo-title">Sere1nFish</div>
              <div className="logo-subtitle">AI 钓鱼中台</div>
            </div>
          )}
        </div>
        <Menu
          mode="inline"
          selectedKeys={[location.pathname]}
          items={menuItems}
          onClick={handleMenuClick}
          className="main-menu"
        />
      </Sider>
      {isMobile && !collapsed && (
        <div
          className="sider-backdrop"
          onClick={() => setCollapsed(true)}
          role="presentation"
        />
      )}
      <Layout>
        <Header className="layout-header">
          <div className="header-left">
            <div className="trigger-group">
              <div
                className="trigger"
                onClick={() => setCollapsed(!collapsed)}
                role="button"
                tabIndex={0}
                aria-label={collapsed ? '展开侧边栏' : '收起侧边栏'}
              >
                {collapsed ? <MenuUnfoldOutlined /> : <MenuFoldOutlined />}
              </div>
              <div className="header-copy">
                <Text className="header-title">运营控制台</Text>
                <Text className="header-subtitle">统一查看项目、工具与资源状态</Text>
              </div>
            </div>
          </div>
          <div className="header-right">
            <Tooltip title={theme === 'dark' ? '切换到亮色模式' : '切换到暗色模式'}>
              <div className="theme-switch">
                <Switch
                  checked={theme === 'light'}
                  onChange={toggleTheme}
                  checkedChildren={<BulbFilled />}
                  unCheckedChildren={<BulbOutlined />}
                  className="theme-switch-btn"
                  aria-label="切换主题"
                />
              </div>
            </Tooltip>
            <Divider orientation="vertical" className="header-divider" />
            <Dropdown menu={{ items: userMenuItems, onClick: handleUserMenuClick }} placement="bottomRight">
              <Space className="user-info" role="button" tabIndex={0} aria-label="用户菜单">
                <Avatar icon={<UserOutlined />} className="user-avatar" />
                <span className="username">{currentUser?.username || '用户'}</span>
                {currentUser?.is_admin && (
                  <span className="user-role-tag">管理员</span>
                )}
              </Space>
            </Dropdown>
          </div>
        </Header>
        <Content className="layout-content">
          <Outlet />
        </Content>
      </Layout>
    </Layout>
  )
}
