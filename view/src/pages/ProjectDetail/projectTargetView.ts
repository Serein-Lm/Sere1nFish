import type { Project } from '../../services/projectService'
import type { ProjectTargetDashboard } from '../../services/sourceDocumentService'
import type { ProjectTargetTab } from '../../utils/targetRoutes'

export interface ProjectTargetView {
  project: Project
  dashboard: ProjectTargetDashboard
  tab: ProjectTargetTab
  onTabChange: (tab: ProjectTargetTab) => void
}
