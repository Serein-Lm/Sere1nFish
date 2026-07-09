import { lazy, Suspense } from 'react'
import { BrowserRouter, Routes, Route, Navigate } from 'react-router-dom'
import ConfigProvider from 'antd/es/config-provider'
import theme from 'antd/es/theme'
import zhCN from 'antd/es/locale/zh_CN'
import ProtectedRoute from './router/ProtectedRoute'
import { ThemeProvider, useTheme } from './contexts/ThemeContext'
import './styles/theme.css'

const Login = lazy(() => import('./components/Login/Login'))
const MainLayout = lazy(() => import('./components/Layout/MainLayout'))
const Dashboard = lazy(() => import('./pages/Dashboard/Dashboard'))
const PhishingPlatform = lazy(() => import('./pages/PhishingPlatform/PhishingPlatform'))
const EmailPhishing = lazy(() => import('./pages/EmailPhishing/EmailPhishing'))
const WebsitePhishing = lazy(() => import('./pages/WebsitePhishing/WebsitePhishing'))
const Observability = lazy(() => import('./pages/Observability/Observability'))
const PhoneControl = lazy(() => import('./pages/PhoneControl/PhoneControl'))
const PersonaLibrary = lazy(() => import('./pages/PersonaLibrary/PersonaLibrary'))
const MobileCollect = lazy(() => import('./pages/MobileCollect/MobileCollect'))
const ProjectManagement = lazy(() => import('./pages/ProjectManagement/ProjectManagement'))
const ProjectDetail = lazy(() => import('./pages/ProjectDetail/ProjectDetail'))
const TaskDetail = lazy(() => import('./pages/TaskDetail/TaskDetail'))
const AITools = lazy(() => import('./pages/AITools/AITools'))
const IMTools = lazy(() => import('./pages/IMTools/IMTools'))
const Infrastructure = lazy(() => import('./pages/Infrastructure/Infrastructure'))
const Capabilities = lazy(() => import('./pages/Capabilities/Capabilities'))
const SkillsManagement = lazy(() => import('./pages/SkillsManagement/SkillsManagement'))
const PromptsManagement = lazy(() => import('./pages/PromptsManagement/PromptsManagement'))
const DocumentCenter = lazy(() => import('./pages/DocumentCenter/DocumentCenter'))
const AgentManagement = lazy(() => import('./pages/AgentManagement/AgentManagement'))
const Settings = lazy(() => import('./pages/Settings/Settings'))
const UserManagement = lazy(() => import('./pages/UserManagement/UserManagement'))
const CookieManagement = lazy(() => import('./pages/CookieManagement/CookieManagement'))
const ConfigManagement = lazy(() => import('./pages/ConfigManagement/ConfigManagement'))

const lightTokens = {
  colorBgContainer: '#ffffff',
  colorBorder: 'rgba(0, 0, 0, 0.06)',
  colorText: 'rgba(0, 0, 0, 0.88)',
  colorTextSecondary: 'rgba(0, 0, 0, 0.65)',
  colorPrimary: '#1677ff',
  colorBgLayout: '#f8f9fa',
}

const darkTokens = {
  colorBgContainer: '#111111',
  colorBorder: 'rgba(255, 255, 255, 0.08)',
  colorText: 'rgba(255, 255, 255, 0.95)',
  colorTextSecondary: 'rgba(255, 255, 255, 0.7)',
  colorPrimary: '#ffffff',
  colorBgLayout: '#0a0a0a',
}

function RouteLoading() {
  return <div className="route-loading" aria-label="页面加载中" />
}

function AppContent() {
  const { theme: currentTheme } = useTheme()

  return (
    <ConfigProvider
      locale={zhCN}
      theme={{
        algorithm: currentTheme === 'dark' ? theme.darkAlgorithm : theme.defaultAlgorithm,
        token: currentTheme === 'dark' ? darkTokens : lightTokens,
      }}
    >
      <BrowserRouter>
        <Suspense fallback={<RouteLoading />}>
          <Routes>
            <Route path="/login" element={<Login />} />
            <Route
              path="/"
              element={
                <ProtectedRoute>
                  <MainLayout />
                </ProtectedRoute>
              }
            >
              <Route index element={<Navigate to="/dashboard" replace />} />
              <Route path="dashboard" element={<Dashboard />} />
              <Route path="phishing" element={<PhishingPlatform />} />
              <Route path="email-phishing" element={<EmailPhishing />} />
              <Route path="website-phishing" element={<WebsitePhishing />} />
              <Route path="phone-control" element={<PhoneControl />} />
              <Route path="persona-library" element={<PersonaLibrary />} />
              <Route path="mobile-collect" element={<MobileCollect />} />
              <Route path="mobile-profiles" element={<Navigate to="/phone-control?tab=profiles" replace />} />
              <Route path="mobile-auto-chat" element={<Navigate to="/phone-control?tab=auto-chat" replace />} />
              <Route path="projects" element={<ProjectManagement />} />
              <Route path="projects/:projectId" element={<ProjectDetail />} />
              <Route path="tasks/:taskId" element={<TaskDetail />} />
              <Route path="projects/:projectId/tasks/:taskId" element={<TaskDetail />} />
              <Route path="ai-tools" element={<AITools />} />
              <Route path="im-tools" element={<IMTools />} />
              <Route path="infrastructure" element={<Infrastructure />} />
              <Route path="skills" element={<SkillsManagement />} />
              <Route path="prompts" element={<PromptsManagement />} />
              <Route path="capabilities" element={<Capabilities />} />
              <Route path="docs" element={<DocumentCenter />} />
              <Route path="agents" element={<AgentManagement />} />
              <Route path="observability" element={<Observability />} />
              <Route path="settings" element={<Settings />} />
              <Route path="settings/users" element={<UserManagement />} />
              <Route path="settings/cookies" element={<CookieManagement />} />
              <Route path="settings/config" element={<ConfigManagement />} />
            </Route>
            <Route path="*" element={<Navigate to="/dashboard" replace />} />
          </Routes>
        </Suspense>
      </BrowserRouter>
    </ConfigProvider>
  )
}

function App() {
  return (
    <ThemeProvider>
      <AppContent />
    </ThemeProvider>
  )
}

export default App
