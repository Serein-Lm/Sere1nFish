/**
 * API 配置文件
 * 集中管理所有 API 端点和配置
 */

/**
 * API 基础配置
 */
export const API_CONFIG = {
  // 默认走 nginx 同源代理；需要直连时用 VITE_API_BASE_URL 覆盖。
  BASE_URL: import.meta.env.VITE_API_BASE_URL || '/api',
  
  // 超时配置（毫秒）
  TIMEOUT: 30000,
  
  // 重试配置
  RETRY_TIMES: 3,
  RETRY_DELAY: 1000,
}

/**
 * API 端点
 */
export const API_ENDPOINTS = {
  // AI 中枢统一 SSE 流式接口
  GRAPH_STREAM: '/v1/agent/stream',
  
  // 工作流列表
  WORKFLOWS: '/v1/agent/workflows',
  
  // 健康检查
  HEALTH_CHECK: '/health',

  // XHS 人物画像
  XHS_PROFILE_STREAM: '/v1/xhs/profile/generate/stream',
  XHS_PROFILES: '/v1/xhs/{projectId}/profiles',

  // 项目下的任务（project-scoped）
  TASKS_CREATE: '/v1/projects/{projectId}/tasks',
  TASKS_UPLOAD: '/v1/projects/{projectId}/tasks/upload',
  TASKS_LIST: '/v1/projects/{projectId}/tasks/list',
  TASKS_DETAIL: '/v1/projects/{projectId}/tasks/{taskId}',
  TASKS_DELETE_BATCH: '/v1/projects/{projectId}/tasks',

  // 项目下的 Findings
  FINDINGS_SUMMARY: '/v1/projects/{projectId}/findings/summary',
  FINDINGS_LIST: '/v1/projects/{projectId}/findings',
  FINDINGS_GENERATE_COPYWRITING: '/v1/findings/{findingId}/generate-copywriting',

  // Finding 详情（按 finding_id）
  FINDING_DETAIL: '/v1/findings/{findingId}',
  FINDING_PROFILE: '/v1/findings/{findingId}/profile',
  FINDING_COPYWRITING: '/v1/findings/{findingId}/copywriting',
  FINDING_NOTES: '/v1/findings/{findingId}/notes',

  // 项目看板 + 聚合
  PROJECT_DASHBOARD: '/v1/projects/{projectId}/dashboard',
  PROJECT_TIMELINE: '/v1/projects/{projectId}/timeline',
  PROJECT_SCORE_DISTRIBUTION: '/v1/projects/{projectId}/score-distribution',
  PROJECT_SOURCE_BREAKDOWN: '/v1/projects/{projectId}/source-breakdown',
  PROJECT_TYPE_BREAKDOWN: '/v1/projects/{projectId}/type-breakdown',
  PROJECT_HIGH_VALUE_TARGETS: '/v1/projects/{projectId}/high-value-targets',
  PROJECT_COPYWRITING_COVERAGE: '/v1/projects/{projectId}/copywriting-coverage',

  // 项目原始数据
  PROJECT_NOTES: '/v1/projects/{projectId}/notes',
  PROJECT_PROFILES: '/v1/projects/{projectId}/profiles',
  PROJECT_WEB_TAGGING: '/v1/projects/{projectId}/web-tagging',

  // Skills
  SKILLS_LIST: '/v1/skills',
  SKILLS_GROUPED: '/v1/skills/grouped',
  SKILLS_STATS: '/v1/skills/stats',
  SKILLS_DETAIL: '/v1/skills/{skillId}',
  SKILLS_CREATE: '/v1/skills',
  SKILLS_UPDATE: '/v1/skills/{skillId}',
  SKILLS_DELETE: '/v1/skills/{skillId}',
  SKILLS_SUBMIT_REVIEW: '/v1/skills/{skillId}/submit-review',
  SKILLS_REVIEW_PENDING: '/v1/skills/review/pending',
  SKILLS_REVIEW: '/v1/skills/{skillId}/review',
  SKILLS_ARCHIVE: '/v1/skills/{skillId}/archive',
  SKILLS_CATEGORIES: '/v1/skills/categories',
  SKILLS_CATEGORIES_TREE: '/v1/skills/categories/tree',
  SKILLS_CATEGORY_DETAIL: '/v1/skills/categories/{categoryId}',
  SKILLS_TAGS: '/v1/skills/tags',
  SKILLS_TAG_DETAIL: '/v1/skills/tags/{tagId}',

  // Prompts
  PROMPTS_LIST: '/v1/prompts',
  PROMPTS_STATS: '/v1/prompts/stats',
  PROMPTS_DETAIL: '/v1/prompts/detail/{promptId}',
  PROMPTS_CREATE: '/v1/prompts',
  PROMPTS_UPDATE: '/v1/prompts/detail/{promptId}',
  PROMPTS_DELETE: '/v1/prompts/detail/{promptId}',
  PROMPTS_SUBMIT_REVIEW: '/v1/prompts/detail/{promptId}/submit-review',
  PROMPTS_REVIEW_PENDING: '/v1/prompts/review/pending',
  PROMPTS_REVIEW: '/v1/prompts/detail/{promptId}/review',
  PROMPTS_ARCHIVE: '/v1/prompts/detail/{promptId}/archive',
  PROMPTS_CATEGORIES: '/v1/prompts/categories',
  PROMPTS_CATEGORY_DETAIL: '/v1/prompts/categories/{categoryId}',
  PROMPTS_TAGS: '/v1/prompts/tags',
  PROMPTS_TAG_DETAIL: '/v1/prompts/tags/{tagId}',

  // 统计
  STATS_GLOBAL: '/v1/stats/global',
  STATS_PROJECT: '/v1/stats/project/{projectId}',
  STATS_TASK: '/v1/stats/task/{taskId}',
  STATS_RECORDS: '/v1/stats/records',
}

/**
 * 获取完整的 API URL
 * @param endpoint API 端点
 * @returns 完整的 URL
 */
export function getApiUrl(endpoint: string): string {
  return `${API_CONFIG.BASE_URL}${endpoint}`
}

/**
 * 请求头配置
 */
export const REQUEST_HEADERS = {
  'Content-Type': 'application/json',
}
